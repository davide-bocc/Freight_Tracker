"""
validator.py — SQL validation for Freight Tracker text-to-SQL pipeline.

Public API
----------
validate(sql, conn) -> ValidationResult

Checks (in order)
-----------------
1.  Non-empty input
2.  No comment syntax (-- or /* */)                   SECURITY
3.  No multiple statements (semicolon injection)       SECURITY
4.  No forbidden write/admin keywords                  SECURITY
5.  Must begin with SELECT or WITH (CTE)               SECURITY
6.  LIMIT injection / clamping to MAX_ROWS             SAFETY
7.  All referenced tables exist in schema              SYNTAX
8.  SQLite EXPLAIN syntax + query-plan check           SYNTAX

ValidationResult keys
---------------------
  is_valid      bool       — True only when errors list is empty
  sanitized_sql str        — LIMIT-capped SQL ready to execute;
                             empty string when is_valid is False
  errors        list[str]  — blocking issues (ordered by check)
  warnings      list[str]  — non-blocking notes (LIMIT changes, etc.)
"""

from __future__ import annotations

import re
import sqlite3
from typing import TypedDict

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

MAX_ROWS = 1000

KNOWN_TABLES: frozenset[str] = frozenset(
    {
        "VESSEL",
        "PORT",
        "ROUTE",
        "ROUTE_LEG",
        "VOYAGE",
        "VOYAGE_STOP",
        "CUSTOMER",
        "SHIPMENT",
        "CONTAINER",
        "CARGO_ITEM",
        "SHIPMENT_EVENT",
    }
)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class ValidationResult(TypedDict):
    is_valid: bool
    sanitized_sql: str
    errors: list[str]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# Write/admin keywords that must never appear in read-only queries
_BLOCKED_KW_RE = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE"
    r"|EXEC(?:UTE)?"
    r"|ATTACH|DETACH|VACUUM|REINDEX|PRAGMA"
    r")\b",
    re.IGNORECASE,
)

# LIMIT clause with its integer value
_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)

# Table or view name immediately following FROM or any JOIN keyword.
# Stops at the first non-identifier character (alias, ON, WHERE, …).
_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

# CTE alias defined with:  <name> AS (
_CTE_NAME_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# String-aware helpers
# ---------------------------------------------------------------------------


def _semicolons_outside_strings(sql: str) -> list[int]:
    """
    Return the positions of semicolons that lie outside single- and
    double-quoted string literals.  Handles SQL-escaped quotes ('').
    """
    positions: list[int] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double:
            if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                i += 2  # escaped quote '' — skip both chars
                continue
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ";" and not in_single and not in_double:
            positions.append(i)
        i += 1
    return positions


def _extract_cte_names(sql: str) -> set[str]:
    """Return uppercased CTE alias names defined in WITH … AS (…) clauses."""
    return {m.upper() for m in _CTE_NAME_RE.findall(sql)}


def _extract_table_refs(sql: str) -> set[str]:
    """Return uppercased table names referenced in FROM / JOIN clauses."""
    return {m.upper() for m in _TABLE_REF_RE.findall(sql)}


# ---------------------------------------------------------------------------
# Individual validation steps (each appends to errors / warnings)
# ---------------------------------------------------------------------------


def _check_empty(sql: str, errors: list[str]) -> bool:
    if not sql or not sql.strip():
        errors.append("SQL string is empty.")
        return False
    return True


def _check_comments(sql: str, errors: list[str]) -> None:
    """Reject any SQL that contains comment syntax, even inside strings.

    Model-generated SQL should never include comments. Allowing them would
    require stripping them first, which can be bypassed (e.g. nested comments).
    """
    if "--" in sql:
        errors.append(
            "Line comments ('--') are not permitted. "
            "The query must contain no comment syntax."
        )
    if "/*" in sql or "*/" in sql:
        errors.append(
            "Block comments ('/* … */') are not permitted. "
            "The query must contain no comment syntax."
        )


def _check_multiple_statements(sql: str, errors: list[str]) -> None:
    """Reject SQL with more than one statement (semicolon-injection guard)."""
    # Strip exactly one trailing semicolon before checking
    candidate = sql.rstrip()
    if candidate.endswith(";"):
        candidate = candidate[:-1].rstrip()
    positions = _semicolons_outside_strings(candidate)
    if positions:
        errors.append(
            "Multiple SQL statements are not permitted "
            "(semicolon detected outside a string literal). "
            "Send one SELECT statement at a time."
        )


def _check_blocked_keywords(sql: str, errors: list[str]) -> None:
    """Reject SQL containing data-mutation or administration keywords."""
    match = _BLOCKED_KW_RE.search(sql)
    if match:
        errors.append(
            f"Forbidden keyword '{match.group().upper()}' is not permitted. "
            "Only read-only SELECT queries are accepted."
        )


