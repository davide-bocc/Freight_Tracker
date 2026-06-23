"""
visualizer.py

Plotly charts for the Freight Tracker dashboard.
All functions return a plotly Figure and have no Streamlit dependencies.

Maps: plot_route_map, plot_shipment_path
Operations: plot_delay_by_route, plot_ontime_performance, plot_voyage_status
Commercial: plot_top_customers, plot_revenue_by_lane, plot_shipment_status
"""

from __future__ import annotations

import sqlite3
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Style constants

_C = {
    "navy": "#1a3a5c",
    "orange": "#e8673c",
    "green": "#27ae60",
    "yellow": "#f39c12",
    "blue": "#2980b9",
    "purple": "#8e44ad",
    "teal": "#16a085",
    "red": "#c0392b",
    "grey": "#7f8c8d",
    "silver": "#bdc3c7",
    "bg": "#ffffff",
    "grid": "#ecf0f1",
    "text": "#2c3e50",
    "subtext": "#7f8c8d",
}

_PALETTE = [
    _C["navy"],
    _C["orange"],
    _C["green"],
    _C["yellow"],
    _C["blue"],
    _C["purple"],
    _C["teal"],
    _C["red"],
    _C["grey"],
    "#f1c40f",
]

_STATUS_COLORS = {
    "Scheduled": _C["silver"],
    "Departed": _C["blue"],
    "In Transit": _C["orange"],
    "Arrived": _C["green"],
    "Completed": _C["navy"],
    "Booked": _C["silver"],
    "Loaded": _C["blue"],
    "Delivered": _C["green"],
    "Cancelled": _C["red"],
}

_FONT = "Inter, Arial, sans-serif"


def _base_layout(**overrides) -> dict:
    layout = dict(
        font=dict(family=_FONT, size=12, color=_C["text"]),
        paper_bgcolor=_C["bg"],
        plot_bgcolor=_C["bg"],
        margin=dict(l=24, r=24, t=52, b=24),
        showlegend=True,
        legend=dict(
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor=_C["grid"],
            borderwidth=1,
            font=dict(size=11),
        ),
        hoverlabel=dict(
            bgcolor=_C["navy"],
            font_color="#ffffff",
            font_size=12,
            bordercolor=_C["navy"],
        ),
    )
    layout.update(overrides)
    return layout


def _axis(title: str = "", **kw) -> dict:
    return dict(
        title=title,
        gridcolor=_C["grid"],
        linecolor=_C["grid"],
        showgrid=True,
        zeroline=False,
        tickfont=dict(size=11),
        **kw,
    )


# MAPS — SQL run directly against the connection

_SQL_PORT_VOLUME = """
SELECT
    p.port_id,
    p.port_name,
    p.un_locode,
    p.country_code,
    p.latitude,
    p.longitude,
    p.is_transshipment,
    COUNT(vs.stop_id) AS total_calls,
    COUNT(DISTINCT vs.voyage_id) AS distinct_voyages,
    ROUND(AVG(vs.delay_hours), 1) AS avg_delay_h
FROM PORT p
LEFT JOIN VOYAGE_STOP vs ON vs.port_id = p.port_id
GROUP BY p.port_id
ORDER BY total_calls DESC
"""

_SQL_ROUTE_LEGS = """
SELECT
    r.route_name,
    r.service_name,
    rl.leg_sequence,
    fp.latitude AS from_lat,
    fp.longitude AS from_lon,
    fp.port_name AS from_name,
    tp.latitude AS to_lat,
    tp.longitude AS to_lon,
    tp.port_name AS to_name,
    rl.distance_nm
FROM ROUTE_LEG rl
JOIN ROUTE r ON r.route_id = rl.route_id
JOIN PORT fp ON fp.port_id = rl.from_port_id
JOIN PORT tp ON tp.port_id = rl.to_port_id
ORDER BY r.route_id, rl.leg_sequence
"""

_SQL_SHIPMENT_STOPS = """
SELECT
    vs.stop_sequence,
    vs.eta,
    vs.ata,
    vs.delay_hours,
    p.port_name,
    p.un_locode,
    p.latitude,
    p.longitude
FROM SHIPMENT s
JOIN VOYAGE v ON v.voyage_id = s.voyage_id
JOIN VOYAGE_STOP vs ON vs.voyage_id = v.voyage_id
JOIN PORT p ON p.port_id = vs.port_id
WHERE s.bl_number = ?
ORDER BY vs.stop_sequence
"""

