"""
text_to_sql.py — Natural language to SQL using Google Gemini 2.5 Flash (free tier).

Public API
----------
ask(question, conn, history)  -> ResultDict
    Convert a question to SQL, validate, execute and return structured result.

ConversationHistory
    Multi-turn context manager with per-session question limit (20).
    Syncs question_count to st.session_state when Streamlit is active.

ResultDict keys
---------------
  sql          : str              — validated SQL that was executed (or attempted)
  result_df    : DataFrame | None — query results (None on error)
  explanation  : str              — natural language summary from the model
  error        : str | None       — error message, None on success
"""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Optional dependencies — fail gracefully at import time
# ---------------------------------------------------------------------------

try:
    from google import genai
    from google.genai import types as genai_types

    _HAS_GEMINI = True
except ImportError:
    _HAS_GEMINI = False
    genai = None
    genai_types = None

try:
    from dotenv import load_dotenv as _load_dotenv
    from pathlib import Path

    _load_dotenv(
        dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=True
    )
except ImportError:
    pass

# Internal imports
from modules.db_setup import _DDL as _SCHEMA_DDL
from modules.query_engine import run_query

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

QUESTION_LIMIT = 20  # max questions per browser session
_MODEL = "gemini-2.5-flash"
_MAX_TOKENS = 2048
_MAX_ROWS = 1000
_MAX_HISTORY_TURNS = 10  # keep last N question/answer pairs in context

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = f"""You are a senior data analyst assistant embedded in Freight Tracker, \
a shipping intelligence platform.
Your sole job is to convert natural language questions into valid SQLite SQL queries \
against the Freight Tracker database, then explain the results in plain English.

DATABASE SCHEMA (SQLite)
{_SCHEMA_DDL.strip()}

TABLE DESCRIPTIONS
VESSEL        10 container ships. teu_capacity = max TEU capacity.
PORT          20 real global ports. latitude/longitude in decimal degrees.
              is_transshipment=1 for hubs (Singapore, Dubai, Hong Kong, Busan, etc.).
ROUTE         10 named trade lanes, each with an origin and destination port.
ROUTE_LEG     Individual segments per route. distance_nm = nautical miles.
VOYAGE        60 voyages over 18 months (2025-01 to 2026-06).
              departure_date / arrival_date are ISO-8601 strings.
VOYAGE_STOP   Port calls within a voyage.
              eta/ata/etd/atd are ISO-8601 strings (ata/atd may be NULL if not yet reached).
              delay_hours >= 0 (capped; early arrivals stored as 0).
CUSTOMER      15 real multinationals (Apple, Samsung, Walmart, Toyota, Amazon...).
SHIPMENT      200 shipments. bl_number is the Bill of Lading (e.g. BL2025000001).
              incoterms: EXW/FCA/FAS/FOB/CFR/CIF/CPT/CIP/DAP/DPU/DDP.
CONTAINER     Types: 20GP 40GP 40HC 20RF 40RF 20OT 40OT.
              is_reefer=1 means refrigerated; temperature_c is set only for reefer.
CARGO_ITEM    HS codes are 6-digit (e.g. 847130 = Laptops). value_usd per item.
SHIPMENT_EVENT Full tracking chain per shipment:
              Booking Confirmed -> Documents Received -> Gate In -> Loaded on Vessel ->
              Vessel Departed -> Port Arrival -> Customs Hold? -> Customs Cleared ->
              Gate Out -> Delivered  (or Cancelled / Exception).

BUSINESS RULES & HELPFUL FORMULAS
• On-time arrival      : delay_hours <= 4
• Severely delayed     : delay_hours > 24
• TEU equivalent       : 20* container types = 1 TEU, 40* types = 2 TEU
• Utilisation %        : booked_teu / teu_capacity * 100
• Voyage status values : Scheduled | Departed | In Transit | Arrived | Completed
• Shipment status      : Booked | Loaded | In Transit | Arrived | Delivered | Cancelled

SQLITE DIALECT NOTES
• Date grouping  : strftime('%Y-%m', departure_date)
• Date diff days : CAST(julianday(arrival_date) - julianday(departure_date) AS INTEGER)
• String contains: column LIKE '%text%'
• Window functions (OVER / PARTITION BY / ROW_NUMBER) are supported (SQLite 3.25+)
• No BOOLEAN type; use INTEGER 0/1
• NULLS LAST is supported in ORDER BY

OUTPUT FORMAT — STRICT
Always reply with EXACTLY this structure and no other text:

<sql>
SELECT ...
</sql>
<explanation>
Plain English: what the query does, what the key numbers mean, any caveats.
Reference previous turns when answering follow-up questions.
</explanation>

CONSTRAINTS:
• Only SELECT statements (WITH ... AS (...) SELECT ... CTEs are allowed).
• Never write INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, PRAGMA, ATTACH.
• Do not include a LIMIT clause; the system will enforce LIMIT {_MAX_ROWS} automatically.
• If the question cannot be answered from the schema, write SELECT 'N/A' AS note
  in the <sql> block and explain why in <explanation>.
• Qualify column names with table aliases whenever joining two or more tables.
"""

# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------


class ConversationHistory:
    """
    Maintains multi-turn message context for the Gemini API.

    question_count is synced to st.session_state['nl_question_count'] when
    Streamlit is active, so the limit survives page re-renders.
    """

    _SS_KEY = "nl_question_count"

    def __init__(self) -> None:
        self._messages: list[dict[str, str]] = []
        self.question_count: int = self._read_session_state()

    # -- session_state helpers --

    @staticmethod
    def _read_session_state() -> int:
        try:
            import streamlit as st

            return int(st.session_state.get(ConversationHistory._SS_KEY, 0))
        except Exception:
            return 0

    @staticmethod
    def _write_session_state(count: int) -> None:
        try:
            import streamlit as st

            st.session_state[ConversationHistory._SS_KEY] = count
        except Exception:
            pass

    # -- message management --

    def add_user(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        self._messages.append({"role": "model", "content": text})

    def get_messages(self) -> list[dict[str, str]]:
        """Return the last MAX_HISTORY_TURNS exchanges (2 messages per turn)."""
        keep = _MAX_HISTORY_TURNS * 2
        return (
            self._messages[-keep:]
            if len(self._messages) > keep
            else list(self._messages)
        )

    def clear(self) -> None:
        self._messages.clear()
        self.question_count = 0
        self._write_session_state(0)

    # -- limit enforcement --

    @property
    def at_limit(self) -> bool:
        return self.question_count >= QUESTION_LIMIT

    @property
    def remaining(self) -> int:
        return max(0, QUESTION_LIMIT - self.question_count)

    def _increment(self) -> None:
        self.question_count += 1
        self._write_session_state(self.question_count)


# ---------------------------------------------------------------------------
# SQL validation helpers
# ---------------------------------------------------------------------------

_BLOCKED_KW = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|EXEC(?:UTE)?"
    r"|ATTACH|DETACH|VACUUM|REINDEX|PRAGMA)\b",
    re.IGNORECASE,
)

_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def _validate_sql(sql: str) -> tuple[bool, str]:
    """
    Return (ok, error_message).
    Accepts SELECT and WITH...SELECT (CTEs). Blocks everything else.
    """
    clean = _strip_sql_comments(sql).strip()

    if not clean:
        return False, "Empty SQL."

    if not re.match(r"\b(SELECT|WITH)\b", clean, re.IGNORECASE):
        return False, "Only SELECT queries (including CTEs) are permitted."

    blocked = _BLOCKED_KW.search(clean)
    if blocked:
        return False, f"Forbidden keyword detected: {blocked.group().upper()}"

    without_trailing = clean.rstrip(";").rstrip()
    if ";" in without_trailing:
        return False, "Multiple SQL statements are not permitted."

    return True, ""


