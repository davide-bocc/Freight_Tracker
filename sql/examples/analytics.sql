-- ============================================================================
-- ANALYTICAL QUERIES  —  Freight Tracker
-- 6 business-intelligence queries covering KPIs used in freight operations.
-- ============================================================================


-- 1. PORT CONGESTION INDEX
-- Measures how much delay (in hours) accumulates at each port relative to
-- the number of vessel calls.  A high index flags ports creating systemic
-- schedule risk; is_transshipment distinguishes hub vs. origin/dest ports.
SELECT
    p.un_locode,
    p.port_name,
    p.country_code,
    p.is_transshipment,
    COUNT(vs.stop_id) AS total_vessel_calls,
    COUNT(vs.stop_id) FILTER (WHERE vs.delay_hours > 0) AS delayed_calls,
    ROUND(
        100.0 * COUNT(vs.stop_id) FILTER (WHERE vs.delay_hours > 0)
        / NULLIF(COUNT(vs.stop_id), 0),
        1
    ) AS pct_delayed,
    ROUND(AVG(vs.delay_hours), 2) AS avg_delay_hours,
    ROUND(SUM(vs.delay_hours), 2) AS total_delay_hours,
    -- congestion index: average delay weighted by call volume
    ROUND(
        SUM(vs.delay_hours) / NULLIF(COUNT(vs.stop_id), 0),
        2
    ) AS congestion_index
FROM port AS p
LEFT JOIN voyage_stop AS vs ON p.port_id = vs.port_id
GROUP BY p.port_id, p.un_locode, p.port_name, p.country_code, p.is_transshipment
ORDER BY congestion_index DESC;


-- 2. ON-TIME PERFORMANCE BY TRADE LANE
-- Trade lane = origin-country to destination-country pair derived from
-- the shipment's port assignments.  OTP % is the share of shipments
-- arriving with zero delay at their final stop — the primary SLA metric
-- carriers report to customers.
WITH final_stop AS (
    -- last stop for each voyage (highest sequence number)
    SELECT
        voyage_id,
        port_id,
        delay_hours,
        ROW_NUMBER() OVER (
            PARTITION BY voyage_id
            ORDER BY stop_sequence DESC
        ) AS rn
    FROM voyage_stop
),

shipment_otp AS (
    SELECT
        p_orig.country_code AS origin_country,
        p_dest.country_code AS dest_country,
        s.shipment_id,
        fs.delay_hours AS final_delay_hours
    FROM shipment AS s
    INNER JOIN port AS p_orig ON s.origin_port_id = p_orig.port_id
    INNER JOIN port AS p_dest ON s.dest_port_id = p_dest.port_id
    INNER JOIN final_stop AS fs ON s.voyage_id = fs.voyage_id AND fs.rn = 1
    WHERE s.status IN ('Arrived', 'Delivered')
)

SELECT
    origin_country || ' -> ' || dest_country AS trade_lane,
    COUNT(*) AS shipments_measured,
    COUNT(*) FILTER (WHERE final_delay_hours = 0) AS on_time,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE final_delay_hours = 0)
        / NULLIF(COUNT(*), 0),
        1
    ) AS otp_pct,
    ROUND(AVG(final_delay_hours), 2) AS avg_final_delay_hours
FROM shipment_otp
GROUP BY origin_country, dest_country
ORDER BY otp_pct ASC;


-- 3. CARGO VALUE CONCENTRATION
-- Identifies which customers and industries account for the largest share
-- of total freight value — a Pareto / concentration-risk view that
-- commercial teams use to assess customer dependency.
WITH totals AS (
    SELECT SUM(total_value_usd) AS grand_total FROM shipment
)

SELECT
    c.company_name,
    c.industry,
    c.country_code,
    COUNT(s.shipment_id) AS shipment_count,
    ROUND(SUM(s.total_value_usd), 2) AS customer_revenue_usd,
    ROUND(
        100.0 * SUM(s.total_value_usd) / t.grand_total,
        2
    ) AS pct_of_total,
    ROUND(
        SUM(SUM(s.total_value_usd)) OVER (
            ORDER BY SUM(s.total_value_usd) DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) / t.grand_total * 100,
        2
    ) AS cumulative_pct
