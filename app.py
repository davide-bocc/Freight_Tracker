"""
app.py — Freight Tracker  ·  Streamlit dashboard
Entry point: streamlit run app.py
"""

from __future__ import annotations

import html
from pathlib import Path

import pandas as pd
import streamlit as st

from modules.db_setup import init_db
from modules.query_engine import get_all_kpis, run_query
from modules.text_to_sql import QUESTION_LIMIT, ConversationHistory, ask
from modules.visualizer import (
    plot_delay_by_route,
    plot_ontime_performance,
    plot_revenue_by_lane,
    plot_route_map,
    plot_shipment_path,
    plot_shipment_status,
    plot_top_customers,
    plot_voyage_status,
)

# -- Page config (must be the very first Streamlit call) ------------------

st.set_page_config(
    page_title="Freight Tracker",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# -- Custom CSS ----------------------------------------------------------------

_css_path = Path("assets/style.css")
if _css_path.exists():
    st.markdown(f"<style>{_css_path.read_text()}</style>", unsafe_allow_html=True)

st.markdown(
    '<div class="ft-header">'
    '<span style="color:#00c9a7;font-size:32px">⚓</span>'
    '<div>'
    '<div class="ft-title">Freight Tracker</div>'
    '<div class="ft-subtitle">Global Shipping Intelligence</div>'
    '</div>'
    '</div>',
    unsafe_allow_html=True,
)

# -- SQL constants -------------------------------------------------------------

_SQL_SHIPMENTS = """
SELECT
    s.bl_number,
    c.company_name              AS customer,
    r.route_name                AS trade_lane,
    p_o.un_locode               AS origin,
    p_d.un_locode               AS destination,
    s.incoterms,
    s.status,
    vo.status                   AS voyage_status,
    s.booking_date,
    ROUND(s.total_weight_kg, 0) AS weight_kg,
    ROUND(s.total_value_usd, 0) AS value_usd
FROM SHIPMENT s
JOIN CUSTOMER c  ON c.customer_id  = s.customer_id
JOIN VOYAGE   vo ON vo.voyage_id   = s.voyage_id
JOIN ROUTE    r  ON r.route_id     = vo.route_id
JOIN PORT     p_o ON p_o.port_id  = s.origin_port_id
JOIN PORT     p_d ON p_d.port_id  = s.dest_port_id
ORDER BY s.booking_date DESC
"""

_SQL_DETAIL_META = """
SELECT
    s.bl_number, s.status, s.incoterms,
    s.booking_date, s.total_weight_kg, s.total_value_usd,
    c.company_name, c.industry,
    vo.voyage_number, vo.departure_date, vo.arrival_date, vo.status AS voyage_status,
    r.route_name,
    p_o.port_name AS origin_port, p_o.un_locode AS origin_locode,
    p_d.port_name AS dest_port,   p_d.un_locode AS dest_locode
FROM SHIPMENT s
JOIN CUSTOMER c  ON c.customer_id  = s.customer_id
JOIN VOYAGE   vo ON vo.voyage_id   = s.voyage_id
JOIN ROUTE    r  ON r.route_id     = vo.route_id
JOIN PORT     p_o ON p_o.port_id  = s.origin_port_id
JOIN PORT     p_d ON p_d.port_id  = s.dest_port_id
WHERE s.bl_number = ?
"""

_SQL_DETAIL_EVENTS = """
SELECT
    se.event_type,
    se.event_timestamp,
    p.port_name,
    se.description
FROM SHIPMENT_EVENT se
JOIN  SHIPMENT s ON s.shipment_id = se.shipment_id
LEFT JOIN PORT p ON p.port_id     = se.port_id
WHERE s.bl_number = ?
ORDER BY se.event_timestamp
"""

_SQL_DETAIL_CONTAINERS = """
SELECT
    c.container_number,
    c.container_type,
    c.is_reefer,
    c.temperature_c,
    c.tare_weight_kg,
    c.max_payload_kg,
    COUNT(ci.cargo_id)           AS cargo_items,
    ROUND(SUM(ci.value_usd), 0)  AS cargo_value_usd
FROM CONTAINER c
JOIN  SHIPMENT s      ON s.shipment_id    = c.shipment_id
LEFT JOIN CARGO_ITEM ci ON ci.container_id = c.container_id
WHERE s.bl_number = ?
GROUP BY c.container_id
ORDER BY c.container_number
"""

# -- Helper functions ----------------------------------------------------------


def _section(title: str) -> None:
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)


