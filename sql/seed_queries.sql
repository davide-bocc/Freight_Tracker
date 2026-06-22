-- ============================================================================
-- ROW COUNTS
-- Expected: VESSEL=10, PORT=20, ROUTE=10, ROUTE_LEG=26, VOYAGE=60,
--           VOYAGE_STOP=216, CUSTOMER=15, SHIPMENT=200, CONTAINER=412,
--           CARGO_ITEM=1031, SHIPMENT_EVENT=1758
-- ============================================================================

SELECT
    'VESSEL' AS table_name,
    COUNT(*) AS row_count
FROM vessel
UNION ALL

SELECT
    'PORT',
    COUNT(*)
FROM port
UNION ALL

SELECT
    'ROUTE',
    COUNT(*)
FROM route
UNION ALL

SELECT
    'ROUTE_LEG',
    COUNT(*)
FROM route_leg
UNION ALL

SELECT
    'VOYAGE',
    COUNT(*)
FROM voyage
UNION ALL

SELECT
    'VOYAGE_STOP',
    COUNT(*)
FROM voyage_stop
UNION ALL

SELECT
    'CUSTOMER',
    COUNT(*)
FROM customer
UNION ALL

SELECT
    'SHIPMENT',
    COUNT(*)
FROM shipment
UNION ALL

SELECT
    'CONTAINER',
    COUNT(*)
FROM container
UNION ALL

SELECT
    'CARGO_ITEM',
    COUNT(*)
FROM cargo_item
UNION ALL

SELECT
    'SHIPMENT_EVENT',
    COUNT(*)
FROM shipment_event;

-- FK INTEGRITY
-- Each query returns 0 rows when the database is clean.
-- A non-zero count names the broken FK and the orphan count.

-- ROUTE.origin_port_id -> PORT
SELECT
    'ROUTE.origin_port_id' AS fk,
    COUNT(*) AS orphans
FROM route
WHERE origin_port_id NOT IN (SELECT port_id FROM port);

-- ROUTE.dest_port_id -> PORT
SELECT
    'ROUTE.dest_port_id' AS fk,
    COUNT(*) AS orphans
FROM route
WHERE dest_port_id NOT IN (SELECT port_id FROM port);

-- ROUTE_LEG.route_id -> ROUTE
SELECT
    'ROUTE_LEG.route_id' AS fk,
    COUNT(*) AS orphans
FROM route_leg
WHERE route_id NOT IN (SELECT route_id FROM route);

-- ROUTE_LEG.from_port_id -> PORT
SELECT
    'ROUTE_LEG.from_port_id' AS fk,
    COUNT(*) AS orphans
FROM route_leg
WHERE from_port_id NOT IN (SELECT port_id FROM port);

-- ROUTE_LEG.to_port_id -> PORT
SELECT
    'ROUTE_LEG.to_port_id' AS fk,
    COUNT(*) AS orphans
FROM route_leg
WHERE to_port_id NOT IN (SELECT port_id FROM port);

-- VOYAGE.vessel_id -> VESSEL
SELECT
    'VOYAGE.vessel_id' AS fk,
    COUNT(*) AS orphans
FROM voyage
WHERE vessel_id NOT IN (SELECT vessel_id FROM vessel);

-- VOYAGE.route_id -> ROUTE
SELECT
    'VOYAGE.route_id' AS fk,
    COUNT(*) AS orphans
FROM voyage
WHERE route_id NOT IN (SELECT route_id FROM route);

-- VOYAGE_STOP.voyage_id -> VOYAGE
SELECT
    'VOYAGE_STOP.voyage_id' AS fk,
    COUNT(*) AS orphans
FROM voyage_stop
WHERE voyage_id NOT IN (SELECT voyage_id FROM voyage);

-- VOYAGE_STOP.port_id -> PORT
SELECT
    'VOYAGE_STOP.port_id' AS fk,
    COUNT(*) AS orphans
FROM voyage_stop
WHERE port_id NOT IN (SELECT port_id FROM port);

-- SHIPMENT.customer_id -> CUSTOMER
SELECT
    'SHIPMENT.customer_id' AS fk,
    COUNT(*) AS orphans
FROM shipment
WHERE customer_id NOT IN (SELECT customer_id FROM customer);

-- SHIPMENT.voyage_id -> VOYAGE
SELECT
    'SHIPMENT.voyage_id' AS fk,
    COUNT(*) AS orphans
FROM shipment
WHERE voyage_id NOT IN (SELECT voyage_id FROM voyage);

-- SHIPMENT.origin_port_id -> PORT
SELECT
    'SHIPMENT.origin_port_id' AS fk,
    COUNT(*) AS orphans
FROM shipment
WHERE origin_port_id NOT IN (SELECT port_id FROM port);

-- SHIPMENT.dest_port_id -> PORT
SELECT
    'SHIPMENT.dest_port_id' AS fk,
    COUNT(*) AS orphans
FROM shipment
WHERE dest_port_id NOT IN (SELECT port_id FROM port);

-- CONTAINER.shipment_id -> SHIPMENT
SELECT
    'CONTAINER.shipment_id' AS fk,
    COUNT(*) AS orphans
FROM container
WHERE shipment_id NOT IN (SELECT shipment_id FROM shipment);

-- CARGO_ITEM.container_id -> CONTAINER
SELECT
    'CARGO_ITEM.container_id' AS fk,
    COUNT(*) AS orphans
