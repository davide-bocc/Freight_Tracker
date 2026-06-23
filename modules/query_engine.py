"""
query_engine.py

Predefined SQL queries for the Freight Tracker analytics dashboard.
Each function takes a sqlite3.Connection and returns a pandas DataFrame.

Usage:
    from modules.query_engine import get_all_kpis, run_query
    kpis = get_all_kpis(conn)
    df   = run_query(conn, "SELECT * FROM VESSEL")
"""

from __future__ import annotations

import sqlite3
import pandas as pd

# FLEET

SQL_VESSEL_UTILIZATION = """
SELECT
    v.vessel_name,
    v.operator,
    v.teu_capacity,
    v.year_built,
    COUNT(DISTINCT voy.voyage_id) AS total_voyages,
    SUM(CASE WHEN voy.status = 'Completed' THEN 1 ELSE 0 END) AS completed,
    SUM(CASE WHEN voy.status IN ('In Transit','Departed') THEN 1 ELSE 0 END) AS active,
    SUM(CASE WHEN voy.status = 'Scheduled' THEN 1 ELSE 0 END) AS scheduled,
    SUM(CASE WHEN voy.status = 'Arrived' THEN 1 ELSE 0 END) AS arrived
FROM VESSEL v
LEFT JOIN VOYAGE voy ON voy.vessel_id = v.vessel_id
GROUP BY v.vessel_id
ORDER BY total_voyages DESC, v.vessel_name
"""

SQL_CAPACITY_VS_BOOKED_TEU = """
SELECT
    v.vessel_name,
    v.operator,
    v.teu_capacity,
    voy.voyage_number,
    voy.status,
    COUNT(c.container_id) AS container_count,
    COALESCE(SUM(CASE
        WHEN c.container_type LIKE '20%' THEN 1
        WHEN c.container_type LIKE '40%' THEN 2
        ELSE 1
    END), 0) AS booked_teu,
    ROUND(
        100.0 * COALESCE(SUM(CASE
            WHEN c.container_type LIKE '20%' THEN 1
            WHEN c.container_type LIKE '40%' THEN 2
            ELSE 1
        END), 0) / NULLIF(v.teu_capacity, 0),
    1) AS utilization_pct
FROM VESSEL v
JOIN VOYAGE voy ON voy.vessel_id = v.vessel_id
LEFT JOIN SHIPMENT s ON s.voyage_id = voy.voyage_id
LEFT JOIN CONTAINER c ON c.shipment_id = s.shipment_id
GROUP BY v.vessel_id, voy.voyage_id
ORDER BY utilization_pct DESC NULLS LAST
"""

FLEET = {
    "vessel_utilization": SQL_VESSEL_UTILIZATION,
    "capacity_vs_booked_teu": SQL_CAPACITY_VS_BOOKED_TEU,
}

# NETWORK

SQL_BUSIEST_PORTS = """
SELECT
    p.port_name,
    p.un_locode,
    p.country_code,
    p.is_transshipment,
    COUNT(vs.stop_id) AS total_calls,
    COUNT(DISTINCT vs.voyage_id) AS distinct_voyages,
    (SELECT COUNT(*) FROM SHIPMENT WHERE origin_port_id = p.port_id) AS outbound_shipments,
    (SELECT COUNT(*) FROM SHIPMENT WHERE dest_port_id = p.port_id) AS inbound_shipments,
    ROUND(AVG(vs.delay_hours), 2) AS avg_delay_h
FROM PORT p
LEFT JOIN VOYAGE_STOP vs ON vs.port_id = p.port_id
GROUP BY p.port_id
ORDER BY total_calls DESC
"""

SQL_AVG_DELAY_BY_ROUTE = """
SELECT
    r.route_name,
    r.service_name,
    r.transit_days AS scheduled_transit_days,
    COUNT(DISTINCT v.voyage_id) AS voyage_count,
    COUNT(vs.stop_id) AS total_stops,
    ROUND(AVG(vs.delay_hours), 2) AS avg_delay_h,
    ROUND(MAX(vs.delay_hours), 2) AS max_delay_h,
    SUM(CASE WHEN vs.delay_hours > 24 THEN 1 ELSE 0 END) AS stops_over_24h,
    ROUND(
        100.0 * SUM(CASE WHEN vs.delay_hours <= 4 AND vs.ata IS NOT NULL THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN vs.ata IS NOT NULL THEN 1 ELSE 0 END), 0),
    1) AS otp_pct
FROM ROUTE r
JOIN VOYAGE v ON v.route_id = r.route_id
JOIN VOYAGE_STOP vs ON vs.voyage_id = v.voyage_id
GROUP BY r.route_id
ORDER BY avg_delay_h DESC
"""

