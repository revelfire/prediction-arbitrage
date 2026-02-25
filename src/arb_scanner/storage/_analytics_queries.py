"""SQL query constants for the analytics / history layer."""

GET_SPREAD_HISTORY = """
SELECT detected_at, net_spread_pct, annualized_return, depth_risk, max_size
FROM arb_opportunities
WHERE poly_event_id = $1
  AND kalshi_event_id = $2
  AND detected_at >= $3
ORDER BY detected_at DESC;
"""

GET_PAIR_SUMMARIES = """
SELECT poly_event_id,
       kalshi_event_id,
       MAX(net_spread_pct)  AS peak_spread,
       MIN(net_spread_pct)  AS min_spread,
       AVG(net_spread_pct)  AS avg_spread,
       COUNT(*)             AS total_detections,
       MIN(detected_at)     AS first_seen,
       MAX(detected_at)     AS last_seen
FROM arb_opportunities
WHERE detected_at >= $1
GROUP BY poly_event_id, kalshi_event_id
ORDER BY peak_spread DESC;
"""

GET_HOURLY_BUCKETS = """
SELECT date_trunc('hour', detected_at) AS hour,
       AVG(net_spread_pct)             AS avg_spread,
       MAX(net_spread_pct)             AS max_spread,
       COUNT(*)                        AS detection_count
FROM arb_opportunities
WHERE detected_at >= $1
GROUP BY 1
ORDER BY 1 DESC;
"""

GET_SCAN_HEALTH = """
SELECT date_trunc('hour', started_at)                            AS hour,
       COUNT(*)                                                  AS scan_count,
       AVG(EXTRACT(EPOCH FROM (completed_at - started_at)))      AS avg_duration_s,
       SUM(llm_evaluations)                                      AS total_llm_calls,
       SUM(opportunities_found)                                  AS total_opps,
       SUM(jsonb_array_length(COALESCE(errors::jsonb, '[]'::jsonb)))
                                                                 AS total_errors
FROM scan_logs
WHERE started_at >= $1
GROUP BY 1
ORDER BY 1 DESC;
"""

GET_RECENT_SCAN_LOGS = """
SELECT *
FROM scan_logs
ORDER BY started_at DESC
LIMIT $1;
"""

GET_OPPS_DATE_RANGE = """
SELECT id, poly_event_id, kalshi_event_id, buy_venue, sell_venue,
       cost_per_contract, gross_profit, net_profit, net_spread_pct,
       max_size, annualized_return, depth_risk, detected_at
FROM arb_opportunities
WHERE detected_at >= $1
  AND ($2::timestamptz IS NULL OR detected_at < $2)
ORDER BY detected_at DESC
LIMIT $3;
"""

GET_TICKETS_DATE_RANGE = """
SELECT t.arb_id, t.leg_1, t.leg_2, t.expected_cost,
       t.expected_profit, t.status, t.created_at,
       o.poly_event_id, o.kalshi_event_id, o.net_spread_pct
FROM execution_tickets t
JOIN arb_opportunities o ON t.arb_id = o.id
WHERE t.created_at >= $1
  AND ($2::timestamptz IS NULL OR t.created_at < $2)
ORDER BY t.created_at DESC
LIMIT $3;
"""

GET_MATCHES_DATE_RANGE = """
SELECT poly_event_id, kalshi_event_id, match_confidence,
       resolution_equivalent, resolution_risks, safe_to_arb,
       reasoning, matched_at, ttl_expires
FROM match_results
WHERE ($1::boolean OR ttl_expires > NOW())
  AND match_confidence >= $2
  AND matched_at >= $3
ORDER BY matched_at DESC;
"""

INSERT_SNAPSHOT = """
INSERT INTO market_price_snapshots (
    venue, event_id, yes_bid, yes_ask, no_bid, no_ask,
    volume_24h, snapshotted_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8);
"""

GET_PRICE_HISTORY = """
SELECT venue, event_id, yes_bid, yes_ask, no_bid, no_ask,
       volume_24h, snapshotted_at
FROM market_price_snapshots
WHERE venue = $1
  AND event_id = $2
  AND snapshotted_at >= $3
ORDER BY snapshotted_at DESC;
"""

INSERT_TREND_ALERT = """
INSERT INTO trend_alerts (
    alert_type, poly_event_id, kalshi_event_id,
    spread_before, spread_after, message, dispatched_at
) VALUES ($1, $2, $3, $4, $5, $6, $7);
"""

GET_RECENT_ALERTS = """
SELECT alert_type, poly_event_id, kalshi_event_id,
       spread_before, spread_after, message, dispatched_at
FROM trend_alerts
WHERE ($1::text IS NULL OR alert_type = $1)
ORDER BY dispatched_at DESC
LIMIT $2;
"""