def _pill(text: str) -> str:
    return f'<div class="insight-pill">{html.escape(text)}</div>'


def _warning(text: str) -> str:
    return f'<div class="anomaly-warning">⚠️ {html.escape(text)}</div>'


_EVENT_ICON: dict[str, str] = {
    "Booking Confirmed": "📋",
    "Documents Received": "📄",
    "Gate In": "🚪",
    "Loaded on Vessel": "📦",
    "Vessel Departed": "⚓",
    "Port Arrival": "🛳️",
    "Customs Hold": "🔴",
    "Customs Cleared": "✅",
    "Gate Out": "🚪",
    "Delivered": "✅",
    "Exception": "⚠️",
    "Cancelled": "❌",
}

# -- Session state init --------------------------------------------------------

if "conn" not in st.session_state:
    st.session_state.conn = init_db("data/database.db")

if "kpis" not in st.session_state:
    with st.spinner("Loading analytics…"):
        st.session_state.kpis = get_all_kpis(st.session_state.conn)

if "shipments_df" not in st.session_state:
    st.session_state.shipments_df = run_query(st.session_state.conn, _SQL_SHIPMENTS)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = ConversationHistory()

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages: list[dict] = []

conn: object = st.session_state.conn
kpis: dict = st.session_state.kpis
ships: pd.DataFrame = st.session_state.shipments_df

# -- Tabs ----------------------------------------------------------------------

t_map, t_ov, t_ship, t_an, t_ask = st.tabs(
    [
        "🌍  Map",
        "📊  Overview",
        "📦  Shipments",
        "📈  Analytics",
        "💬  Ask",
    ]
)

# TAB 1 — OVERVIEW

with t_ov:
    otp = kpis["on_time_performance"].iloc[0]
    vutil = kpis["vessel_utilization"]
    sb = kpis["shipment_status_breakdown"]
    rl = kpis["revenue_by_trade_lane"]
    bp = kpis["busiest_ports"]
    adr = kpis["avg_delay_by_route"]
    tcv = kpis["top_customers_by_value"]

    active_voyages = int(vutil["active"].sum())
    sched_voyages = int(vutil["scheduled"].sum())
    total_ships = int(sb["shipment_count"].sum()) if not sb.empty else 0
    in_transit = (
        int(sb.loc[sb["status"] == "In Transit", "shipment_count"].sum())
        if "In Transit" in sb["status"].values
        else 0
    )
    avg_delay_h = float(otp.get("avg_delay_h") or 0)
    otp_pct = float(otp.get("otp_pct") or 0)
    severely = int(otp.get("severely_delayed") or 0)
    top_lane_name = rl.iloc[0]["route_name"] if not rl.empty else "N/A"
    top_lane_value = rl.iloc[0]["total_value_usd"] if not rl.empty else 0

    _section("KEY PERFORMANCE INDICATORS")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric(
        "Active Voyages",
        active_voyages,
        delta=f"{sched_voyages} scheduled",
        delta_color="off",
    )
    k2.metric(
        "Total Shipments",
        total_ships,
        delta=f"{in_transit} in transit",
        delta_color="off",
    )
    k3.metric(
        "Fleet OTP",
        f"{otp_pct:.1f}%",
        delta=f"avg delay {avg_delay_h:.1f} h",
        delta_color="inverse",
    )
    k4.metric(
        "Top Trade Lane",
        top_lane_name,
        delta=f"${top_lane_value / 1e6:.1f} M revenue",
        delta_color="off",
    )

    st.divider()
    ins_col, chart_col = st.columns([1, 1.6])

    with ins_col:
        _section("KEY INSIGHTS")
        if not bp.empty:
            p = bp.iloc[0]
            st.markdown(
                _pill(
                    f"🔴  {p['port_name']} ({p['un_locode']}) — busiest hub "
                    f"with {int(p['total_calls'])} vessel calls"
                ),
                unsafe_allow_html=True,
            )

        if not adr.empty:
            worst = adr.iloc[0]
            st.markdown(
                _pill(
                    f"⚠️  '{worst['route_name']}' has the highest avg delay: "
                    f"{worst['avg_delay_h']:.1f} h"
                ),
                unsafe_allow_html=True,
            )

        if not tcv.empty:
            cust = tcv.iloc[0]
            st.markdown(
                _pill(
                    f"💼  {cust['company_name']} ({cust['industry']}) — top customer "
                    f"at ${cust['total_value_usd'] / 1e6:.1f} M"
                ),
                unsafe_allow_html=True,
            )

        if not rl.empty:
            st.markdown(
                _pill(
                    f"🚢  {len(rl)} active trade lanes, "
                    f"${rl['total_value_usd'].sum() / 1e6:.0f} M total booked revenue"
                ),
                unsafe_allow_html=True,
            )

        if severely > 0:
            st.markdown(
                _warning(f"{severely} port call(s) delayed more than 24 h"),
                unsafe_allow_html=True,
            )

    with chart_col:
        _section("SHIPMENT STATUS MIX")
        st.plotly_chart(
            plot_shipment_status(sb),
            width="stretch",
            key="chart_overview_status",
        )

