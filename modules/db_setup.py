"""
db_setup.py — Freight Tracker database initialisation and seeding.

Usage:
    python -m modules.db_setup data/database.db
    from modules.db_setup import init_db
"""

from __future__ import annotations

import sqlite3
import random
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- VESSEL --
CREATE TABLE IF NOT EXISTS VESSEL (
    vessel_id INTEGER PRIMARY KEY,
    imo_number TEXT NOT NULL UNIQUE, -- IMO 9-digit
    vessel_name TEXT NOT NULL,
    vessel_type TEXT NOT NULL CHECK (vessel_type IN ('Container','Bulk Carrier','Tanker','RoRo')),
    flag_state TEXT NOT NULL, -- ISO 3166-1 alpha-2
    gross_tonnage INTEGER NOT NULL,
    teu_capacity INTEGER, -- NULL for non-container
    year_built INTEGER NOT NULL,
    operator TEXT NOT NULL
);

-- PORT --
CREATE TABLE IF NOT EXISTS PORT (
    port_id INTEGER PRIMARY KEY,
    un_locode TEXT NOT NULL UNIQUE, -- e.g. USLAX
    port_name TEXT NOT NULL,
    country_code TEXT NOT NULL, -- ISO 3166-1 alpha-2
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    timezone TEXT NOT NULL,
    is_transshipment INTEGER NOT NULL DEFAULT 0 CHECK (is_transshipment IN (0,1))
);

-- ROUTE --
CREATE TABLE IF NOT EXISTS ROUTE (
    route_id INTEGER PRIMARY KEY,
    route_name TEXT NOT NULL,
    origin_port_id INTEGER NOT NULL REFERENCES PORT(port_id),
    dest_port_id INTEGER NOT NULL REFERENCES PORT(port_id),
    service_name TEXT NOT NULL,
    transit_days INTEGER NOT NULL
);

-- ROUTE_LEG --
CREATE TABLE IF NOT EXISTS ROUTE_LEG (
    leg_id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES ROUTE(route_id),
    leg_sequence INTEGER NOT NULL,
    from_port_id INTEGER NOT NULL REFERENCES PORT(port_id),
    to_port_id INTEGER NOT NULL REFERENCES PORT(port_id),
    distance_nm INTEGER NOT NULL,
    typical_days INTEGER NOT NULL,
    UNIQUE (route_id, leg_sequence)
);

-- VOYAGE --
CREATE TABLE IF NOT EXISTS VOYAGE (
    voyage_id INTEGER PRIMARY KEY,
    voyage_number TEXT NOT NULL UNIQUE,
    vessel_id INTEGER NOT NULL REFERENCES VESSEL(vessel_id),
    route_id INTEGER NOT NULL REFERENCES ROUTE(route_id),
    departure_date TEXT NOT NULL, -- ISO-8601
    arrival_date TEXT NOT NULL, -- ISO-8601
    status TEXT NOT NULL CHECK (status IN
        ('Scheduled','Departed','In Transit','Arrived','Completed'))
);

-- VOYAGE_STOP --
CREATE TABLE IF NOT EXISTS VOYAGE_STOP (
    stop_id INTEGER PRIMARY KEY,
    voyage_id INTEGER NOT NULL REFERENCES VOYAGE(voyage_id),
    port_id INTEGER NOT NULL REFERENCES PORT(port_id),
    stop_sequence INTEGER NOT NULL,
    eta TEXT NOT NULL,
    ata TEXT, -- NULL if not yet arrived
    etd TEXT NOT NULL,
    atd TEXT, -- NULL if not yet departed
    delay_hours REAL NOT NULL DEFAULT 0,
    UNIQUE (voyage_id, stop_sequence)
);

-- CUSTOMER --
CREATE TABLE IF NOT EXISTS CUSTOMER (
    customer_id INTEGER PRIMARY KEY,
    company_name TEXT NOT NULL,
    country_code TEXT NOT NULL,
    industry TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    credit_limit REAL NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);

