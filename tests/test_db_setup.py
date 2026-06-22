"""Tests for modules/db_setup.py — init_db() on in-memory and file-based DBs."""

import sqlite3
import pytest
from modules.db_setup import init_db

TABLES = [
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
]


@pytest.fixture(scope="module")
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def test_all_tables_exist(conn):
    """All 11 schema tables are present in sqlite_master."""
    found = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    missing = set(TABLES) - found
    assert not missing, f"Missing tables: {missing}"


@pytest.mark.parametrize("table", TABLES)
def test_row_count_positive(conn, table):
    """Every table contains at least one seeded row."""
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    assert n > 0, f"{table} is empty after initialisation"


def test_fk_integrity(conn):
    """PRAGMA foreign_key_check reports no referential-integrity violations."""
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert not violations, (
        f"{len(violations)} FK violation(s) found; "
        f"first: table={violations[0][0]!r} rowid={violations[0][1]} "
        f"parent={violations[0][2]!r}"
    )


def test_idempotency(tmp_path):
    """Calling init_db() twice on the same file DB leaves every row count unchanged."""
    db_file = tmp_path / "freight.db"

    c1 = init_db(db_file)
    before = {t: c1.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    c1.close()

    c2 = init_db(db_file)
    after = {t: c2.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    c2.close()

    changed = {t for t in TABLES if before[t] != after[t]}
    assert not changed, f"Row counts changed on second init: " + ", ".join(
        f"{t}: {before[t]}->{after[t]}" for t in changed
    )


def test_row_factory_is_sqlite_row(conn):
    """row_factory is sqlite3.Row; columns are accessible by name and by index."""
    row = conn.execute("SELECT vessel_name, vessel_type FROM VESSEL LIMIT 1").fetchone()
    assert isinstance(row, sqlite3.Row)
    assert row["vessel_name"] == row[0]
    assert row["vessel_type"] in ("Container", "Bulk Carrier", "Tanker", "RoRo")