# TAB 2 — MAP

with t_map:
    ctrl_col, _ = st.columns([1, 3])
    with ctrl_col:
        _section("SHIPMENT LOOKUP")
        bl_map = (
            st.text_input(
                "Bill of Lading (leave blank for network view)",
                placeholder="BL2025000001",
                key="map_bl_input",
            )
            .strip()
            .upper()
        )

    if bl_map:
        st.plotly_chart(
            plot_shipment_path(conn, bl_map),
            width="stretch",
            key="chart_map_path",
        )
        st.caption(f"Showing voyage stops and tracking events for **{bl_map}**.")
    else:
        st.plotly_chart(
            plot_route_map(conn),
            width="stretch",
            key="chart_map_network",
        )
        st.caption(
            "Markers sized by vessel call volume.  "
            "Diamonds = transshipment hubs.  "
            "Enter a BL number above to track an individual shipment."
        )

# TAB 3 — SHIPMENTS

with t_ship:
    _section("FILTERS")
    fc1, fc2, fc3 = st.columns(3)

    all_statuses = sorted(ships["status"].dropna().unique())
    all_lanes = sorted(ships["trade_lane"].dropna().unique())

    sel_status = fc1.multiselect(
        "Shipment status", all_statuses, default=list(all_statuses), key="f_status"
    )
    sel_lanes = fc2.multiselect(
        "Trade lane", all_lanes, default=list(all_lanes), key="f_lanes"
    )
    bl_search = (
        fc3.text_input("Search BL", placeholder="BL2025…", key="f_bl").strip().upper()
    )

    filt = ships[ships["status"].isin(sel_status) & ships["trade_lane"].isin(sel_lanes)]
    if bl_search:
        filt = filt[filt["bl_number"].str.startswith(bl_search, na=False)]

    _section(f"RESULTS  ·  {len(filt):,} of {len(ships):,} shipments")
    st.dataframe(
        filt,
        width="stretch",
        height=300,
        column_config={
            "bl_number": st.column_config.TextColumn("Bill of Lading"),
            "customer": st.column_config.TextColumn("Customer"),
            "trade_lane": st.column_config.TextColumn("Trade Lane"),
            "origin": st.column_config.TextColumn("Origin"),
            "destination": st.column_config.TextColumn("Dest."),
            "incoterms": st.column_config.TextColumn("Incoterms"),
            "status": st.column_config.TextColumn("Status"),
            "voyage_status": st.column_config.TextColumn("Voyage"),
            "booking_date": st.column_config.TextColumn("Booked"),
            "weight_kg": st.column_config.NumberColumn("Weight kg", format="%,.0f"),
            "value_usd": st.column_config.NumberColumn("Value USD", format="$%,.0f"),
        },
        hide_index=True,
    )

    st.divider()
    _section("SHIPMENT DETAIL")

    bl_choices = [""] + filt["bl_number"].tolist()
    sel_bl = st.selectbox(
        "Select a Bill of Lading",
        bl_choices,
        format_func=lambda x: x if x else "— choose a shipment —",
        key="detail_bl",
    )

    if sel_bl:
        meta_df = run_query(conn, _SQL_DETAIL_META, params=(sel_bl,))
        ev_df = run_query(conn, _SQL_DETAIL_EVENTS, params=(sel_bl,))
        ctr_df = run_query(conn, _SQL_DETAIL_CONTAINERS, params=(sel_bl,))

        if not meta_df.empty:
            m = meta_df.iloc[0]
            dm1, dm2, dm3, dm4 = st.columns(4)
            dm1.metric("Customer", m["company_name"])
            dm2.metric("Route", m["route_name"])
            dm3.metric("Status", m["status"])
            dm4.metric("Value", f"${m['total_value_usd']:,.0f}")

            dm5, dm6, dm7, dm8 = st.columns(4)
            dm5.metric("Voyage", m["voyage_number"])
            dm6.metric("Incoterms", m["incoterms"])
            dm7.metric("Departure", str(m["departure_date"])[:10])
            dm8.metric("ETA", str(m["arrival_date"])[:10])

        ctr_col, ev_col = st.columns([1, 1.4])

        with ctr_col:
            if not ctr_df.empty:
                _section("CONTAINERS")
                st.dataframe(
                    ctr_df[
                        [
                            "container_number",
                            "container_type",
                            "is_reefer",
                            "temperature_c",
                            "cargo_items",
                            "cargo_value_usd",
                        ]
                    ],
                    width="stretch",
                    column_config={
                        "container_number": st.column_config.TextColumn("Container"),
                        "container_type": st.column_config.TextColumn("Type"),
                        "is_reefer": st.column_config.CheckboxColumn("Reefer"),
                        "temperature_c": st.column_config.NumberColumn(
                            "°C", format="%.1f"
                        ),
                        "cargo_items": st.column_config.NumberColumn("Items"),
                        "cargo_value_usd": st.column_config.NumberColumn(
                            "Value USD", format="$%,.0f"
                        ),
                    },
                    hide_index=True,
                )

        with ev_col:
            if not ev_df.empty:
                _section("TRACKING TIMELINE")
                for _, ev in ev_df.iterrows():
                    icon = _EVENT_ICON.get(ev["event_type"], "•")
                    ts = str(ev["event_timestamp"])[:16]
                    port = str(ev["port_name"] or "")
                    desc = str(ev["description"] or "")
                    port_part = f"  ·  {html.escape(port)}" if port else ""
                    st.markdown(
                        f'<div class="insight-pill">'
                        f'  {icon} <b>{html.escape(ev["event_type"])}</b>{port_part}'
                        f'  <span style="opacity:.55;font-size:11px">{ts}</span><br>'
                        f'  <span style="opacity:.6;font-size:11px">{html.escape(desc)}</span>'
                        f"</div>",
                        unsafe_allow_html=True,
                    )

        st.divider()
        _section("SHIPMENT ROUTE MAP")
        st.plotly_chart(
            plot_shipment_path(conn, sel_bl),
            width="stretch",
            key="chart_ship_path",
        )