-- SHIPMENT --
CREATE TABLE IF NOT EXISTS SHIPMENT (
    shipment_id INTEGER PRIMARY KEY,
    bl_number TEXT NOT NULL UNIQUE, -- Bill of Lading
    customer_id INTEGER NOT NULL REFERENCES CUSTOMER(customer_id),
    voyage_id INTEGER NOT NULL REFERENCES VOYAGE(voyage_id),
    origin_port_id INTEGER NOT NULL REFERENCES PORT(port_id),
    dest_port_id INTEGER NOT NULL REFERENCES PORT(port_id),
    incoterms TEXT NOT NULL CHECK (incoterms IN
        ('EXW','FCA','FAS','FOB','CFR','CIF','CPT','CIP',
         'DAP','DPU','DDP')),
    status TEXT NOT NULL CHECK (status IN
        ('Booked','Loaded','In Transit','Arrived','Delivered','Cancelled')),
    booking_date TEXT NOT NULL,
    total_weight_kg REAL NOT NULL,
    total_value_usd REAL NOT NULL
);

-- CONTAINER --
CREATE TABLE IF NOT EXISTS CONTAINER (
    container_id INTEGER PRIMARY KEY,
    container_number TEXT NOT NULL UNIQUE, -- ISO 6346 e.g. MSCU1234567
    shipment_id INTEGER NOT NULL REFERENCES SHIPMENT(shipment_id),
    container_type TEXT NOT NULL CHECK (container_type IN
        ('20GP','40GP','40HC','20RF','40RF','20OT','40OT')),
    tare_weight_kg REAL NOT NULL,
    max_payload_kg REAL NOT NULL,
    is_reefer INTEGER NOT NULL DEFAULT 0 CHECK (is_reefer IN (0,1)),
    temperature_c REAL, -- NULL unless reefer
    seal_number TEXT NOT NULL
);

-- CARGO_ITEM --
CREATE TABLE IF NOT EXISTS CARGO_ITEM (
    cargo_id INTEGER PRIMARY KEY,
    container_id INTEGER NOT NULL REFERENCES CONTAINER(container_id),
    hs_code TEXT NOT NULL, -- 6-digit HS code
    description TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit TEXT NOT NULL,
    weight_kg REAL NOT NULL,
    value_usd REAL NOT NULL
);

-- SHIPMENT_EVENT --
CREATE TABLE IF NOT EXISTS SHIPMENT_EVENT (
    event_id INTEGER PRIMARY KEY,
    shipment_id INTEGER NOT NULL REFERENCES SHIPMENT(shipment_id),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'Booking Confirmed','Documents Received','Gate In',
        'Loaded on Vessel','Vessel Departed','Port Arrival',
        'Customs Hold','Customs Cleared','Gate Out',
        'Delivered','Exception','Cancelled')),
    event_timestamp TEXT NOT NULL, -- ISO-8601
    port_id INTEGER REFERENCES PORT(port_id),
    description TEXT,
    created_by TEXT NOT NULL DEFAULT 'SYSTEM'
);