def _check_select_only(sql: str, errors: list[str]) -> None:
    """Require the first token to be SELECT or WITH (CTE prefix)."""
    tokens = sql.split()
    first = tokens[0].upper() if tokens else ""
    if first not in ("SELECT", "WITH"):
        errors.append(
            f"Query must begin with SELECT or WITH, got '{first}'. "
            "Only read-only SELECT queries are accepted."
        )


def _inject_limit(sql: str, warnings: list[str]) -> str:
    """
    Ensure a LIMIT clause exists and is no greater than MAX_ROWS.
    Strips any trailing semicolon as a side effect.
    """
    sql = sql.rstrip().rstrip(";").rstrip()
    match = _LIMIT_RE.search(sql)
    if match:
        existing = int(match.group(1))
        if existing > MAX_ROWS:
            sql = _LIMIT_RE.sub(f"LIMIT {MAX_ROWS}", sql)
            warnings.append(
                f"LIMIT clamped from {existing:,} to {MAX_ROWS:,} "
                "(maximum rows allowed per query)."
            )
        # else: LIMIT already within bounds — no change, no warning
    else:
        sql = f"{sql}\nLIMIT {MAX_ROWS}"
        warnings.append(
            f"LIMIT {MAX_ROWS:,} appended automatically " "(no LIMIT clause found)."
        )
    return sql


def _check_table_references(sql: str, errors: list[str]) -> None:
    """
    Validate that every table name in FROM / JOIN clauses is either a
    known Freight Tracker table or a CTE alias defined within the query.
    """
    cte_aliases = _extract_cte_names(sql)
    referenced = _extract_table_refs(sql)
    unknown = referenced - KNOWN_TABLES - cte_aliases
    for tbl in sorted(unknown):
        errors.append(
            f"Unknown table referenced: '{tbl}'. "
            f"Valid tables: {', '.join(sorted(KNOWN_TABLES))}."
        )


def _check_syntax(sql: str, conn: sqlite3.Connection, errors: list[str]) -> None:
    """
    Run SQLite's EXPLAIN on the query to catch syntax errors, unknown
    column names, and query-planning failures before execution.

    EXPLAIN does a full parse and plan but writes no data.
    """
    try:
        conn.execute(f"EXPLAIN {sql}")
    except sqlite3.OperationalError as exc:
        errors.append(f"SQL syntax / planning error: {exc}")
    except sqlite3.DatabaseError as exc:
        errors.append(f"Database error during syntax check: {exc}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate(sql: str, conn: sqlite3.Connection) -> ValidationResult:
    """
    Validate *sql* for security, row-limit safety, schema correctness,
    and SQLite syntax before execution.

    Parameters
    ----------
    sql  : Raw SQL string, typically produced by text_to_sql.ask().
    conn : Active sqlite3.Connection to the Freight Tracker database.

    Returns
    -------
    ValidationResult dict:
        is_valid      — True only when errors is empty
        sanitized_sql — LIMIT-capped query ready to pass to run_query();
                        empty string when is_valid is False
        errors        — list of blocking error strings (ordered by check)
        warnings      — list of non-blocking note strings
    """
    errors: list[str] = []
    warnings: list[str] = []

    # --- 1. Non-empty ---
    if not _check_empty(sql, errors):
        return _result(False, "", errors, warnings)

    working = sql.strip()

    # --- 2-5. Security gates (all run; collect all messages) ---
    _check_comments(working, errors)
    _check_multiple_statements(working, errors)
    _check_blocked_keywords(working, errors)
    _check_select_only(working, errors)

    # Abort before touching the SQL if any security check failed.
    # Never return a partially-sanitized query that failed a security gate.
    if errors:
        return _result(False, "", errors, warnings)

    # --- 6. Normalise + inject / clamp LIMIT ---
    working = _inject_limit(working, warnings)

    # --- 7. Schema: table existence ---
    _check_table_references(working, errors)

    # --- 8. Syntax + planning via EXPLAIN ---
    #  Run even when table errors exist; EXPLAIN may surface additional issues.
    _check_syntax(working, conn, errors)

    is_valid = len(errors) == 0
    return _result(is_valid, working if is_valid else "", errors, warnings)


def _result(
    is_valid: bool,
    sanitized_sql: str,
    errors: list[str],
    warnings: list[str],
) -> ValidationResult:
    return {
        "is_valid": is_valid,
        "sanitized_sql": sanitized_sql,
        "errors": errors,
        "warnings": warnings,
    }