_SQL_SHIPMENT_EVENTS = """
SELECT
    se.event_type,
    se.event_timestamp,
    se.description,
    p.port_name,
    p.latitude,
    p.longitude
FROM SHIPMENT_EVENT se
JOIN SHIPMENT s ON s.shipment_id = se.shipment_id
LEFT JOIN PORT p ON p.port_id = se.port_id
WHERE s.bl_number = ?
ORDER BY se.event_timestamp
"""

_SQL_SHIPMENT_META = """
SELECT
    s.bl_number,
    s.status,
    s.incoterms,
    s.total_value_usd,
    c.company_name,
    v.voyage_number,
    r.route_name
FROM SHIPMENT s
JOIN CUSTOMER c ON c.customer_id = s.customer_id
JOIN VOYAGE v ON v.voyage_id = s.voyage_id
JOIN ROUTE r ON r.route_id = v.route_id
WHERE s.bl_number = ?
"""


def plot_route_map(conn: sqlite3.Connection) -> go.Figure:
    """World map: port markers sized by call volume, route legs as arcs."""
    ports = pd.read_sql_query(_SQL_PORT_VOLUME, conn)
    legs = pd.read_sql_query(_SQL_ROUTE_LEGS, conn)

    fig = go.Figure()

    # route leg lines — single trace with None breaks between segments
    lat_seq: list = []
    lon_seq: list = []
    for _, row in legs.iterrows():
        lat_seq += [row.from_lat, row.to_lat, None]
        lon_seq += [row.from_lon, row.to_lon, None]

    fig.add_trace(
        go.Scattergeo(
            lat=lat_seq,
            lon=lon_seq,
            mode="lines",
            line=dict(width=1, color=_C["navy"]),
            opacity=0.30,
            hoverinfo="skip",
            name="Route legs",
        )
    )

    # port markers — two groups by type
    for is_hub, label, color, symbol in (
        (1, "Transshipment hub", _C["orange"], "diamond"),
        (0, "Port", _C["navy"], "circle"),
    ):
        sub = ports[ports.is_transshipment == is_hub]
        if sub.empty:
            continue
        mn, mx = sub.total_calls.min(), sub.total_calls.max()
        sizes = (8 + 28 * (sub.total_calls - mn) / max(1, mx - mn)).fillna(10)
        fig.add_trace(
            go.Scattergeo(
                lat=sub.latitude,
                lon=sub.longitude,
                mode="markers",
                marker=dict(
                    size=sizes,
                    color=color,
                    symbol=symbol,
                    line=dict(width=1, color="#ffffff"),
                    opacity=0.9,
                ),
                text=sub.apply(
                    lambda r: (
                        f"<b>{r.port_name}</b> ({r.un_locode})<br>"
                        f"Calls: {int(r.total_calls)}<br>"
                        f"Voyages: {int(r.distinct_voyages)}<br>"
                        f"Avg delay: {r.avg_delay_h} h"
                    ),
                    axis=1,
                ),
                hovertemplate="%{text}<extra></extra>",
                name=label,
            )
        )

    fig.update_layout(
        **_base_layout(
            title=dict(text="Global Port Network — Call Volume", font=dict(size=16)),
            margin=dict(l=0, r=0, t=48, b=0),
            legend=dict(x=0.01, y=0.01, xanchor="left", yanchor="bottom"),
        ),
        geo=dict(
            projection_type="natural earth",
            showland=True,
            landcolor="#f0f0e8",
            showocean=True,
            oceancolor="#d6eaf8",
            showcountries=True,
            countrycolor="#cccccc",
            showcoastlines=True,
            coastlinecolor="#aaaaaa",
            showframe=False,
            bgcolor=_C["bg"],
        ),
    )
    return fig