# TAB 4 — ANALYTICS

with t_an:
    an1, an2 = st.columns(2)
    with an1:
        _section("DELAY BY ROUTE")
        st.plotly_chart(
            plot_delay_by_route(kpis["avg_delay_by_route"]),
            width="stretch",
            key="chart_an_delay",
        )
    with an2:
        _section("FLEET ON-TIME PERFORMANCE")
        st.plotly_chart(
            plot_ontime_performance(kpis["on_time_performance"]),
            width="stretch",
            key="chart_an_otp",
        )

    st.divider()
    an3, an4 = st.columns(2)
    with an3:
        _section("TOP CUSTOMERS BY REVENUE")
        st.plotly_chart(
            plot_top_customers(kpis["top_customers_by_value"]),
            width="stretch",
            key="chart_an_customers",
        )
    with an4:
        _section("REVENUE BY TRADE LANE")
        st.plotly_chart(
            plot_revenue_by_lane(kpis["revenue_by_trade_lane"]),
            width="stretch",
            key="chart_an_revenue",
        )

    st.divider()
    an5, an6 = st.columns(2)
    with an5:
        _section("VESSEL VOYAGE STATUS")
        st.plotly_chart(
            plot_voyage_status(kpis["vessel_utilization"]),
            width="stretch",
            key="chart_an_voyage",
        )
    with an6:
        _section("SHIPMENT STATUS BREAKDOWN")
        st.plotly_chart(
            plot_shipment_status(kpis["shipment_status_breakdown"]),
            width="stretch",
            key="chart_an_shipstatus",
        )

