"""
Tests for modules/validator.py and modules/text_to_sql.py.
Covers SQL security validation, conversation history lifecycle and the ask() function with mocked API.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from modules.db_setup import init_db
from modules.validator import MAX_ROWS, validate
from modules.text_to_sql import (
    QUESTION_LIMIT,
    ConversationHistory,
    _MAX_HISTORY_TURNS,
    ask,
)

# Shared fixture


@pytest.fixture(scope="module")
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


# Helper — build a mock Anthropic client from a raw response string

def _mock_client(response_text: str) -> MagicMock:
    content = MagicMock()
    content.text = response_text
    response = MagicMock()
    response.content = [content]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


# validator.validate() — security blocks

class TestValidatorBlocked:

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP TABLE VESSEL",
            "INSERT INTO VESSEL (vessel_name) VALUES ('X')",
            "UPDATE VESSEL SET vessel_name = 'X'",
            "DELETE FROM VESSEL WHERE 1=1",
            "ALTER TABLE VESSEL ADD COLUMN foo TEXT",
            "CREATE TABLE evil (id INTEGER)",
            "TRUNCATE TABLE VESSEL",
            "PRAGMA foreign_keys = OFF",
            "EXEC('DROP TABLE VESSEL')",
            "ATTACH DATABASE 'evil.db' AS evil",
        ],
    )
    def test_forbidden_keyword_rejected(self, conn, sql):
        result = validate(sql, conn)
        assert not result["is_valid"]
        assert result["sanitized_sql"] == ""
        assert result["errors"]

    def test_line_comment_rejected(self, conn):
        result = validate("SELECT vessel_id FROM VESSEL -- strip me", conn)
        assert not result["is_valid"]
        assert any("comment" in e.lower() for e in result["errors"])

    def test_block_comment_rejected(self, conn):
        result = validate("SELECT /* evil */ vessel_id FROM VESSEL", conn)
        assert not result["is_valid"]
        assert any("comment" in e.lower() for e in result["errors"])

    def test_semicolon_injection_rejected(self, conn):
        result = validate("SELECT 1; DROP TABLE VESSEL", conn)
        assert not result["is_valid"]
        assert any(
            "semicolon" in e.lower() or "statement" in e.lower()
            for e in result["errors"]
        )

    def test_non_select_first_token_rejected(self, conn):
        result = validate("SHOW TABLES", conn)
        assert not result["is_valid"]
        assert any("SELECT or WITH" in e for e in result["errors"])

    def test_empty_string_rejected(self, conn):
        result = validate("", conn)
        assert not result["is_valid"]
        assert result["errors"]

    def test_whitespace_only_rejected(self, conn):
        result = validate("   \n\t  ", conn)
        assert not result["is_valid"]
        assert result["errors"]


# validator.validate() — SELECT allowed

class TestValidatorAllowed:

    def test_simple_select_passes(self, conn):
        result = validate("SELECT vessel_id, vessel_name FROM VESSEL", conn)
        assert result["is_valid"]
        assert result["sanitized_sql"] != ""
        assert result["errors"] == []

    def test_cte_select_passes(self, conn):
        sql = "WITH v AS (SELECT vessel_id FROM VESSEL) SELECT * FROM v"
        result = validate(sql, conn)
        assert result["is_valid"]
        assert result["errors"] == []

    def test_multi_table_join_passes(self, conn):
        sql = (
            "SELECT v.vessel_name, vo.voyage_number "
            "FROM VESSEL v "
            "JOIN VOYAGE vo ON vo.vessel_id = v.vessel_id"
        )
        result = validate(sql, conn)
        assert result["is_valid"]

    def test_aggregate_query_passes(self, conn):
        result = validate("SELECT COUNT(*) AS n FROM SHIPMENT", conn)
        assert result["is_valid"]

    def test_window_function_passes(self, conn):
        sql = (
            "SELECT vessel_name, "
            "RANK() OVER (ORDER BY gross_tonnage DESC) AS rk "
            "FROM VESSEL"
        )
        result = validate(sql, conn)
        assert result["is_valid"]

    def test_sanitized_sql_is_string_on_success(self, conn):
        result = validate("SELECT 1 FROM VESSEL", conn)
        assert isinstance(result["sanitized_sql"], str)

    def test_warnings_is_list_on_success(self, conn):
        result = validate("SELECT vessel_id FROM VESSEL", conn)
        assert isinstance(result["warnings"], list)


# validator.validate() — LIMIT injection

class TestValidatorLimit:

    def test_no_limit_gets_appended(self, conn):
        result = validate("SELECT vessel_id FROM VESSEL", conn)
        assert result["is_valid"]
        assert f"LIMIT {MAX_ROWS}" in result["sanitized_sql"]
        assert any("appended" in w.lower() for w in result["warnings"])

    def test_limit_within_bounds_kept(self, conn):
        result = validate("SELECT vessel_id FROM VESSEL LIMIT 5", conn)
        assert result["is_valid"]
        assert "LIMIT 5" in result["sanitized_sql"]
        assert not any("clamped" in w.lower() for w in result["warnings"])

    def test_limit_too_high_clamped(self, conn):
        result = validate(f"SELECT vessel_id FROM VESSEL LIMIT {MAX_ROWS + 9000}", conn)
        assert result["is_valid"]
        assert f"LIMIT {MAX_ROWS}" in result["sanitized_sql"]
        assert any("clamped" in w.lower() for w in result["warnings"])

    def test_trailing_semicolon_stripped(self, conn):
        result = validate("SELECT vessel_id FROM VESSEL;", conn)
        assert result["is_valid"]
        assert not result["sanitized_sql"].rstrip().endswith(";")


# validator.validate() — syntax error detection

class TestValidatorSyntax:

    def test_incomplete_where_is_invalid(self, conn):
        # Trailing WHERE with no condition is a SQLite parse error
        result = validate("SELECT * FROM VESSEL WHERE", conn)
        assert not result["is_valid"]
        assert any(
            "syntax" in e.lower() or "error" in e.lower() for e in result["errors"]
        )

    def test_unknown_table_rejected(self, conn):
        result = validate("SELECT * FROM GHOST_TABLE", conn)
        assert not result["is_valid"]
        assert any("GHOST_TABLE" in e or "Unknown table" in e for e in result["errors"])

    def test_valid_query_produces_no_errors(self, conn):
        result = validate("SELECT port_name FROM PORT", conn)
        assert result["is_valid"]
        assert result["errors"] == []



# ConversationHistory

class TestConversationHistory:

    def test_initial_state(self):
        h = ConversationHistory()
        assert h.question_count == 0
        assert h.get_messages() == []
        assert not h.at_limit

    def test_add_user_message(self):
        h = ConversationHistory()
        h.add_user("How many vessels?")
        msgs = h.get_messages()
        assert len(msgs) == 1
        assert msgs[0] == {"role": "user", "content": "How many vessels?"}

    def test_add_assistant_message(self):
        h = ConversationHistory()
        h.add_assistant("There are 10 vessels.")
        msgs = h.get_messages()
        assert len(msgs) == 1
        assert msgs[0] == {"role": "assistant", "content": "There are 10 vessels."}

    def test_add_alternating_roles_preserved(self):
        h = ConversationHistory()
        h.add_user("Q1")
        h.add_assistant("A1")
        h.add_user("Q2")
        roles = [m["role"] for m in h.get_messages()]
        assert roles == ["user", "assistant", "user"]

    def test_get_messages_returns_independent_copy(self):
        h = ConversationHistory()
        h.add_user("original")
        snapshot = h.get_messages()
        snapshot.clear()
        assert len(h.get_messages()) == 1  # internal list unchanged

    def test_get_messages_trims_to_max_history_window(self):
        h = ConversationHistory()
        # Add one more turn than the window allows
        total_pairs = _MAX_HISTORY_TURNS + 3
        for i in range(total_pairs):
            h.add_user(f"Q{i}")
            h.add_assistant(f"A{i}")
        msgs = h.get_messages()
        assert len(msgs) == _MAX_HISTORY_TURNS * 2

    def test_get_messages_keeps_most_recent_turns(self):
        h = ConversationHistory()
        total_pairs = _MAX_HISTORY_TURNS + 3
        for i in range(total_pairs):
            h.add_user(f"Q{i}")
            h.add_assistant(f"A{i}")
        msgs = h.get_messages()
        assert msgs[-1]["content"] == f"A{total_pairs - 1}"
        assert msgs[0]["content"] == f"Q{total_pairs - 1 - (_MAX_HISTORY_TURNS - 1)}"

    def test_get_messages_within_window_returns_all(self):
        h = ConversationHistory()
        for i in range(3):
            h.add_user(f"Q{i}")
            h.add_assistant(f"A{i}")
        assert len(h.get_messages()) == 6

    def test_clear_empties_messages(self):
        h = ConversationHistory()
        h.add_user("Q")
        h.add_assistant("A")
        h.clear()
        assert h.get_messages() == []

    def test_clear_resets_question_count(self):
        h = ConversationHistory()
        h.question_count = 7
        h.clear()
        assert h.question_count == 0

    def test_at_limit_false_when_below(self):
        h = ConversationHistory()
        h.question_count = QUESTION_LIMIT - 1
        assert not h.at_limit

    def test_at_limit_true_exactly_at_limit(self):
        h = ConversationHistory()
        h.question_count = QUESTION_LIMIT
        assert h.at_limit

    def test_at_limit_true_above_limit(self):
        h = ConversationHistory()
        h.question_count = QUESTION_LIMIT + 5
        assert h.at_limit

    def test_remaining_at_start(self):
        h = ConversationHistory()
        assert h.remaining == QUESTION_LIMIT

    def test_remaining_decreases_with_count(self):
        h = ConversationHistory()
        h.question_count = 7
        assert h.remaining == QUESTION_LIMIT - 7

    def test_remaining_floors_at_zero(self):
        h = ConversationHistory()
        h.question_count = QUESTION_LIMIT + 10
        assert h.remaining == 0

    def test_increment_increases_count(self):
        h = ConversationHistory()
        h._increment()
        h._increment()
        assert h.question_count == 2



# ask() — Anthropic client mocked

_GOOD_RESPONSE = (
    "<sql>SELECT vessel_name, vessel_type FROM VESSEL</sql>"
    "<explanation>Returns the name and type of every vessel.</explanation>"
)

_PATCH = "modules.text_to_sql._get_client"


def _reset_st_count() -> None:
    """Zero out the shared Streamlit session_state counter between tests."""
    try:
        import streamlit as st

        st.session_state["nl_question_count"] = 0
    except Exception:
        pass


class TestAsk:

    @pytest.fixture(autouse=True)
    def _isolate_session_count(self):
        """Reset the Streamlit session counter before every TestAsk test."""
        _reset_st_count()
        yield
        _reset_st_count()

    def test_result_dict_has_required_keys(self, conn):
        with patch(_PATCH, return_value=_mock_client(_GOOD_RESPONSE)):
            result = ask("List all vessels", conn)
        assert set(result.keys()) == {"sql", "result_df", "explanation", "error"}

    def test_success_error_is_none(self, conn):
        with patch(_PATCH, return_value=_mock_client(_GOOD_RESPONSE)):
            result = ask("List all vessels", conn)
        assert result["error"] is None

    def test_success_result_df_is_dataframe(self, conn):
        with patch(_PATCH, return_value=_mock_client(_GOOD_RESPONSE)):
            result = ask("List all vessels", conn)
        assert isinstance(result["result_df"], pd.DataFrame)
        assert len(result["result_df"]) > 0

    def test_success_sql_has_limit_appended(self, conn):
        with patch(_PATCH, return_value=_mock_client(_GOOD_RESPONSE)):
            result = ask("List all vessels", conn)
        assert "LIMIT" in result["sql"].upper()

    def test_success_explanation_extracted(self, conn):
        with patch(_PATCH, return_value=_mock_client(_GOOD_RESPONSE)):
            result = ask("List all vessels", conn)
        assert result["explanation"] == "Returns the name and type of every vessel."

    def test_success_increments_question_count(self, conn):
        history = ConversationHistory()
        with patch(_PATCH, return_value=_mock_client(_GOOD_RESPONSE)):
            ask("List all vessels", conn, history)
        assert history.question_count == 1

    def test_success_adds_user_and_assistant_to_history(self, conn):
        history = ConversationHistory()
        with patch(_PATCH, return_value=_mock_client(_GOOD_RESPONSE)):
            ask("List all vessels", conn, history)
        msgs = history.get_messages()
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_at_limit_returns_error_no_api_call(self, conn):
        history = ConversationHistory()
        history.question_count = QUESTION_LIMIT
        client = _mock_client(_GOOD_RESPONSE)
        with patch(_PATCH, return_value=client):
            result = ask("Anything", conn, history)
        assert result["error"] is not None
        assert "limit" in result["error"].lower()
        client.messages.create.assert_not_called()

    def test_at_limit_result_df_is_none(self, conn):
        history = ConversationHistory()
        history.question_count = QUESTION_LIMIT
        with patch(_PATCH, return_value=_mock_client(_GOOD_RESPONSE)):
            result = ask("Anything", conn, history)
        assert result["result_df"] is None

    def test_api_error_returns_error_string(self, conn):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("Connection timeout")
        with patch(_PATCH, return_value=client):
            result = ask("List vessels", conn)
        assert result["error"] is not None
        assert "API error" in result["error"]

    def test_api_error_result_df_is_none(self, conn):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("timeout")
        with patch(_PATCH, return_value=client):
            result = ask("List vessels", conn)
        assert result["result_df"] is None

    def test_api_error_does_not_increment_count(self, conn):
        history = ConversationHistory()
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("timeout")
        with patch(_PATCH, return_value=client):
            ask("List vessels", conn, history)
        assert history.question_count == 0

    def test_model_returns_no_sql_reports_error(self, conn):
        # Response has no SELECT/WITH anywhere — _parse_response returns empty sql_raw
        no_sql = "I am sorry, I cannot answer that from the available data."
        with patch(_PATCH, return_value=_mock_client(no_sql)):
            result = ask("Who is the CEO?", conn)
        assert result["error"] is not None
        assert "SQL" in result["error"]
        assert result["result_df"] is None

    def test_forbidden_sql_from_model_caught(self, conn):
        drop_resp = (
            "<sql>DROP TABLE VESSEL</sql>"
            "<explanation>Dropping the vessel table.</explanation>"
        )
        with patch(_PATCH, return_value=_mock_client(drop_resp)):
            result = ask("Delete all vessels", conn)
        assert result["error"] is not None
        assert "validation" in result["error"].lower()
        assert result["result_df"] is None
