PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;


-- ============================================================================
-- FLEET
-- ============================================================================

CREATE TABLE IF NOT EXISTS VESSEL (
    vessel_id      INTEGER  PRIMARY KEY,
    imo_number     TEXT     NOT NULL UNIQUE,       -- IMO 9-digit
    vessel_name    TEXT     NOT NULL,
    vessel_type    TEXT     NOT NULL CHECK (vessel_type IN (
                       'Container', 'Bulk Carrier', 'Tanker', 'RoRo'
                   )),
    flag_state     TEXT     NOT NULL,              -- ISO 3166-1 alpha-2
    gross_tonnage  INTEGER  NOT NULL,
    teu_capacity   INTEGER,                        -- NULL for non-container vessels
    year_built     INTEGER  NOT NULL,
    operator       TEXT     NOT NULL
);


-- ============================================================================
-- NETWORK
-- ============================================================================

CREATE TABLE IF NOT EXISTS PORT (
    port_id           INTEGER  PRIMARY KEY,
    un_locode         TEXT     NOT NULL UNIQUE,    -- e.g. USLAX
    port_name         TEXT     NOT NULL,
    country_code      TEXT     NOT NULL,           -- ISO 3166-1 alpha-2
    latitude          REAL     NOT NULL,
    longitude         REAL     NOT NULL,
    timezone          TEXT     NOT NULL,
    is_transshipment  INTEGER  NOT NULL DEFAULT 0 CHECK (is_transshipment IN (0, 1))
);