# TAB 5 — ASK

with t_ask:
    history: ConversationHistory = st.session_state.chat_history
    msgs: list[dict] = st.session_state.chat_messages

    # -- header row ---------------------------------------------
    hdr, badge = st.columns([3, 1])
    with hdr:
        _section("NATURAL LANGUAGE QUERY")
    with badge:
        colour = "#f08080" if history.remaining <= 5 else "#00c9a7"
        st.markdown(
            f'<div style="text-align:right;padding-top:4px">'
            f'<span class="limit-badge" style="border-color:{colour};color:{colour}">'
            f"{history.remaining} / {QUESTION_LIMIT} questions remaining"
            f"</span></div>",
            unsafe_allow_html=True,
        )

    # -- conversation display ------------------------------------
    for msg in msgs:
        if msg["role"] == "user":
            st.markdown(
                f'<div class="chat-user">{html.escape(msg["text"])}</div>',
                unsafe_allow_html=True,
            )
        else:
            if msg.get("error"):
                st.markdown(_warning(msg["error"]), unsafe_allow_html=True)
            else:
                st.markdown(
                    f'<div class="chat-assistant">'
                    f'{html.escape(msg.get("explanation") or "")}'
                    f"</div>",
                    unsafe_allow_html=True,
                )
            if msg.get("sql"):
                with st.expander("Generated SQL", expanded=False):
                    st.code(msg["sql"], language="sql")
            if msg.get("result_df") is not None and not msg["result_df"].empty:
                st.dataframe(msg["result_df"], width="stretch")

    # -- input / limit guard -------------------------------------
    if history.at_limit:
        st.markdown(
            _warning(f"Session limit of {QUESTION_LIMIT} questions reached."),
            unsafe_allow_html=True,
        )
        if st.button("↺  Start new session", type="primary", key="ask_reset"):
            history.clear()
            st.session_state.chat_messages = []
            st.rerun()
    else:
        question = st.chat_input(
            "Ask anything about your freight data…", key="ask_input"
        )
        if question:
            msgs.append({"role": "user", "text": question})
            with st.spinner("Generating SQL and querying…"):
                result = ask(question, conn, history)
            msgs.append(
                {
                    "role": "assistant",
                    "sql": result["sql"],
                    "result_df": result["result_df"],
                    "explanation": result["explanation"],
                    "error": result["error"],
                }
            )
            st.rerun()

        if msgs:
            if st.button("Clear conversation", key="ask_clear"):
                history.clear()
                st.session_state.chat_messages = []
                st.rerun()