def plot_shipment_path(conn: sqlite3.Connection, bl_number: str) -> go.Figure:
    """Single shipment: voyage-stop arc + event pins coloured by event type."""
    stops = pd.read_sql_query(_SQL_SHIPMENT_STOPS, conn, params=(bl_number,))
    events = pd.read_sql_query(_SQL_SHIPMENT_EVENTS, conn, params=(bl_number,))
    meta = pd.read_sql_query(_SQL_SHIPMENT_META, conn, params=(bl_number,))

    if stops.empty:
        fig = go.Figure()
        fig.update_layout(
            **_base_layout(
                title=dict(text=f"BL {bl_number} — not found", font=dict(size=14))
            )
        )
        return fig

    m = meta.iloc[0] if not meta.empty else {}
    title = (
        f"Shipment {bl_number}  ·  {m.get('company_name', '?')}  ·  "
        f"{m.get('route_name', '?')}  ·  Status: {m.get('status', '?')}"
    )

    fig = go.Figure()

    # voyage-stop route line
    fig.add_trace(
        go.Scattergeo(
            lat=list(stops.latitude),
            lon=list(stops.longitude),
            mode="lines+markers",
            line=dict(width=2.5, color=_C["navy"]),
            marker=dict(
                size=9,
                color=_C["navy"],
                symbol="circle",
                line=dict(width=1.5, color="#ffffff"),
            ),
            text=stops.apply(
                lambda r: (
                    f"<b>Stop {int(r.stop_sequence)}: {r.port_name}</b> ({r.un_locode})<br>"
                    f"ETA: {r.eta[:16]}<br>"
                    f"ATA: {r.ata[:16] if r.ata else '—'}<br>"
                    f"Delay: {r.delay_hours:.1f} h"
                ),
                axis=1,
            ),
            hovertemplate="%{text}<extra></extra>",
            name="Voyage stops",
        )
    )

    # event pins grouped by event type
    ev = events.dropna(subset=["latitude", "longitude"]).copy()
    if not ev.empty:
        unique_types = ev.event_type.unique().tolist()
        type_color = {
            t: _PALETTE[i % len(_PALETTE)] for i, t in enumerate(unique_types)
        }
        for etype, grp in ev.groupby("event_type"):
            fig.add_trace(
                go.Scattergeo(
                    lat=grp.latitude,
                    lon=grp.longitude,
                    mode="markers",
                    marker=dict(
                        size=13,
                        color=type_color[etype],
                        symbol="square",
                        line=dict(width=1.5, color="#ffffff"),
                    ),
                    text=grp.apply(
                        lambda r: (
                            f"<b>{r.event_type}</b><br>"
                            f"{r.port_name}<br>"
                            f"{r.event_timestamp[:16]}"
                        ),
                        axis=1,
                    ),
                    hovertemplate="%{text}<extra></extra>",
                    name=etype,
                )
            )

    fig.update_layout(
        **_base_layout(
            title=dict(text=title, font=dict(size=13)),
            margin=dict(l=0, r=0, t=52, b=0),
        ),
        geo=dict(
            projection_type="natural earth",
            showland=True,
            landcolor="#f0f0e8",
            showocean=True,
            oceancolor="#d6eaf8",
            showcountries=True,
            countrycolor="#cccccc",
            showcoastlines=True,
            coastlinecolor="#aaaaaa",
            showframe=False,
            fitbounds="locations",
            bgcolor=_C["bg"],
        ),
    )
    return fig


# OPERATIONS

def plot_delay_by_route(df: pd.DataFrame) -> go.Figure:
    """Horizontal grouped bar: avg and max delay per route, sorted by avg delay."""
    df = df.sort_values("avg_delay_h", ascending=True).copy()

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            y=df.route_name,
            x=df.avg_delay_h,
            orientation="h",
            name="Avg delay (h)",
            marker=dict(color=_C["orange"], opacity=0.85),
            text=df.avg_delay_h.map("{:.1f} h".format),
            textposition="outside",
            hovertemplate=(
                "<b>%{y}</b><br>" "Avg delay: %{x:.1f} h<br>" "<extra></extra>"
            ),
        )
    )
    fig.add_trace(
        go.Bar(
            y=df.route_name,
            x=df.max_delay_h,
            orientation="h",
            name="Max delay (h)",
            marker=dict(color=_C["red"], opacity=0.45),
            hovertemplate=(
                "<b>%{y}</b><br>" "Max delay: %{x:.1f} h<br>" "<extra></extra>"
            ),
        )
    )

    # OTP % as scatter on secondary axis
    if "otp_pct" in df.columns:
        fig.add_trace(
            go.Scatter(
                y=df.route_name,
                x=df.otp_pct,
                mode="markers+text",
                name="OTP %",
                marker=dict(size=9, color=_C["green"], symbol="diamond"),
                text=df.otp_pct.map("{:.0f}%".format),
                textposition="middle right",
                xaxis="x2",
                hovertemplate="<b>%{y}</b><br>OTP: %{x:.1f}%<extra></extra>",
            )
        )
        fig.update_layout(
            xaxis2=dict(
                title="OTP %",
                overlaying="x",
                side="top",
                range=[0, 110],
                showgrid=False,
                ticksuffix="%",
            )
        )

    fig.update_layout(
        **_base_layout(
            title=dict(text="Delay by Route", font=dict(size=16)),
            barmode="overlay",
            xaxis=_axis("Delay (hours)"),
            yaxis=_axis(),
        )
    )
    return fig