-- INDEXES --
CREATE INDEX IF NOT EXISTS idx_route_origin ON ROUTE(origin_port_id);
CREATE INDEX IF NOT EXISTS idx_route_dest ON ROUTE(dest_port_id);
CREATE INDEX IF NOT EXISTS idx_route_leg_route ON ROUTE_LEG(route_id);
CREATE INDEX IF NOT EXISTS idx_route_leg_from ON ROUTE_LEG(from_port_id);
CREATE INDEX IF NOT EXISTS idx_route_leg_to ON ROUTE_LEG(to_port_id);
CREATE INDEX IF NOT EXISTS idx_voyage_vessel ON VOYAGE(vessel_id);
CREATE INDEX IF NOT EXISTS idx_voyage_route ON VOYAGE(route_id);
CREATE INDEX IF NOT EXISTS idx_voyage_status ON VOYAGE(status);
CREATE INDEX IF NOT EXISTS idx_stop_voyage ON VOYAGE_STOP(voyage_id);
CREATE INDEX IF NOT EXISTS idx_stop_port ON VOYAGE_STOP(port_id);
CREATE INDEX IF NOT EXISTS idx_shipment_customer ON SHIPMENT(customer_id);
CREATE INDEX IF NOT EXISTS idx_shipment_voyage ON SHIPMENT(voyage_id);
CREATE INDEX IF NOT EXISTS idx_shipment_origin ON SHIPMENT(origin_port_id);
CREATE INDEX IF NOT EXISTS idx_shipment_dest ON SHIPMENT(dest_port_id);
CREATE INDEX IF NOT EXISTS idx_container_shipment ON CONTAINER(shipment_id);
CREATE INDEX IF NOT EXISTS idx_cargo_container ON CARGO_ITEM(container_id);
CREATE INDEX IF NOT EXISTS idx_event_shipment ON SHIPMENT_EVENT(shipment_id);
CREATE INDEX IF NOT EXISTS idx_event_port ON SHIPMENT_EVENT(port_id);
CREATE INDEX IF NOT EXISTS idx_event_type ON SHIPMENT_EVENT(event_type);
"""

# ---------------------------------------------------------------------------
# Static reference data
# ---------------------------------------------------------------------------

_PORTS = [
    # (un_locode, name, country, lat, lon, tz, is_transshipment)
    ("USLAX", "Los Angeles", "US", 33.7392, -118.2620, "America/Los_Angeles", 0),
    ("USNYC", "New York / Newark", "US", 40.6840, -74.1740, "America/New_York", 0),
    ("USLGB", "Long Beach", "US", 33.7543, -118.2139, "America/Los_Angeles", 0),
    ("CNSHA", "Shanghai", "CN", 31.3713, 121.5000, "Asia/Shanghai", 0),
    ("CNNGB", "Ningbo", "CN", 29.8683, 121.5440, "Asia/Shanghai", 0),
    ("CNSZX", "Shenzhen (Yantian)", "CN", 22.5564, 114.2550, "Asia/Shanghai", 0),
    ("SGSIN", "Singapore", "SG", 1.2644, 103.8223, "Asia/Singapore", 1),
    ("NLRTM", "Rotterdam", "NL", 51.9225, 4.4792, "Europe/Amsterdam", 0),
    ("DEHAM", "Hamburg", "DE", 53.5330, 9.9500, "Europe/Berlin", 0),
    ("GBFXT", "Felixstowe", "GB", 51.9628, 1.3514, "Europe/London", 0),
    ("BEANR", "Antwerp", "BE", 51.2632, 4.4196, "Europe/Brussels", 0),
    ("AEDXB", "Dubai (Jebel Ali)", "AE", 24.9964, 55.0560, "Asia/Dubai", 1),
    ("HKHKG", "Hong Kong", "HK", 22.3086, 114.1750, "Asia/Hong_Kong", 1),
    ("KRPUS", "Busan", "KR", 35.0989, 129.0403, "Asia/Seoul", 1),
    ("JPYOK", "Yokohama", "JP", 35.4437, 139.6380, "Asia/Tokyo", 0),
    ("INMAA", "Chennai", "IN", 13.0827, 80.2707, "Asia/Kolkata", 0),
    ("EGPSD", "Port Said", "EG", 31.2565, 32.2841, "Africa/Cairo", 1),
    ("MAPTM", "Tanger Med", "MA", 35.8826, -5.5047, "Africa/Casablanca", 1),
    ("BRSSZ", "Santos", "BR", -23.9285, -46.3225, "America/Sao_Paulo", 0),
    ("AUSYD", "Sydney", "AU", -33.8500, 151.2340, "Australia/Sydney", 0),
]

_VESSELS = [
    # (imo, name, type, flag, gt, teu, year_built, operator)
    ("IMO9234567", "MSC OSCAR", "Container", "PA", 192000, 19224, 2015, "MSC"),
    ("IMO9345678", "EVER GIVEN", "Container", "PA", 199629, 20388, 2018, "Evergreen"),
    (
        "IMO9456789",
        "CMA CGM MARCO POLO",
        "Container",
        "FR",
        187000,
        16020,
        2012,
        "CMA CGM",
    ),
    ("IMO9567890", "MAERSK ESSEX", "Container", "DK", 141000, 13092, 2010, "Maersk"),
    (
        "IMO9678901",
        "COSCO SHIPPING UNIVERSE",
        "Container",
        "HK",
        187000,
        21237,
        2018,
        "COSCO",
    ),
    ("IMO9789012", "ONE INNOVATION", "Container", "JP", 145000, 14000, 2019, "ONE"),
    (
        "IMO9890123",
        "HAPAG BRUSSELS",
        "Container",
        "DE",
        135000,
        13167,
        2013,
        "Hapag-Lloyd",
    ),
    (
        "IMO9901234",
        "YANGMING TRIUMPH",
        "Container",
        "TW",
        141000,
        14000,
        2017,
        "Yang Ming",
    ),
    ("IMO9012345", "ZIM INTEGRATED", "Container", "IL", 116000, 11000, 2011, "ZIM"),
    ("IMO9123456", "PIL KARIMUN", "Container", "SG", 62000, 5600, 2014, "PIL"),
]

_CUSTOMERS = [
    # (name, country, industry, email, credit_limit)
    ("Apple Inc.", "US", "Electronics", "logistics@apple.com", 5000000),
    ("Samsung Electronics", "KR", "Electronics", "logistics@samsung.com", 4500000),
    ("Walmart Inc.", "US", "Retail", "logistics@walmart.com", 6000000),
    ("H&M Group", "SE", "Apparel", "logistics@hm.com", 2500000),
    ("IKEA Supply AG", "CH", "Furniture", "logistics@ikea.com", 3000000),
    ("Toyota Motor Corp.", "JP", "Automotive", "logistics@toyota.com", 4000000),
    ("Nestlé S.A.", "CH", "Food & Bev.", "logistics@nestle.com", 3500000),
    ("Nike Inc.", "US", "Apparel", "logistics@nike.com", 2800000),
    ("Unilever PLC", "GB", "FMCG", "logistics@unilever.com", 3200000),
    ("Amazon.com Inc.", "US", "E-commerce", "logistics@amazon.com", 7000000),
    ("Bosch GmbH", "DE", "Automotive", "logistics@bosch.com", 2000000),
    ("Philips N.V.", "NL", "Electronics", "logistics@philips.com", 1800000),
    ("Caterpillar Inc.", "US", "Machinery", "logistics@caterpillar.com", 2500000),
    ("Procter & Gamble Co.", "US", "FMCG", "logistics@pg.com", 3000000),
    ("Siemens AG", "DE", "Industrial", "logistics@siemens.com", 2200000),
]

# Routes: (name, origin_idx, dest_idx, service, transit_days)
# port indexes refer to _PORTS list (0-based)
_ROUTES_DEF = [
    ("Asia – US West Coast", 3, 0, "Pacific Express", 28),
    ("Asia – US East Coast", 3, 1, "All Water Atlantic", 35),
    ("Asia – North Europe", 3, 7, "AEX1", 30),
    ("Asia – Mediterranean", 3, 16, "MED Dragon", 25),
    ("Asia – Middle East", 3, 11, "ME Shuttle", 18),
    ("Europe – US East Coast", 7, 1, "Atlantic Pendulum", 14),
    ("Intra-Asia", 13, 6, "Intra-Asia Loop", 7),
    ("Asia – Australia", 3, 19, "Kangaroo Express", 22),
    ("US West – Asia", 2, 4, "Trans-Pacific Return", 26),
    ("South America – Europe", 18, 7, "SAX", 21),
]

# Route legs: (route_idx, seq, from_port_idx, to_port_idx, nm, days)
_LEGS_DEF = [
    (0, 1, 3, 12, 1139, 4),
    (0, 2, 12, 6, 930, 3),
    (0, 3, 6, 0, 6470, 14),  # Asia–USLAX via HK,SG
    (1, 1, 3, 12, 1139, 4),
    (1, 2, 12, 16, 7020, 14),
    (1, 3, 16, 1, 5600, 13),  # Asia–USNYC via HK,PS
    (2, 1, 3, 14, 1840, 5),
    (2, 2, 14, 13, 895, 2),
    (2, 3, 13, 7, 11480, 19),  # Asia–RTM via Yoko,Busan
    (3, 1, 3, 6, 2560, 7),
    (3, 2, 6, 11, 3740, 9),
    (3, 3, 11, 16, 1280, 6),  # Asia–EGPSD via SG,AE
    (4, 1, 3, 6, 2560, 7),
    (4, 2, 6, 11, 3740, 9),  # Asia–Dubai via SG
    (5, 1, 7, 10, 195, 1),
    (5, 2, 10, 1, 3490, 10),  # RTM–USNYC via BEANR
    (6, 1, 13, 6, 930, 3),
    (6, 2, 6, 12, 1600, 3),  # Busan–SG–HK
    (7, 1, 3, 12, 1139, 4),
    (7, 2, 12, 6, 930, 3),
    (7, 3, 6, 19, 5430, 13),  # Asia–SYD via HK,SG
    (8, 1, 2, 4, 425, 1),
    (8, 2, 4, 5, 200, 1),
    (8, 3, 5, 14, 2245, 7),  # LGB–NGB–SZX–Yoko
    (9, 1, 18, 16, 5740, 12),
    (9, 2, 16, 7, 1900, 7),  # Santos–EGPSD–RTM
]

_INCOTERMS = [
    "EXW",
    "FCA",
    "FAS",
    "FOB",
    "CFR",
    "CIF",
    "CPT",
    "CIP",
    "DAP",
    "DPU",
    "DDP",
]
_CONTAINER_SPECS = {
    "20GP": (2230, 21770, 0),
    "40GP": (3750, 26630, 0),
    "40HC": (3900, 26580, 0),
    "20RF": (2800, 21200, 1),
    "40RF": (4800, 25600, 1),
    "20OT": (2200, 21800, 0),
    "40OT": (3700, 26300, 0),
}

# HS codes (code, description, unit, value_per_unit_usd)
_CARGO_TYPES = [
    ("847130", "Laptops and notebooks", "units", 800),
    ("847150", "Processing units / CPUs", "units", 300),
    ("851762", "Smartphones", "units", 600),
    ("870321", "Passenger cars (petrol <1000cc)", "units", 18000),
    ("611020", "Cotton jerseys and pullovers", "pieces", 25),
    ("940360", "Wooden furniture", "pieces", 150),
    ("220421", "Bottled wine", "cases", 30),
    ("100190", "Wheat", "MT", 240),
    ("271019", "Other petroleum oils", "MT", 650),
    ("390110", "Polyethylene (LDPE)", "MT", 1200),
    ("841810", "Combined refrigerators-freezers", "units", 400),
    ("854140", "Photovoltaic cells", "units", 80),
    ("480256", "Uncoated paper rolls", "MT", 900),
    ("730890", "Steel structures", "MT", 1800),
    ("300490", "Medicaments (mixed)", "kgs", 120),
]

_OPERATORS = [
    "MSC",
    "Evergreen",
    "CMA CGM",
    "Maersk",
    "COSCO",
    "ONE",
    "Hapag-Lloyd",
    "Yang Ming",
    "ZIM",
    "PIL",
]

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _add_days(dt: datetime, days: float) -> datetime:
    return dt + timedelta(days=days)


def _voyage_status(departure: datetime, arrival: datetime, now: datetime) -> str:
    if now < departure:
        return "Scheduled"
    if now < departure + timedelta(hours=6):
        return "Departed"
    if now < arrival:
        return "In Transit"
    if now < arrival + timedelta(days=3):
        return "Arrived"
    return "Completed"


def _shipment_status_from_voyage(voyage_status: str, rng: random.Random) -> str:
    mapping = {
        "Scheduled": ["Booked"],
        "Departed": ["Loaded", "In Transit"],
        "In Transit": ["In Transit"],
        "Arrived": ["Arrived", "Delivered"],
        "Completed": ["Delivered"],
    }
    return rng.choice(mapping[voyage_status])


def _container_number(prefix: str, seq: int) -> str:
    """Generate ISO 6346-style container number (prefix + 6 digits + check)."""
    digits = f"{seq:06d}"
    number = prefix + digits
    # simplified check digit (not full ISO algorithm — for demo data)
    check = sum(ord(c) * (i + 1) for i, c in enumerate(number)) % 10
    return f"{number}{check}"


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _seed(conn: sqlite3.Connection) -> None:
    rng = random.Random(42)
    now = datetime(2026, 6, 17, 12, 0, 0)  # deterministic "now"
    anchor = datetime(2025, 1, 1, 0, 0, 0)  # 18-month window start

    cur = conn.cursor()

    # ports --
    cur.executemany(
        "INSERT OR IGNORE INTO PORT "
        "(un_locode,port_name,country_code,latitude,longitude,timezone,is_transshipment) "
        "VALUES (?,?,?,?,?,?,?)",
        _PORTS,
    )

    # vessels --
    cur.executemany(
        "INSERT OR IGNORE INTO VESSEL "
        "(imo_number,vessel_name,vessel_type,flag_state,gross_tonnage,"
        "teu_capacity,year_built,operator) VALUES (?,?,?,?,?,?,?,?)",
        _VESSELS,
    )

    # routes --
    port_ids: list[int] = [
        cur.execute("SELECT port_id FROM PORT WHERE un_locode=?", (p[0],)).fetchone()[0]
        for p in _PORTS
    ]

    route_ids: list[int] = []
    for name, orig_i, dest_i, svc, transit in _ROUTES_DEF:
        cur.execute(
            "INSERT OR IGNORE INTO ROUTE "
            "(route_name,origin_port_id,dest_port_id,service_name,transit_days) "
            "VALUES (?,?,?,?,?)",
            (name, port_ids[orig_i], port_ids[dest_i], svc, transit),
        )
        route_ids.append(
            cur.execute(
                "SELECT route_id FROM ROUTE WHERE route_name=?", (name,)
            ).fetchone()[0]
        )

    # route legs --
    for r_idx, seq, fp_i, tp_i, nm, days in _LEGS_DEF:
        cur.execute(
            "INSERT OR IGNORE INTO ROUTE_LEG "
            "(route_id,leg_sequence,from_port_id,to_port_id,distance_nm,typical_days) "
            "VALUES (?,?,?,?,?,?)",
            (route_ids[r_idx], seq, port_ids[fp_i], port_ids[tp_i], nm, days),
        )

    # customers --
    customer_ids: list[int] = []
    for row in _CUSTOMERS:
        cur.execute(
            "INSERT OR IGNORE INTO CUSTOMER "
            "(company_name,country_code,industry,contact_email,credit_limit) "
            "VALUES (?,?,?,?,?)",
            row,
        )
        customer_ids.append(
            cur.execute(
                "SELECT customer_id FROM CUSTOMER WHERE company_name=?", (row[0],)
            ).fetchone()[0]
        )

    vessel_ids: list[int] = [
        cur.execute(
            "SELECT vessel_id FROM VESSEL WHERE imo_number=?", (v[0],)
        ).fetchone()[0]
        for v in _VESSELS
    ]

    # voyages (60 over 18 months) --
    voyage_ids: list[int] = []
    voyage_meta: list[dict] = []  # {voyage_id, route_idx, status, dep, arr}

    for i in range(60):
        r_idx = i % len(_ROUTES_DEF)
        v_idx = i % len(_VESSELS)
        route_transit = _ROUTES_DEF[r_idx][4]

        # spread departures across 18 months
        days_offset = (i / 60) * 545  # 0..545 days
        dep = _add_days(anchor, days_offset + rng.uniform(-3, 3))
        dep = dep.replace(hour=rng.randint(4, 22), minute=0, second=0, microsecond=0)
        arr = _add_days(dep, route_transit + rng.uniform(-2, 4))

        status = _voyage_status(dep, arr, now)
        voyage_number = f"V{2025 + i // 30}-{(i % 30) + 1:03d}-{r_idx + 1}"

        cur.execute(
            "INSERT OR IGNORE INTO VOYAGE "
            "(voyage_number,vessel_id,route_id,departure_date,arrival_date,status) "
            "VALUES (?,?,?,?,?,?)",
            (
                voyage_number,
                vessel_ids[v_idx],
                route_ids[r_idx],
                _iso(dep),
                _iso(arr),
                status,
            ),
        )
        vid = cur.execute(
            "SELECT voyage_id FROM VOYAGE WHERE voyage_number=?", (voyage_number,)
        ).fetchone()[0]
        voyage_ids.append(vid)
        voyage_meta.append(
            {
                "voyage_id": vid,
                "route_idx": r_idx,
                "status": status,
                "dep": dep,
                "arr": arr,
            }
        )

    # voyage stops --
    for meta in voyage_meta:
        vid = meta["voyage_id"]
        r_idx = meta["route_idx"]
        dep = meta["dep"]
        arr = meta["arr"]
        status = meta["status"]

        # gather legs for this route
        legs = [l for l in _LEGS_DEF if l[0] == r_idx]
        if not legs:
            legs = [
                (
                    r_idx,
                    1,
                    _ROUTES_DEF[r_idx][1],
                    _ROUTES_DEF[r_idx][2],
                    1000,
                    _ROUTES_DEF[r_idx][4],
                )
            ]

        # build stop list: origin + each leg's destination
        stops: list[tuple[int, int]] = [(port_ids[_ROUTES_DEF[r_idx][1]], 0)]
        for leg in legs:
            stops.append((port_ids[leg[3]], leg[5]))  # (port_id, typical_days)

        cursor_dt = dep
        is_completed = status in ("Arrived", "Completed")
        is_in_transit = status == "In Transit"
        is_departed = status == "Departed"

        for seq, (port_id, leg_days) in enumerate(stops):
            delay = rng.gauss(0, 8)  # hours
            delay = max(-12, min(72, delay))
            eta = cursor_dt
            etd = _add_days(cursor_dt, max(0.25, leg_days * 0.15))  # port time

            if is_completed or (is_in_transit and seq < len(stops) // 2):
                ata = _add_days(eta, delay / 24)
                atd = _add_days(etd, delay / 24) if seq < len(stops) - 1 else None
            elif is_departed and seq == 0:
                ata = _add_days(eta, delay / 24)
                atd = _add_days(etd, delay / 24)
            else:
                ata = None
                atd = None

            cur.execute(
                "INSERT OR IGNORE INTO VOYAGE_STOP "
                "(voyage_id,port_id,stop_sequence,eta,ata,etd,atd,delay_hours) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    vid,
                    port_id,
                    seq + 1,
                    _iso(eta),
                    _iso(ata) if ata else None,
                    _iso(etd),
                    _iso(atd) if atd else None,
                    round(max(0, delay), 2),
                ),
            )
            cursor_dt = _add_days(etd, max(0, leg_days))

    # shipments (200) --
    shipment_meta: list[dict] = []
    ctr_seq = 1000

    for i in range(200):
        meta = voyage_meta[i % len(voyage_meta)]
        v_status = meta["status"]
        s_status = _shipment_status_from_voyage(v_status, rng)

        r_idx = meta["route_idx"]
        orig_port = port_ids[_ROUTES_DEF[r_idx][1]]
        dest_port = port_ids[_ROUTES_DEF[r_idx][2]]

        cust_id = customer_ids[i % len(customer_ids)]
        inco = rng.choice(_INCOTERMS)

        booking_dt = _add_days(meta["dep"], -rng.uniform(7, 30))
        weight = round(rng.uniform(5000, 25000), 2)
        value = round(rng.uniform(50000, 2000000), 2)
        bl = f"BL{2025000 + i + 1:07d}"

        cur.execute(
            "INSERT OR IGNORE INTO SHIPMENT "
            "(bl_number,customer_id,voyage_id,origin_port_id,dest_port_id,"
            "incoterms,status,booking_date,total_weight_kg,total_value_usd) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                bl,
                cust_id,
                meta["voyage_id"],
                orig_port,
                dest_port,
                inco,
                s_status,
                _iso(booking_dt),
                weight,
                value,
            ),
        )
        shp_id = cur.execute(
            "SELECT shipment_id FROM SHIPMENT WHERE bl_number=?", (bl,)
        ).fetchone()[0]
        shipment_meta.append(
            {
                "shipment_id": shp_id,
                "bl": bl,
                "status": s_status,
                "orig_port": orig_port,
                "dest_port": dest_port,
                "dep": meta["dep"],
                "arr": meta["arr"],
                "voyage_status": v_status,
                "booking_dt": booking_dt,
            }
        )

        # containers (1-3 per shipment) --
        n_containers = rng.randint(1, 3)
        ctype_choices = list(_CONTAINER_SPECS.keys())
        for _ in range(n_containers):
            ctype = rng.choice(ctype_choices)
            tare, maxp, is_r = _CONTAINER_SPECS[ctype]
            prefix_pool = [
                "MSCU",
                "EVGU",
                "CMAU",
                "MSKU",
                "CSNU",
                "ONEY",
                "HLCU",
                "YMLU",
                "ZIMU",
                "PILU",
            ]
            prefix = rng.choice(prefix_pool)
            cnum = _container_number(prefix, ctr_seq)
            ctr_seq += 1
            temp = round(rng.uniform(-25, 4), 1) if is_r else None
            seal = f"SL{rng.randint(100000,999999)}"

            cur.execute(
                "INSERT OR IGNORE INTO CONTAINER "
                "(container_number,shipment_id,container_type,tare_weight_kg,"
                "max_payload_kg,is_reefer,temperature_c,seal_number) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (cnum, shp_id, ctype, tare, maxp, is_r, temp, seal),
            )
            ctr_id = cur.execute(
                "SELECT container_id FROM CONTAINER WHERE container_number=?", (cnum,)
            ).fetchone()[0]

            # cargo items (1-4 per container) --
            n_cargo = rng.randint(1, 4)
            chosen_cargo = rng.sample(_CARGO_TYPES, min(n_cargo, len(_CARGO_TYPES)))
            for hs, desc, unit, uprice in chosen_cargo:
                qty = rng.randint(1, 500)
                w = round(qty * rng.uniform(0.5, 5.0), 2)
                val = round(qty * uprice * rng.uniform(0.8, 1.2), 2)
                cur.execute(
                    "INSERT INTO CARGO_ITEM "
                    "(container_id,hs_code,description,quantity,unit,weight_kg,value_usd) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (ctr_id, hs, desc, qty, unit, w, val),
                )

    # shipment events --
    # Full tracking chain consistent with shipment status
    event_chains = {
        "Booked": ["Booking Confirmed"],
        "Loaded": [
            "Booking Confirmed",
            "Documents Received",
            "Gate In",
            "Loaded on Vessel",
        ],
        "In Transit": [
            "Booking Confirmed",
            "Documents Received",
            "Gate In",
            "Loaded on Vessel",
            "Vessel Departed",
        ],
        "Arrived": [
            "Booking Confirmed",
            "Documents Received",
            "Gate In",
            "Loaded on Vessel",
            "Vessel Departed",
            "Port Arrival",
            "Customs Cleared",
            "Gate Out",
        ],
        "Delivered": [
            "Booking Confirmed",
            "Documents Received",
            "Gate In",
            "Loaded on Vessel",
            "Vessel Departed",
            "Port Arrival",
            "Customs Cleared",
            "Gate Out",
            "Delivered",
        ],
        "Cancelled": ["Booking Confirmed", "Cancelled"],
    }

    for smeta in shipment_meta:
        chain = event_chains.get(smeta["status"], ["Booking Confirmed"])
        t = smeta["booking_dt"]
        dep = smeta["dep"]
        arr = smeta["arr"]
        orig = smeta["orig_port"]
        dest = smeta["dest_port"]

        for evt in chain:
            if evt == "Booking Confirmed":
                ts = t
                port = orig
            elif evt == "Documents Received":
                ts = _add_days(t, rng.uniform(1, 3))
                port = orig
            elif evt == "Gate In":
                ts = _add_days(dep, -rng.uniform(0.5, 2))
                port = orig
            elif evt == "Loaded on Vessel":
                ts = _add_days(dep, -rng.uniform(0, 0.5))
                port = orig
            elif evt == "Vessel Departed":
                ts = _add_days(dep, rng.uniform(0, 0.25))
                port = orig
            elif evt == "Port Arrival":
                ts = _add_days(arr, rng.uniform(-0.25, 0.5))
                port = dest
            elif evt == "Customs Cleared":
                ts = _add_days(arr, rng.uniform(0.5, 2))
                port = dest
            elif evt == "Gate Out":
                ts = _add_days(arr, rng.uniform(2, 3))
                port = dest
            elif evt == "Delivered":
                ts = _add_days(arr, rng.uniform(3, 7))
                port = dest
            elif evt == "Cancelled":
                ts = _add_days(t, rng.uniform(0.5, 5))
                port = orig
            else:
                ts = t
                port = orig

            cur.execute(
                "INSERT INTO SHIPMENT_EVENT "
                "(shipment_id,event_type,event_timestamp,port_id,description) "
                "VALUES (?,?,?,?,?)",
                (smeta["shipment_id"], evt, _iso(ts), port, f"{evt} for {smeta['bl']}"),
            )

            # occasional customs hold (5% chance after Gate In)
            if evt == "Gate In" and rng.random() < 0.05:
                hold_ts = _add_days(ts, rng.uniform(0.1, 0.5))
                cur.execute(
                    "INSERT INTO SHIPMENT_EVENT "
                    "(shipment_id,event_type,event_timestamp,port_id,description) "
                    "VALUES (?,?,?,?,?)",
                    (
                        smeta["shipment_id"],
                        "Customs Hold",
                        _iso(hold_ts),
                        orig,
                        f"Random inspection triggered for {smeta['bl']}",
                    ),
                )

    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_db(db_path: str | Path = ":memory:") -> sqlite3.Connection:
    """
    Return a sqlite3.Connection with FK enforcement, WAL mode, and row_factory=Row.
    Creates and seeds the schema on first use (idempotent on subsequent calls).
    check_same_thread=False is required for Streamlit, which accesses the
    connection from multiple threads within a single session.
    """
    db_path = Path(db_path)
    if db_path != Path(":memory:"):
        db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Apply DDL (idempotent — all CREATE … IF NOT EXISTS)
    conn.executescript(_DDL)

    # Seed only when tables are empty
    row = conn.execute("SELECT COUNT(*) FROM PORT").fetchone()
    if row[0] == 0:
        _seed(conn)

    return conn


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/database.db"
    conn = init_db(path)

    # Quick sanity report
    tables = [
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
    print(f"Database: {path}")
    print("-" * 36)
    for t in tables:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<22} {n:>5} rows")
    conn.close()
    print("Done.")