CREATE TABLE IF NOT EXISTS ROUTE (
    route_id        INTEGER  PRIMARY KEY,
    route_name      TEXT     NOT NULL,
    origin_port_id  INTEGER  NOT NULL REFERENCES PORT (port_id),
    dest_port_id    INTEGER  NOT NULL REFERENCES PORT (port_id),
    service_name    TEXT     NOT NULL,
    transit_days    INTEGER  NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_route_origin  ON ROUTE (origin_port_id);
CREATE INDEX IF NOT EXISTS idx_route_dest    ON ROUTE (dest_port_id);

CREATE TABLE IF NOT EXISTS ROUTE_LEG (
    leg_id        INTEGER  PRIMARY KEY,
    route_id      INTEGER  NOT NULL REFERENCES ROUTE (route_id),
    leg_sequence  INTEGER  NOT NULL,
    from_port_id  INTEGER  NOT NULL REFERENCES PORT (port_id),
    to_port_id    INTEGER  NOT NULL REFERENCES PORT (port_id),
    distance_nm   INTEGER  NOT NULL,
    typical_days  INTEGER  NOT NULL,
    UNIQUE (route_id, leg_sequence)
);

CREATE INDEX IF NOT EXISTS idx_route_leg_route  ON ROUTE_LEG (route_id);
CREATE INDEX IF NOT EXISTS idx_route_leg_from   ON ROUTE_LEG (from_port_id);
CREATE INDEX IF NOT EXISTS idx_route_leg_to     ON ROUTE_LEG (to_port_id);


-- ============================================================================
-- OPERATIONS
-- ============================================================================

CREATE TABLE IF NOT EXISTS VOYAGE (
    voyage_id      INTEGER  PRIMARY KEY,
    voyage_number  TEXT     NOT NULL UNIQUE,
    vessel_id      INTEGER  NOT NULL REFERENCES VESSEL (vessel_id),
    route_id       INTEGER  NOT NULL REFERENCES ROUTE (route_id),
    departure_date TEXT     NOT NULL,              -- ISO-8601
    arrival_date   TEXT     NOT NULL,              -- ISO-8601
    status         TEXT     NOT NULL CHECK (status IN (
                       'Scheduled', 'Departed', 'In Transit', 'Arrived', 'Completed'
                   ))
);

CREATE INDEX IF NOT EXISTS idx_voyage_vessel  ON VOYAGE (vessel_id);
CREATE INDEX IF NOT EXISTS idx_voyage_route   ON VOYAGE (route_id);
CREATE INDEX IF NOT EXISTS idx_voyage_status  ON VOYAGE (status);

CREATE TABLE IF NOT EXISTS VOYAGE_STOP (
    stop_id        INTEGER  PRIMARY KEY,
    voyage_id      INTEGER  NOT NULL REFERENCES VOYAGE (voyage_id),
    port_id        INTEGER  NOT NULL REFERENCES PORT (port_id),
    stop_sequence  INTEGER  NOT NULL,
    eta            TEXT     NOT NULL,
    ata            TEXT,                           -- NULL until arrived
    etd            TEXT     NOT NULL,
    atd            TEXT,                           -- NULL until departed
    delay_hours    REAL     NOT NULL DEFAULT 0,
    UNIQUE (voyage_id, stop_sequence)
);

CREATE INDEX IF NOT EXISTS idx_stop_voyage  ON VOYAGE_STOP (voyage_id);
CREATE INDEX IF NOT EXISTS idx_stop_port    ON VOYAGE_STOP (port_id);


-- ============================================================================
-- COMMERCIAL
-- ============================================================================

CREATE TABLE IF NOT EXISTS CUSTOMER (
    customer_id    INTEGER  PRIMARY KEY,
    company_name   TEXT     NOT NULL,
    country_code   TEXT     NOT NULL,              -- ISO 3166-1 alpha-2
    industry       TEXT     NOT NULL,
    contact_email  TEXT     NOT NULL,
    credit_limit   REAL     NOT NULL,
    is_active      INTEGER  NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS SHIPMENT (
    shipment_id      INTEGER  PRIMARY KEY,
    bl_number        TEXT     NOT NULL UNIQUE,     -- Bill of Lading
    customer_id      INTEGER  NOT NULL REFERENCES CUSTOMER (customer_id),
    voyage_id        INTEGER  NOT NULL REFERENCES VOYAGE (voyage_id),
    origin_port_id   INTEGER  NOT NULL REFERENCES PORT (port_id),
    dest_port_id     INTEGER  NOT NULL REFERENCES PORT (port_id),
    incoterms        TEXT     NOT NULL CHECK (incoterms IN (
                         'EXW', 'FCA', 'FAS', 'FOB', 'CFR', 'CIF',
                         'CPT', 'CIP', 'DAP', 'DPU', 'DDP'
                     )),
    status           TEXT     NOT NULL CHECK (status IN (
                         'Booked', 'Loaded', 'In Transit',
                         'Arrived', 'Delivered', 'Cancelled'
                     )),
    booking_date     TEXT     NOT NULL,
    total_weight_kg  REAL     NOT NULL,
    total_value_usd  REAL     NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shipment_customer  ON SHIPMENT (customer_id);
CREATE INDEX IF NOT EXISTS idx_shipment_voyage    ON SHIPMENT (voyage_id);
CREATE INDEX IF NOT EXISTS idx_shipment_origin    ON SHIPMENT (origin_port_id);
CREATE INDEX IF NOT EXISTS idx_shipment_dest      ON SHIPMENT (dest_port_id);

CREATE TABLE IF NOT EXISTS CONTAINER (
    container_id      INTEGER  PRIMARY KEY,
    container_number  TEXT     NOT NULL UNIQUE,    -- ISO 6346, e.g. MSCU1234567
    shipment_id       INTEGER  NOT NULL REFERENCES SHIPMENT (shipment_id),
    container_type    TEXT     NOT NULL CHECK (container_type IN (
                          '20GP', '40GP', '40HC', '20RF', '40RF', '20OT', '40OT'
                      )),
    tare_weight_kg    REAL     NOT NULL,
    max_payload_kg    REAL     NOT NULL,
    is_reefer         INTEGER  NOT NULL DEFAULT 0 CHECK (is_reefer IN (0, 1)),
    temperature_c     REAL,                        -- NULL unless reefer
    seal_number       TEXT     NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_container_shipment  ON CONTAINER (shipment_id);

CREATE TABLE IF NOT EXISTS CARGO_ITEM (
    cargo_id      INTEGER  PRIMARY KEY,
    container_id  INTEGER  NOT NULL REFERENCES CONTAINER (container_id),
    hs_code       TEXT     NOT NULL,               -- 6-digit HS code
    description   TEXT     NOT NULL,
    quantity      INTEGER  NOT NULL,
    unit          TEXT     NOT NULL,
    weight_kg     REAL     NOT NULL,
    value_usd     REAL     NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cargo_container  ON CARGO_ITEM (container_id);


-- ============================================================================
-- TRACKING
-- ============================================================================

CREATE TABLE IF NOT EXISTS SHIPMENT_EVENT (
    event_id         INTEGER  PRIMARY KEY,
    shipment_id      INTEGER  NOT NULL REFERENCES SHIPMENT (shipment_id),
    event_type       TEXT     NOT NULL CHECK (event_type IN (
                         'Booking Confirmed',  'Documents Received', 'Gate In',
                         'Loaded on Vessel',   'Vessel Departed',    'Port Arrival',
                         'Customs Hold',       'Customs Cleared',    'Gate Out',
                         'Delivered',          'Exception',          'Cancelled'
                     )),
    event_timestamp  TEXT     NOT NULL,            -- ISO-8601
    port_id          INTEGER  REFERENCES PORT (port_id),
    description      TEXT,
    created_by       TEXT     NOT NULL DEFAULT 'SYSTEM'
);

CREATE INDEX IF NOT EXISTS idx_event_shipment  ON SHIPMENT_EVENT (shipment_id);
CREATE INDEX IF NOT EXISTS idx_event_port      ON SHIPMENT_EVENT (port_id);
CREATE INDEX IF NOT EXISTS idx_event_type      ON SHIPMENT_EVENT (event_type);
