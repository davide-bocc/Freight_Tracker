-- ============================================================================
-- JOIN SHOWCASE  —  Freight Tracker
-- 6 queries demonstrating JOIN patterns across the schema.
-- ============================================================================


-- 1. INNER JOIN
-- Which shipments are currently in transit, and which customer and vessel carry them?
-- Recruiters: three-table INNER JOIN collapsing commercial + operations data onto one row.
SELECT
    s.bl_number,
    c.company_name,
    c.country_code AS customer_country,
    v.vessel_name,
    v.vessel_type,
    s.incoterms,
    s.total_value_usd
FROM shipment AS s
INNER JOIN customer AS c ON s.customer_id = c.customer_id
INNER JOIN voyage AS vo ON s.voyage_id = vo.voyage_id
INNER JOIN vessel AS v ON vo.vessel_id = v.vessel_id
WHERE s.status = 'In Transit'
ORDER BY s.total_value_usd DESC;


-- 2. LEFT JOIN
-- Show every route with its leg count and total nautical miles;
-- routes returning NULL have no legs defined yet (data-completeness audit).
SELECT
    r.route_id,
    r.route_name,
    r.service_name,
    r.transit_days,
    COUNT(rl.leg_id) AS leg_count,
    SUM(rl.distance_nm) AS total_distance_nm
FROM route AS r
LEFT JOIN route_leg AS rl ON r.route_id = rl.route_id
GROUP BY r.route_id, r.route_name, r.service_name, r.transit_days
ORDER BY r.route_id;


-- 3. MULTI-TABLE JOIN (6 tables)
-- Full shipment manifest: customer, vessel, origin port, destination port,
-- incoterms, and cargo weight — everything ops needs at a glance.
SELECT
    s.bl_number,
    c.company_name,
    v.vessel_name,
    p_orig.un_locode AS origin_locode,
    p_orig.port_name AS origin_port,
    p_dest.un_locode AS dest_locode,
    p_dest.port_name AS dest_port,
    s.incoterms,
    s.total_weight_kg,
    s.total_value_usd,
    vo.status AS voyage_status
FROM shipment AS s
INNER JOIN customer AS c ON s.customer_id = c.customer_id
INNER JOIN voyage AS vo ON s.voyage_id = vo.voyage_id
INNER JOIN vessel AS v ON vo.vessel_id = v.vessel_id
INNER JOIN port AS p_orig ON s.origin_port_id = p_orig.port_id
INNER JOIN port AS p_dest ON s.dest_port_id = p_dest.port_id
ORDER BY s.booking_date DESC;


-- 4. SELF-JOIN on ROUTE_LEG
-- Pair each leg with the next consecutive leg on the same route to verify
-- port connectivity and surface large distance jumps between segments.
SELECT
    r.route_name,
    rl1.leg_sequence AS leg_num,
    p_from.port_name AS departure_port,
    p_mid.port_name AS junction_port,       -- shared waypoint
    p_to.port_name AS arrival_port,
    rl1.distance_nm AS leg1_nm,
    rl2.distance_nm AS leg2_nm,
    rl1.distance_nm + rl2.distance_nm AS combined_nm
FROM route_leg AS rl1
INNER JOIN route_leg AS rl2
    ON
        rl1.route_id = rl2.route_id
        AND rl2.leg_sequence = rl1.leg_sequence + 1
INNER JOIN route AS r ON rl1.route_id = r.route_id
INNER JOIN port AS p_from ON rl1.from_port_id = p_from.port_id
INNER JOIN port AS p_mid ON rl1.to_port_id = p_mid.port_id
INNER JOIN port AS p_to ON rl2.to_port_id = p_to.port_id
ORDER BY r.route_name, rl1.leg_sequence;


-- 5. SUBQUERY IN WHERE
-- Flag shipments whose declared value exceeds the average for all shipments
-- on the same route — useful for insurance tier and risk-review queues.
SELECT
    s.bl_number,
    c.company_name,
    r.route_name,
    s.total_value_usd,
    s.status
FROM shipment AS s
INNER JOIN customer AS c ON s.customer_id = c.customer_id
INNER JOIN voyage AS vo ON s.voyage_id = vo.voyage_id
INNER JOIN route AS r ON vo.route_id = r.route_id
WHERE
    s.total_value_usd > (
        SELECT AVG(s2.total_value_usd)
        FROM shipment AS s2
        INNER JOIN voyage AS vo2 ON s2.voyage_id = vo2.voyage_id
        WHERE vo2.route_id = vo.route_id
    )
ORDER BY s.total_value_usd DESC;


-- 6. EXISTS
-- Which vessels are on voyages that have at least one shipment stuck on
-- a Customs Hold?  Operations uses this to prioritise broker follow-up.
SELECT DISTINCT
    v.vessel_name,
    v.imo_number,
    v.operator,
    vo.voyage_number,
    vo.status AS voyage_status
FROM vessel AS v
INNER JOIN voyage AS vo ON v.vessel_id = vo.vessel_id
WHERE
    EXISTS (
        SELECT 1
        FROM shipment AS s
        INNER JOIN shipment_event AS se ON s.shipment_id = se.shipment_id
        WHERE
            s.voyage_id = vo.voyage_id
            AND se.event_type = 'Customs Hold'
    )
ORDER BY v.vessel_name;
