"""SQL query constants for execution ticket management."""

GET_TICKETS_FILTERED = """
SELECT arb_id, leg_1, leg_2, expected_cost, expected_profit,
       status, ticket_type, category, category_type, created_at
FROM execution_tickets
WHERE ($1::TEXT IS NULL OR status = $1)
  AND ($2::TEXT IS NULL OR category = $2)
  AND ($3::TEXT IS NULL OR ticket_type = $3)
ORDER BY created_at DESC
LIMIT $4
"""

GET_TICKET_BY_ID = """
SELECT t.arb_id, t.leg_1, t.leg_2, t.expected_cost, t.expected_profit,
       t.status, t.ticket_type, t.category, t.category_type, t.created_at,
       e.market_id, e.market_title, e.spike_price, e.spike_magnitude,
       e.confidence, e.detected_at AS event_detected_at
FROM execution_tickets t
LEFT JOIN flippening_events e ON t.arb_id = e.id AND t.ticket_type = 'flippening'
WHERE t.arb_id = $1
"""

UPDATE_TICKET_STATUS = """
UPDATE execution_tickets
SET status = $2
WHERE arb_id = $1
"""

INSERT_TICKET_ACTION = """
INSERT INTO flippening_ticket_actions
    (id, ticket_id, action, actual_entry_price, actual_size_usd,
     actual_exit_price, actual_pnl, slippage, notes, created_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
"""

GET_TICKET_ACTIONS = """
SELECT id, ticket_id, action, actual_entry_price, actual_size_usd,
       actual_exit_price, actual_pnl, slippage, notes, created_at
FROM flippening_ticket_actions
WHERE ticket_id = $1
ORDER BY created_at ASC
"""

GET_TICKET_SUMMARY = """
WITH ticket_stats AS (
    SELECT
        t.category,
        t.category_type,
        t.ticket_type,
        COUNT(*) AS total_tickets,
        COUNT(*) FILTER (WHERE t.status = 'executed') AS executed_count,
        COUNT(*) FILTER (WHERE t.status = 'expired') AS expired_count,
        COUNT(*) FILTER (WHERE t.status = 'cancelled') AS cancelled_count
    FROM execution_tickets t
    WHERE t.created_at >= NOW() - ($1 || ' days')::INTERVAL
    GROUP BY t.category, t.category_type, t.ticket_type
),
action_stats AS (
    SELECT
        t.category,
        t.category_type,
        t.ticket_type,
        ROUND(AVG(a.actual_pnl), 6) AS avg_pnl,
        ROUND(SUM(a.actual_pnl), 6) AS total_pnl,
        ROUND(AVG(ABS(a.slippage)), 6) AS avg_slippage,
        COUNT(*) FILTER (WHERE a.actual_pnl > 0) AS wins,
        COUNT(*) FILTER (WHERE a.actual_pnl IS NOT NULL) AS total_with_pnl
    FROM execution_tickets t
    JOIN flippening_ticket_actions a
        ON a.ticket_id = t.arb_id AND a.action = 'execute'
    WHERE t.created_at >= NOW() - ($1 || ' days')::INTERVAL
    GROUP BY t.category, t.category_type, t.ticket_type
)
SELECT
    ts.category,
    ts.category_type,
    ts.ticket_type,
    ts.total_tickets,
    ts.executed_count,
    ts.expired_count,
    ts.cancelled_count,
    ROUND(
        ts.executed_count::NUMERIC
        / NULLIF(ts.total_tickets, 0) * 100, 2
    ) AS execution_rate,
    COALESCE(a.avg_pnl, 0) AS avg_pnl,
    COALESCE(a.total_pnl, 0) AS total_pnl,
    COALESCE(a.avg_slippage, 0) AS avg_slippage,
    COALESCE(a.wins, 0) AS wins,
    COALESCE(a.total_with_pnl, 0) AS total_with_pnl,
    CASE
        WHEN COALESCE(a.total_with_pnl, 0) > 0
        THEN ROUND(a.wins::NUMERIC / a.total_with_pnl * 100, 2)
        ELSE 0
    END AS win_rate
FROM ticket_stats ts
LEFT JOIN action_stats a
    ON a.category = ts.category
    AND a.category_type = ts.category_type
    AND a.ticket_type = ts.ticket_type
ORDER BY ts.total_tickets DESC
"""

AUTO_EXPIRE_TICKETS = """
UPDATE execution_tickets
SET status = 'expired'
WHERE status = 'pending'
  AND created_at < NOW() - ($1 || ' hours')::INTERVAL
RETURNING arb_id
"""
