"""Tests for modules/query_engine.py — all public functions against the seeded in-memory DB."""

import pandas as pd
import pytest
from modules.db_setup import init_db
from modules.query_engine import (
    run_query,
    fleet,
    network,
    operations,
    commercial,
    get_all_kpis,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


@pytest.fixture(scope="module")
def kpis(conn):
    return get_all_kpis(conn)


# ---------------------------------------------------------------------------
# Expected columns per named query (set — order-independent subset check)
# ---------------------------------------------------------------------------

EXPECTED_COLUMNS: dict[str, set[str]] = {
    "vessel_utilization": {
        "vessel_name",
        "operator",
        "teu_capacity",
        "year_built",
        "total_voyages",
        "completed",
        "active",
        "scheduled",
        "arrived",
    },
    "capacity_vs_booked_teu": {
        "vessel_name",
        "operator",
        "teu_capacity",
        "voyage_number",
        "status",
        "container_count",
        "booked_teu",
        "utilization_pct",
    },
    "busiest_ports": {
        "port_name",
        "un_locode",
        "country_code",
        "is_transshipment",
        "total_calls",
        "distinct_voyages",
        "outbound_shipments",
        "inbound_shipments",
        "avg_delay_h",
    },
    "avg_delay_by_route": {
        "route_name",
        "service_name",
        "scheduled_transit_days",
        "voyage_count",
        "total_stops",
        "avg_delay_h",
        "max_delay_h",
        "stops_over_24h",
        "otp_pct",
    },
    "leg_distances": {
        "route_name",
        "service_name",
        "leg_sequence",
        "from_port",
        "from_locode",
        "from_country",
        "to_port",
        "to_locode",
        "to_country",
        "distance_nm",
        "typical_days",
        "avg_speed_nm_per_day",
    },
    "on_time_performance": {
        "total_stops",
        "completed_stops",
        "pending_stops",
        "on_time",
        "delayed",
        "severely_delayed",
        "otp_pct",
        "avg_delay_h",
        "max_delay_h",
    },
    "delayed_voyages": {
        "voyage_number",
        "status",
        "vessel_name",
        "operator",
        "route_name",
        "departure_date",
        "arrival_date",
        "stop_count",
        "total_delay_h",
        "avg_delay_h",
        "max_stop_delay_h",
        "stops_over_24h",
    },
    "avg_delay_by_port": {
        "port_name",
        "un_locode",
        "country_code",
        "latitude",
        "longitude",
        "is_transshipment",
        "total_calls",
        "arrived_calls",
        "avg_delay_h",
        "max_delay_h",
        "delayed_arrivals",
        "severe_delays",
        "otp_pct",
    },
    "top_customers_by_value": {
        "company_name",
        "country_code",
        "industry",
        "shipment_count",
        "total_value_usd",
        "avg_shipment_value_usd",
        "total_weight_kg",
        "voyages_used",
        "status_variety",
    },
    "revenue_by_trade_lane": {
        "route_name",
        "service_name",
        "origin_port",
        "dest_port",
        "shipment_count",
        "customer_count",
        "total_value_usd",
        "avg_value_usd",
        "total_weight_kg",
        "avg_weight_kg",
    },
    "shipment_status_breakdown": {
        "status",
        "shipment_count",
        "pct_of_total",
        "total_value_usd",
        "avg_value_usd",
        "total_weight_kg",
        "avg_weight_kg",
    },
    "reefer_by_type": {
        "container_type",
        "total_containers",
        "reefer_count",
        "dry_count",
        "reefer_pct",
        "avg_temp_c",
        "min_temp_c",
        "max_temp_c",
    },
    "reefer_summary": {
        "total_containers",
        "reefer_containers",
        "dry_containers",
        "reefer_pct",
        "avg_reefer_temp_c",
        "min_temp_c",
        "max_temp_c",
    },
}

ALL_QUERY_NAMES = list(EXPECTED_COLUMNS)

# ---------------------------------------------------------------------------
# run_query
# ---------------------------------------------------------------------------


def test_run_query_returns_dataframe(conn):
    df = run_query(conn, "SELECT vessel_id, vessel_name FROM VESSEL")
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["vessel_id", "vessel_name"]
    assert len(df) > 0


def test_run_query_with_params(conn):
    df = run_query(conn, "SELECT * FROM VESSEL WHERE vessel_type = ?", ("Container",))
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    assert (df["vessel_type"] == "Container").all()


def test_run_query_empty_result_is_dataframe(conn):
    df = run_query(conn, "SELECT * FROM VESSEL WHERE 1 = 0")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# Category functions — dict shape and value types
# ---------------------------------------------------------------------------


def test_fleet_keys_and_types(conn):
    result = fleet(conn)
    assert set(result) == {"vessel_utilization", "capacity_vs_booked_teu"}
    assert all(isinstance(v, pd.DataFrame) for v in result.values())


def test_network_keys_and_types(conn):
    result = network(conn)
    assert set(result) == {"busiest_ports", "avg_delay_by_route", "leg_distances"}
    assert all(isinstance(v, pd.DataFrame) for v in result.values())


def test_operations_keys_and_types(conn):
    result = operations(conn)
    assert set(result) == {
        "on_time_performance",
        "delayed_voyages",
        "avg_delay_by_port",
    }
    assert all(isinstance(v, pd.DataFrame) for v in result.values())


def test_commercial_keys_and_types(conn):
    result = commercial(conn)
    assert set(result) == {
        "top_customers_by_value",
        "revenue_by_trade_lane",
        "shipment_status_breakdown",
        "reefer_by_type",
        "reefer_summary",
    }
    assert all(isinstance(v, pd.DataFrame) for v in result.values())


# ---------------------------------------------------------------------------
# get_all_kpis — dict shape
# ---------------------------------------------------------------------------


def test_get_all_kpis_returns_all_keys(kpis):
    assert set(kpis) == set(ALL_QUERY_NAMES)


def test_get_all_kpis_all_values_are_dataframes(kpis):
    non_df = [k for k, v in kpis.items() if not isinstance(v, pd.DataFrame)]
    assert not non_df, f"Non-DataFrame values for: {non_df}"


# ---------------------------------------------------------------------------
# Per-query: DataFrame type, expected columns, non-empty
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query_name", ALL_QUERY_NAMES)
def test_kpi_is_dataframe(kpis, query_name):
    assert isinstance(kpis[query_name], pd.DataFrame)


@pytest.mark.parametrize("query_name", ALL_QUERY_NAMES)
def test_kpi_expected_columns_present(kpis, query_name):
    actual = set(kpis[query_name].columns)
    missing = EXPECTED_COLUMNS[query_name] - actual
    assert not missing, f"{query_name!r} missing columns: {missing}"


@pytest.mark.parametrize("query_name", ALL_QUERY_NAMES)
def test_kpi_not_empty(kpis, query_name):
    assert len(kpis[query_name]) > 0, f"{query_name!r} returned an empty DataFrame"