def plot_ontime_performance(df: pd.DataFrame) -> go.Figure:
    """Donut showing on-time / delayed / severely delayed / pending breakdown."""
    row = df.iloc[0]
    on_time = int(row.get("on_time", 0) or 0)
    severe = int(row.get("severely_delayed", 0) or 0)
    delayed = max(0, int(row.get("delayed", 0) or 0) - severe)
    pending = int(row.get("pending_stops", 0) or 0)
    otp_pct = float(row.get("otp_pct", 0) or 0)
    avg_delay = float(row.get("avg_delay_h", 0) or 0)

    labels = [
        "On time (≤ 4 h)",
        "Delayed (4–24 h)",
        "Severely delayed (> 24 h)",
        "Pending",
    ]
    values = [on_time, delayed, severe, pending]
    colors = [_C["green"], _C["yellow"], _C["red"], _C["silver"]]

    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.60,
            marker=dict(colors=colors, line=dict(color="#ffffff", width=2)),
            hovertemplate="<b>%{label}</b><br>%{value} stops (%{percent})<extra></extra>",
            textinfo="percent",
            textfont=dict(size=12),
            sort=False,
        )
    )

    fig.add_annotation(
        text=f"<b>{otp_pct:.1f}%</b><br><span style='font-size:10px'>On-Time</span>",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(size=20, family=_FONT, color=_C["navy"]),
        align="center",
    )
    fig.add_annotation(
        text=f"Avg delay: {avg_delay:.1f} h",
        x=0.5,
        y=-0.10,
        showarrow=False,
        font=dict(size=11, color=_C["subtext"]),
    )

    fig.update_layout(
        **_base_layout(
            title=dict(text="Fleet On-Time Performance", font=dict(size=16)),
        )
    )
    return fig


def plot_voyage_status(df: pd.DataFrame) -> go.Figure:
    """Stacked horizontal bar: voyage-state breakdown per vessel.

    Accepts the ``vessel_utilization`` DataFrame from query_engine.
    """
    df = df.copy()
    cols = ["completed", "active", "arrived", "scheduled"]
    labels = ["Completed", "Active", "Arrived", "Scheduled"]
    colors = [_C["navy"], _C["orange"], _C["green"], _C["silver"]]

    fig = go.Figure()
    for col, label, color in zip(cols, labels, colors):
        if col not in df.columns:
            continue
        vals = df[col].fillna(0).astype(int)
        fig.add_trace(
            go.Bar(
                y=df.vessel_name,
                x=vals,
                orientation="h",
                name=label,
                marker_color=color,
                hovertemplate=f"<b>%{{y}}</b><br>{label}: %{{x}}<extra></extra>",
                text=vals.where(vals > 0).map(
                    lambda v: str(int(v)) if pd.notna(v) else ""
                ),
                textposition="inside",
                insidetextanchor="middle",
                textfont=dict(color="#ffffff", size=10),
            )
        )

    fig.update_layout(
        **_base_layout(
            title=dict(text="Voyage Status by Vessel", font=dict(size=16)),
            barmode="stack",
            xaxis=_axis("Number of voyages"),
            yaxis=_axis(autorange="reversed"),
        )
    )
    return fig


# COMMERCIAL