def _enforce_limit(sql: str, max_rows: int = _MAX_ROWS) -> str:
    """Append or clamp LIMIT clause to max_rows."""
    sql = sql.rstrip().rstrip(";")
    match = _LIMIT_RE.search(sql)
    if match:
        existing = int(match.group(1))
        if existing > max_rows:
            sql = _LIMIT_RE.sub(f"LIMIT {max_rows}", sql)
    else:
        sql = f"{sql}\nLIMIT {max_rows}"
    return sql


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_response(text: str) -> tuple[str, str]:
    """
    Extract (sql, explanation) from the model response.
    Tries XML-tag format first, then falls back to markdown code fences.
    """
    sql_match = re.search(r"<sql>(.*?)</sql>", text, re.DOTALL | re.IGNORECASE)
    exp_match = re.search(
        r"<explanation>(.*?)</explanation>", text, re.DOTALL | re.IGNORECASE
    )

    if sql_match:
        sql = sql_match.group(1).strip()
        sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\s*```$", "", sql)
        explanation = exp_match.group(1).strip() if exp_match else ""
        return sql, explanation

    fence_match = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        sql = fence_match.group(1).strip()
        explanation = re.sub(r"```(?:sql)?.*?```", "", text, flags=re.DOTALL).strip()
        return sql, explanation

    bare = re.search(r"\b((?:WITH|SELECT)\b.*)", text, re.DOTALL | re.IGNORECASE)
    if bare:
        return bare.group(1).strip(), ""

    return "", text.strip()


# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------


def _get_client():
    if not _HAS_GEMINI:
        raise RuntimeError(
            "The 'google-genai' package is not installed. "
            "Run: pip install google-genai"
        )
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. "
            "Add it to your .env file or set it as an environment variable."
        )
    return genai.Client(api_key=api_key)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ResultDict = dict[str, Any]


def ask(
    question: str,
    conn: sqlite3.Connection,
    history: ConversationHistory | None = None,
) -> ResultDict:
    """
    Convert *question* to SQL, validate, execute and return a ResultDict.

    Parameters
    ----------
    question : str
        Natural language question about the Freight Tracker data.
    conn : sqlite3.Connection
        Active database connection (row_factory=Row recommended).
    history : ConversationHistory | None
        Pass a ConversationHistory instance to enable multi-turn dialogue.

    Returns
    -------
    dict with keys:
        sql          : str
        result_df    : pd.DataFrame | None
        explanation  : str
        error        : str | None
    """
    if history is None:
        history = ConversationHistory()

    _empty: ResultDict = {
        "sql": "",
        "result_df": None,
        "explanation": "",
        "error": None,
    }

    # -- question limit --
    if history.at_limit:
        return {
            **_empty,
            "error": (
                f"Session question limit of {QUESTION_LIMIT} reached. "
                "Reload the page to start a new session."
            ),
        }

    # -- API client --
    try:
        client = _get_client()
    except RuntimeError as exc:
        return {**_empty, "error": str(exc)}

    # -- build Gemini contents --
    history.add_user(question)
    messages = history.get_messages()

    contents = []
    for msg in messages:
        role = msg["role"]  # "user" or "model"
        contents.append(
            genai_types.Content(
                role=role, parts=[genai_types.Part(text=msg["content"])]
            )
        )

    # -- call the model --
    try:
        response = client.models.generate_content(
            model=_MODEL,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                max_output_tokens=_MAX_TOKENS,
            ),
        )
        raw_text: str = response.text
    except Exception as exc:
        history._messages.pop()
        return {**_empty, "error": f"API error: {exc}"}

    # -- parse response --
    sql_raw, explanation = _parse_response(raw_text)

    if not sql_raw:
        history.add_assistant(raw_text)
        history._increment()
        return {
            **_empty,
            "explanation": explanation or raw_text,
            "error": "Model did not return a SQL query.",
        }

    # -- validate --
    ok, val_error = _validate_sql(sql_raw)
    if not ok:
        history.add_assistant(raw_text)
        history._increment()
        return {
            **_empty,
            "sql": sql_raw,
            "explanation": explanation,
            "error": f"SQL validation failed: {val_error}",
        }

    # -- enforce row limit --
    sql_final = _enforce_limit(sql_raw)

    # -- execute --
    try:
        result_df: pd.DataFrame = run_query(conn, sql_final)
    except Exception as exc:
        history.add_assistant(raw_text)
        history._increment()
        return {
            **_empty,
            "sql": sql_final,
            "explanation": explanation,
            "error": f"Query execution failed: {exc}",
        }

    # -- success --
    history.add_assistant(raw_text)
    history._increment()

    return {
        "sql": sql_final,
        "result_df": result_df,
        "explanation": explanation,
        "error": None,
    }


def ask_raw(
    question: str,
    history: ConversationHistory | None = None,
) -> tuple[str, str]:
    """
    Call the model and return (sql, explanation) without executing the query.
    Useful for previewing SQL before running it.
    """
    if history is None:
        history = ConversationHistory()

    client = _get_client()
    history.add_user(question)
    messages = history.get_messages()

    contents = []
    for msg in messages:
        contents.append(
            genai_types.Content(
                role=msg["role"], parts=[genai_types.Part(text=msg["content"])]
            )
        )

    response = client.models.generate_content(
        model=_MODEL,
        contents=contents,
        config=genai_types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            max_output_tokens=_MAX_TOKENS,
        ),
    )
    raw_text = response.text
    history.add_assistant(raw_text)
    history._increment()

    return _parse_response(raw_text)