SQL_LEG_DISTANCES = """
SELECT
    r.route_name,
    r.service_name,
    rl.leg_sequence,
    fp.port_name AS from_port,
    fp.un_locode AS from_locode,
    fp.country_code AS from_country,
    tp.port_name AS to_port,
    tp.un_locode AS to_locode,
    tp.country_code AS to_country,
    rl.distance_nm,
    rl.typical_days,
    ROUND(CAST(rl.distance_nm AS REAL) / NULLIF(rl.typical_days, 0), 0) AS avg_speed_nm_per_day
FROM ROUTE_LEG rl
JOIN ROUTE r ON r.route_id = rl.route_id
JOIN PORT fp ON fp.port_id = rl.from_port_id
JOIN PORT tp ON tp.port_id = rl.to_port_id
ORDER BY r.route_id, rl.leg_sequence
"""

NETWORK = {
    "busiest_ports": SQL_BUSIEST_PORTS,
    "avg_delay_by_route": SQL_AVG_DELAY_BY_ROUTE,
    "leg_distances": SQL_LEG_DISTANCES,
}

# OPERATIONS

SQL_ON_TIME_PERFORMANCE = """
SELECT
    COUNT(*) AS total_stops,
    SUM(CASE WHEN vs.ata IS NOT NULL THEN 1 ELSE 0 END) AS completed_stops,
    SUM(CASE WHEN vs.ata IS NULL THEN 1 ELSE 0 END) AS pending_stops,
    SUM(CASE WHEN vs.delay_hours <= 4 AND vs.ata IS NOT NULL THEN 1 ELSE 0 END) AS on_time,
    SUM(CASE WHEN vs.delay_hours > 4 AND vs.ata IS NOT NULL THEN 1 ELSE 0 END) AS delayed,
    SUM(CASE WHEN vs.delay_hours > 24 AND vs.ata IS NOT NULL THEN 1 ELSE 0 END) AS severely_delayed,
    ROUND(
        100.0 * SUM(CASE WHEN vs.delay_hours <= 4 AND vs.ata IS NOT NULL THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN vs.ata IS NOT NULL THEN 1 ELSE 0 END), 0),
    1) AS otp_pct,
    ROUND(AVG(CASE WHEN vs.ata IS NOT NULL THEN vs.delay_hours END), 2) AS avg_delay_h,
    ROUND(MAX(vs.delay_hours), 2) AS max_delay_h
FROM VOYAGE_STOP vs
"""

SQL_DELAYED_VOYAGES = """
SELECT
    voy.voyage_number,
    voy.status,
    ve.vessel_name,
    ve.operator,
    r.route_name,
    voy.departure_date,
    voy.arrival_date,
    COUNT(vs.stop_id) AS stop_count,
    ROUND(SUM(vs.delay_hours), 2) AS total_delay_h,
    ROUND(AVG(vs.delay_hours), 2) AS avg_delay_h,
    ROUND(MAX(vs.delay_hours), 2) AS max_stop_delay_h,
    SUM(CASE WHEN vs.delay_hours > 24 THEN 1 ELSE 0 END) AS stops_over_24h
FROM VOYAGE voy
JOIN VESSEL ve ON ve.vessel_id = voy.vessel_id
JOIN ROUTE r ON r.route_id = voy.route_id
JOIN VOYAGE_STOP vs ON vs.voyage_id = voy.voyage_id
GROUP BY voy.voyage_id
HAVING SUM(vs.delay_hours) > 24
ORDER BY total_delay_h DESC
"""

SQL_AVG_DELAY_BY_PORT = """
SELECT
    p.port_name,
    p.un_locode,
    p.country_code,
    p.latitude,
    p.longitude,
    p.is_transshipment,
    COUNT(vs.stop_id) AS total_calls,
    SUM(CASE WHEN vs.ata IS NOT NULL THEN 1 ELSE 0 END) AS arrived_calls,
    ROUND(AVG(CASE WHEN vs.ata IS NOT NULL THEN vs.delay_hours END), 2) AS avg_delay_h,
    ROUND(MAX(vs.delay_hours), 2) AS max_delay_h,
    SUM(CASE WHEN vs.delay_hours > 4 AND vs.ata IS NOT NULL THEN 1 ELSE 0 END) AS delayed_arrivals,
    SUM(CASE WHEN vs.delay_hours > 24 AND vs.ata IS NOT NULL THEN 1 ELSE 0 END) AS severe_delays,
    ROUND(
        100.0 * SUM(CASE WHEN vs.delay_hours <= 4 AND vs.ata IS NOT NULL THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN vs.ata IS NOT NULL THEN 1 ELSE 0 END), 0),
    1) AS otp_pct
FROM PORT p
JOIN VOYAGE_STOP vs ON vs.port_id = p.port_id
GROUP BY p.port_id
ORDER BY avg_delay_h DESC NULLS LAST
"""

OPERATIONS = {
    "on_time_performance": SQL_ON_TIME_PERFORMANCE,
    "delayed_voyages": SQL_DELAYED_VOYAGES,
    "avg_delay_by_port": SQL_AVG_DELAY_BY_PORT,
}

# COMMERCIAL