FROM shipment AS s
INNER JOIN customer AS c ON s.customer_id = c.customer_id
CROSS JOIN totals AS t
GROUP BY
    c.customer_id, c.company_name, c.industry, c.country_code, t.grand_total
ORDER BY customer_revenue_usd DESC;


-- 4. VOYAGE UTILIZATION RATE
-- Compares the total cargo weight loaded on each voyage against the vessel's
-- theoretical maximum payload (gross_tonnage used as proxy).  Low utilisation
-- signals commercial gaps; overloading flags a data-quality or ops issue.
SELECT
    vo.voyage_number,
    vo.departure_date,
    vo.status,
    v.vessel_name,
    v.vessel_type,
    v.gross_tonnage AS capacity_kg,
    COUNT(s.shipment_id) AS shipment_count,
    ROUND(SUM(s.total_weight_kg), 2) AS loaded_weight_kg,
    ROUND(
        100.0 * SUM(s.total_weight_kg) / NULLIF(v.gross_tonnage, 0),
        1
    ) AS utilisation_pct,
    CASE
        WHEN
            SUM(s.total_weight_kg) / NULLIF(v.gross_tonnage, 0) >= 0.90
            THEN 'Full'
        WHEN
            SUM(s.total_weight_kg) / NULLIF(v.gross_tonnage, 0) >= 0.60
            THEN 'Moderate'
        WHEN SUM(s.total_weight_kg) / NULLIF(v.gross_tonnage, 0) > 0 THEN 'Low'
        ELSE 'Empty'
    END AS load_band
FROM voyage AS vo
INNER JOIN vessel AS v ON vo.vessel_id = v.vessel_id
LEFT JOIN shipment AS s ON vo.voyage_id = s.voyage_id
GROUP BY
    vo.voyage_id, vo.voyage_number, vo.departure_date, vo.status,
    v.vessel_name, v.vessel_type, v.gross_tonnage
ORDER BY utilisation_pct DESC;


-- 5. MONTHLY REVENUE TREND
-- Books revenue by month of shipment booking, then overlays a 3-month
-- rolling average to smooth seasonality — standard input for executive
-- dashboards and budget-vs-actual reporting.
WITH monthly AS (
    SELECT
        STRFTIME('%Y-%m', booking_date) AS month,
        COUNT(*) AS new_shipments,
        ROUND(SUM(total_value_usd), 2) AS booked_revenue_usd
    FROM shipment
    WHERE status != 'Cancelled'
    GROUP BY month
)

SELECT
    month,
    new_shipments,
    booked_revenue_usd,
    ROUND(
        AVG(booked_revenue_usd) OVER (
            ORDER BY month
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ),
        2
    ) AS rolling_3m_avg_usd,
    ROUND(
        SUM(booked_revenue_usd) OVER (
            ORDER BY month
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ),
        2
    ) AS cumulative_revenue_usd
FROM monthly
ORDER BY month;


-- 6. AVERAGE DELAY BY VESSEL
-- Ranks every vessel by its mean port delay across all voyage stops,
-- separating scheduled/in-progress voyages from completed ones so
-- fleet managers can distinguish current performance from history.
SELECT
    v.vessel_name,
    v.vessel_type,
    v.flag_state,
    v.operator,
    COUNT(DISTINCT vo.voyage_id) AS total_voyages,
    COUNT(vs.stop_id) AS total_port_calls,
    ROUND(AVG(vs.delay_hours), 2) AS avg_delay_hours,
    ROUND(MAX(vs.delay_hours), 2) AS worst_delay_hours,
    ROUND(SUM(vs.delay_hours), 2) AS cumulative_delay_hours,
    DENSE_RANK() OVER (
        ORDER BY AVG(vs.delay_hours) DESC
    ) AS delay_rank
FROM vessel AS v
INNER JOIN voyage AS vo ON v.vessel_id = vo.vessel_id
INNER JOIN voyage_stop AS vs ON vo.voyage_id = vs.voyage_id
GROUP BY v.vessel_id, v.vessel_name, v.vessel_type, v.flag_state, v.operator
ORDER BY avg_delay_hours DESC;