FROM cargo_item
WHERE container_id NOT IN (SELECT container_id FROM container);

-- SHIPMENT_EVENT.shipment_id -> SHIPMENT
SELECT
    'SHIPMENT_EVENT.shipment_id' AS fk,
    COUNT(*) AS orphans
FROM shipment_event
WHERE shipment_id NOT IN (SELECT shipment_id FROM shipment);

-- SHIPMENT_EVENT.port_id -> PORT (nullable — exclude NULLs)
SELECT
    'SHIPMENT_EVENT.port_id' AS fk,
    COUNT(*) AS orphans
FROM shipment_event
WHERE
    port_id IS NOT NULL
    AND port_id NOT IN (SELECT port_id FROM port);


-- ============================================================================
-- FLEET — vessel roster with capacity and utilisation context
-- Joins VESSEL to VOYAGE to confirm every vessel has at least one voyage.
-- ============================================================================

SELECT
    v.vessel_name,
    v.operator,
    v.vessel_type,
    v.teu_capacity,
    v.flag_state,
    COUNT(vo.voyage_id) AS total_voyages,
    SUM(CASE WHEN vo.status = 'Completed' THEN 1 ELSE 0 END)
        AS completed_voyages
FROM vessel AS v
LEFT JOIN voyage AS vo ON v.vessel_id = vo.vessel_id
GROUP BY v.vessel_id
ORDER BY total_voyages DESC;


-- ============================================================================
-- NETWORK — route map with named origin/destination ports and leg count
-- Joins ROUTE to PORT twice (origin, destination) and counts legs per route.
-- ============================================================================

SELECT
    r.route_name,
    r.service_name,
    r.transit_days,
    po.un_locode AS origin_locode,
    po.port_name AS origin_port,
    pd.un_locode AS dest_locode,
    pd.port_name AS dest_port,
    COUNT(rl.leg_id) AS leg_count,
    SUM(rl.distance_nm) AS total_distance_nm
FROM route AS r
INNER JOIN port AS po ON r.origin_port_id = po.port_id
INNER JOIN port AS pd ON r.dest_port_id = pd.port_id
LEFT JOIN route_leg AS rl ON r.route_id = rl.route_id
GROUP BY r.route_id
ORDER BY r.transit_days DESC;


-- ============================================================================
-- OPERATIONS — voyage schedule with vessel name, route, and stop count
-- Joins VOYAGE to VESSEL, ROUTE, and aggregates VOYAGE_STOP.
-- Confirms stop records exist and delay data is populated.
-- ============================================================================

SELECT
    vo.voyage_number,
    v.vessel_name,
    r.route_name,
    vo.status,
    vo.departure_date,
    vo.arrival_date,
    COUNT(vs.stop_id) AS stop_count,
    ROUND(AVG(vs.delay_hours), 1) AS avg_delay_hours,
    ROUND(MAX(vs.delay_hours), 1) AS max_delay_hours
FROM voyage AS vo
INNER JOIN vessel AS v ON vo.vessel_id = v.vessel_id
INNER JOIN route AS r ON vo.route_id = r.route_id
LEFT JOIN voyage_stop AS vs ON vo.voyage_id = vs.voyage_id
GROUP BY vo.voyage_id
ORDER BY vo.departure_date
LIMIT 20;


-- ============================================================================
-- COMMERCIAL — shipment detail with customer, ports, container and cargo totals
-- Joins SHIPMENT to CUSTOMER, PORT (x2), CONTAINER, and CARGO_ITEM.
-- Confirms the full commercial chain resolves end-to-end.
-- ============================================================================

SELECT
    s.bl_number,
    c.company_name AS customer,
    po.un_locode AS origin,
    pd.un_locode AS destination,
    s.incoterms,
    s.status,
    COUNT(DISTINCT cn.container_id) AS containers,
    COUNT(ci.cargo_id) AS cargo_lines,
    ROUND(SUM(ci.weight_kg), 0) AS total_cargo_kg,
    ROUND(SUM(ci.value_usd), 0) AS total_cargo_usd
FROM shipment AS s
INNER JOIN customer AS c ON s.customer_id = c.customer_id
INNER JOIN port AS po ON s.origin_port_id = po.port_id
INNER JOIN port AS pd ON s.dest_port_id = pd.port_id
LEFT JOIN container AS cn ON s.shipment_id = cn.shipment_id
LEFT JOIN cargo_item AS ci ON cn.container_id = ci.container_id
GROUP BY s.shipment_id
ORDER BY total_cargo_usd DESC
LIMIT 20;


-- ============================================================================
-- TRACKING — full event timeline for one shipment, resolved to port names
-- Joins SHIPMENT_EVENT to SHIPMENT and PORT.
-- Confirms event chain is ordered and port references resolve.
-- ============================================================================

SELECT
    se.event_timestamp,
    se.event_type,
    p.un_locode AS port,
    p.port_name,
    se.description,
    se.created_by
FROM shipment_event AS se
INNER JOIN shipment AS s ON se.shipment_id = s.shipment_id
LEFT JOIN port AS p ON se.port_id = p.port_id
WHERE
    s.bl_number = (
        -- pick the shipment with the longest event chain
        SELECT s2.bl_number
        FROM shipment AS s2
        INNER JOIN shipment_event AS se2 ON s2.shipment_id = se2.shipment_id
        GROUP BY s2.shipment_id
        ORDER BY COUNT(*) DESC
        LIMIT 1
    )
ORDER BY se.event_timestamp;