SQL_TOP_CUSTOMERS_BY_VALUE = """
SELECT
    c.company_name,
    c.country_code,
    c.industry,
    COUNT(DISTINCT s.shipment_id) AS shipment_count,
    ROUND(SUM(s.total_value_usd), 2) AS total_value_usd,
    ROUND(AVG(s.total_value_usd), 2) AS avg_shipment_value_usd,
    ROUND(SUM(s.total_weight_kg), 2) AS total_weight_kg,
    COUNT(DISTINCT s.voyage_id) AS voyages_used,
    COUNT(DISTINCT s.status) AS status_variety
FROM CUSTOMER c
JOIN SHIPMENT s ON s.customer_id = c.customer_id
GROUP BY c.customer_id
ORDER BY total_value_usd DESC
"""

SQL_REVENUE_BY_TRADE_LANE = """
SELECT
    r.route_name,
    r.service_name,
    po.port_name AS origin_port,
    pd.port_name AS dest_port,
    COUNT(DISTINCT s.shipment_id) AS shipment_count,
    COUNT(DISTINCT s.customer_id) AS customer_count,
    ROUND(SUM(s.total_value_usd), 2) AS total_value_usd,
    ROUND(AVG(s.total_value_usd), 2) AS avg_value_usd,
    ROUND(SUM(s.total_weight_kg), 2) AS total_weight_kg,
    ROUND(AVG(s.total_weight_kg), 2) AS avg_weight_kg
FROM ROUTE r
JOIN PORT po ON po.port_id = r.origin_port_id
JOIN PORT pd ON pd.port_id = r.dest_port_id
JOIN VOYAGE v ON v.route_id = r.route_id
JOIN SHIPMENT s ON s.voyage_id = v.voyage_id
GROUP BY r.route_id
ORDER BY total_value_usd DESC
"""

SQL_SHIPMENT_STATUS_BREAKDOWN = """
SELECT
    status,
    COUNT(*) AS shipment_count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_total,
    ROUND(SUM(total_value_usd), 2) AS total_value_usd,
    ROUND(AVG(total_value_usd), 2) AS avg_value_usd,
    ROUND(SUM(total_weight_kg), 2) AS total_weight_kg,
    ROUND(AVG(total_weight_kg), 2) AS avg_weight_kg
FROM SHIPMENT
GROUP BY status
ORDER BY shipment_count DESC
"""

SQL_REEFER_BY_TYPE = """
SELECT
    c.container_type,
    COUNT(*) AS total_containers,
    SUM(c.is_reefer) AS reefer_count,
    SUM(CASE WHEN c.is_reefer = 0 THEN 1 ELSE 0 END) AS dry_count,
    ROUND(100.0 * SUM(c.is_reefer) / COUNT(*), 1) AS reefer_pct,
    ROUND(AVG(CASE WHEN c.is_reefer = 1 THEN c.temperature_c END), 1) AS avg_temp_c,
    ROUND(MIN(CASE WHEN c.is_reefer = 1 THEN c.temperature_c END), 1) AS min_temp_c,
    ROUND(MAX(CASE WHEN c.is_reefer = 1 THEN c.temperature_c END), 1) AS max_temp_c
FROM CONTAINER c
GROUP BY c.container_type
ORDER BY total_containers DESC
"""

SQL_REEFER_SUMMARY = """
SELECT
    COUNT(*) AS total_containers,
    SUM(is_reefer) AS reefer_containers,
    SUM(CASE WHEN is_reefer = 0 THEN 1 ELSE 0 END) AS dry_containers,
    ROUND(100.0 * SUM(is_reefer) / COUNT(*), 1) AS reefer_pct,
    ROUND(AVG(CASE WHEN is_reefer = 1 THEN temperature_c END), 1) AS avg_reefer_temp_c,
    ROUND(MIN(CASE WHEN is_reefer = 1 THEN temperature_c END), 1) AS min_temp_c,
    ROUND(MAX(CASE WHEN is_reefer = 1 THEN temperature_c END), 1) AS max_temp_c
FROM CONTAINER
"""

COMMERCIAL = {
    "top_customers_by_value": SQL_TOP_CUSTOMERS_BY_VALUE,
    "revenue_by_trade_lane": SQL_REVENUE_BY_TRADE_LANE,
    "shipment_status_breakdown": SQL_SHIPMENT_STATUS_BREAKDOWN,
    "reefer_by_type": SQL_REEFER_BY_TYPE,
    "reefer_summary": SQL_REEFER_SUMMARY,
}

# Public API

def run_query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    """Execute arbitrary SQL and return results as a DataFrame."""
    return pd.read_sql_query(sql, conn, params=params)


def _category(
    conn: sqlite3.Connection, queries: dict[str, str]
) -> dict[str, pd.DataFrame]:
    return {name: run_query(conn, sql) for name, sql in queries.items()}


def fleet(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    return _category(conn, FLEET)


def network(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    return _category(conn, NETWORK)


def operations(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    return _category(conn, OPERATIONS)


def commercial(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    return _category(conn, COMMERCIAL)


def get_all_kpis(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    """Run every predefined query and return a flat dict keyed by query name."""
    all_queries = {**FLEET, **NETWORK, **OPERATIONS, **COMMERCIAL}
    return {name: run_query(conn, sql) for name, sql in all_queries.items()}
