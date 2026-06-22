-- ============================================================================
-- WINDOW FUNCTION SHOWCASE  —  Freight Tracker
-- 6 queries demonstrating analytic/window functions across the schema.
-- ============================================================================


-- 1. RANK
-- Which voyage stops had the worst delays, ranked within each port?
-- Ties (equal delay_hours) share a rank; the next rank skips accordingly.
SELECT
    p.port_name,
    vo.voyage_number,
    vs.eta,
    vs.ata,
    vs.delay_hours,
    RANK() OVER (
        PARTITION BY vs.port_id
        ORDER BY vs.delay_hours DESC
    ) AS delay_rank_at_port
FROM voyage_stop AS vs
INNER JOIN voyage AS vo ON vs.voyage_id = vo.voyage_id
INNER JOIN port AS p ON vs.port_id = p.port_id
WHERE vs.delay_hours > 0
ORDER BY p.port_name, delay_rank_at_port;


-- 2. DENSE_RANK
-- Rank customers by total shipment revenue, grouped by their country.
-- DENSE_RANK avoids gaps in the sequence when multiple customers tie.
SELECT
    c.country_code,
    c.company_name,
    SUM(s.total_value_usd) AS total_revenue_usd,
    DENSE_RANK() OVER (
        PARTITION BY c.country_code
        ORDER BY SUM(s.total_value_usd) DESC
    ) AS revenue_rank_in_country
FROM customer AS c
INNER JOIN shipment AS s ON c.customer_id = s.customer_id
GROUP BY c.country_code, c.customer_id, c.company_name
ORDER BY c.country_code, revenue_rank_in_country;


-- 3. ROW_NUMBER
-- Assign a unique sequence number to each shipment per customer, ordered by
-- booking date — useful for identifying a customer's first, second, nth booking.
SELECT
    c.company_name,
    s.bl_number,
    s.booking_date,
    s.total_value_usd,
    ROW_NUMBER() OVER (
        PARTITION BY s.customer_id
        ORDER BY s.booking_date
    ) AS booking_sequence
FROM shipment AS s
INNER JOIN customer AS c ON s.customer_id = c.customer_id
ORDER BY c.company_name, booking_sequence;


-- 4. LAG
-- For each voyage stop, compare current delay to the previous stop's delay
-- on the same voyage to spot whether delays are compounding or recovering.
SELECT
    vo.voyage_number,
    vs.stop_sequence,
    p.port_name,
    vs.delay_hours AS current_delay_h,
    LAG(vs.delay_hours) OVER (
        PARTITION BY vs.voyage_id
        ORDER BY vs.stop_sequence
    ) AS prev_stop_delay_h,
    vs.delay_hours - LAG(vs.delay_hours, 1, 0.0) OVER (
        PARTITION BY vs.voyage_id
        ORDER BY vs.stop_sequence
    ) AS delay_change_h
FROM voyage_stop AS vs
INNER JOIN voyage AS vo ON vs.voyage_id = vo.voyage_id
INNER JOIN port AS p ON vs.port_id = p.port_id
ORDER BY vo.voyage_number, vs.stop_sequence;


-- 5. LEAD
-- For each route leg, show the distance of the next leg on the same route
-- so planners can see whether the upcoming segment is longer or shorter.
SELECT
    r.route_name,
    rl.leg_sequence,
    p_from.port_name AS from_port,
    p_to.port_name AS to_port,
    rl.distance_nm,
    LEAD(rl.distance_nm) OVER (
        PARTITION BY rl.route_id
        ORDER BY rl.leg_sequence
    ) AS next_leg_nm,
    LEAD(p_to.port_name) OVER (
        PARTITION BY rl.route_id
        ORDER BY rl.leg_sequence
    ) AS next_destination
FROM route_leg AS rl
INNER JOIN route AS r ON rl.route_id = r.route_id
INNER JOIN port AS p_from ON rl.from_port_id = p_from.port_id
INNER JOIN port AS p_to ON rl.to_port_id = p_to.port_id
ORDER BY r.route_name, rl.leg_sequence;


-- 6. RUNNING TOTAL with SUM OVER
-- Monthly shipment count and cumulative revenue (YTD running total) so
-- management can track booking momentum through the calendar year.
WITH monthly AS (
    SELECT
        STRFTIME('%Y', booking_date) AS booking_year,
        STRFTIME('%m', booking_date) AS booking_month,
        COUNT(*) AS shipments_booked,
        SUM(total_value_usd) AS monthly_revenue_usd
    FROM shipment
    GROUP BY booking_year, booking_month
)

SELECT
    booking_year,
    booking_month,
    shipments_booked,
    monthly_revenue_usd,
    SUM(shipments_booked) OVER (
        PARTITION BY booking_year
        ORDER BY booking_month
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS ytd_shipment_count,
    SUM(monthly_revenue_usd) OVER (
        PARTITION BY booking_year
        ORDER BY booking_month
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS ytd_revenue_usd
FROM monthly
ORDER BY booking_year, booking_month;