def plot_top_customers(df: pd.DataFrame, top_n: int = 10) -> go.Figure:
    """Horizontal bar: top customers by total shipment value, coloured by industry."""
    df = df.head(top_n).sort_values("total_value_usd", ascending=True).copy()
    industries = df.industry.unique().tolist()
    ind_color = {ind: _PALETTE[i % len(_PALETTE)] for i, ind in enumerate(industries)}

    fig = go.Figure()
    for industry, grp in df.groupby("industry", sort=False):
        fig.add_trace(
            go.Bar(
                y=grp.company_name,
                x=grp.total_value_usd,
                orientation="h",
                name=industry,
                marker_color=ind_color[industry],
                text=grp.total_value_usd.map("${:,.0f}".format),
                textposition="outside",
                hovertemplate=(
                    f"<b>%{{y}}</b> — {industry}<br>"
                    "Revenue: $%{x:,.0f}<br>"
                    "<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        **_base_layout(
            title=dict(
                text=f"Top {top_n} Customers by Shipment Value", font=dict(size=16)
            ),
            barmode="overlay",
            xaxis=_axis("Total value (USD)", tickformat="$,.0f"),
            yaxis=_axis(),
        )
    )
    return fig


def plot_revenue_by_lane(df: pd.DataFrame) -> go.Figure:
    """Treemap: trade lanes sized by total revenue, colour intensity by shipment count."""
    df = df.copy()
    df["label"] = df.apply(
        lambda r: f"{r.route_name}<br>${r.total_value_usd / 1e6:.1f} M", axis=1
    )

    fig = go.Figure(
        go.Treemap(
            labels=df.label,
            parents=[""] * len(df),
            values=df.total_value_usd,
            customdata=df[
                ["shipment_count", "customer_count", "avg_value_usd", "service_name"]
            ].values,
            hovertemplate=(
                "<b>%{label}</b><br>"
                "Revenue: $%{value:,.0f}<br>"
                "Shipments: %{customdata[0]}<br>"
                "Customers: %{customdata[1]}<br>"
                "Avg value: $%{customdata[2]:,.0f}<br>"
                "Service: %{customdata[3]}<extra></extra>"
            ),
            marker=dict(
                colors=df.shipment_count,
                colorscale=[[0, _C["teal"]], [0.5, _C["navy"]], [1, _C["orange"]]],
                showscale=True,
                colorbar=dict(title="Shipments", thickness=14, len=0.6),
            ),
            textfont=dict(size=12, family=_FONT),
        )
    )

    fig.update_layout(
        **_base_layout(
            title=dict(text="Revenue by Trade Lane", font=dict(size=16)),
            margin=dict(l=10, r=10, t=52, b=10),
        )
    )
    return fig


def plot_shipment_status(df: pd.DataFrame) -> go.Figure:
    """Side-by-side: donut (shipment count) and bar (total value) by status."""
    df = df.copy()
    colors = [_STATUS_COLORS.get(s, _C["grey"]) for s in df.status]

    fig = make_subplots(
        rows=1,
        cols=2,
        specs=[[{"type": "pie"}, {"type": "xy"}]],
        subplot_titles=("Shipment count", "Total value (USD)"),
        column_widths=[0.44, 0.56],
    )

    # donut — count
    fig.add_trace(
        go.Pie(
            labels=df.status,
            values=df.shipment_count,
            hole=0.55,
            marker=dict(colors=colors, line=dict(color="#ffffff", width=2)),
            hovertemplate="<b>%{label}</b><br>%{value} shipments (%{percent})<extra></extra>",
            textinfo="label+percent",
            textfont=dict(size=11),
            showlegend=False,
            sort=False,
        ),
        row=1,
        col=1,
    )

    # bar — value
    df_bar = df.sort_values("total_value_usd", ascending=True)
    bar_colors = [_STATUS_COLORS.get(s, _C["grey"]) for s in df_bar.status]
    fig.add_trace(
        go.Bar(
            y=df_bar.status,
            x=df_bar.total_value_usd,
            orientation="h",
            marker=dict(color=bar_colors, line=dict(color="#ffffff", width=1)),
            text=df_bar.total_value_usd.map(lambda v: f"${v / 1e6:.1f} M"),
            textposition="outside",
            hovertemplate="<b>%{y}</b><br>Value: $%{x:,.0f}<extra></extra>",
            showlegend=False,
        ),
        row=1,
        col=2,
    )

    fig.update_layout(
        **_base_layout(
            title=dict(text="Shipment Status Breakdown", font=dict(size=16)),
        )
    )
    fig.update_xaxes(
        tickformat="$,.0f", gridcolor=_C["grid"], showgrid=True, row=1, col=2
    )
    fig.update_yaxes(gridcolor=_C["grid"], row=1, col=2)
    return fig
