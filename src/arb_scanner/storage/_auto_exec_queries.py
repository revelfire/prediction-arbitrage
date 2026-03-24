"""SQL query constants for auto-execution pipeline."""

INSERT_LOG = """
INSERT INTO auto_execution_log (
    id, arb_id, trigger_spread_pct, trigger_confidence,
    criteria_snapshot, pre_exec_balances, size_usd,
    critic_verdict, execution_result_id, actual_spread,
    actual_pnl, slippage, duration_ms, circuit_breaker_state,
    status, source
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
    $11, $12, $13, $14, $15, $16
)
"""

UPDATE_LOG = """
UPDATE auto_execution_log
SET execution_result_id = COALESCE($2, execution_result_id),
    actual_spread = COALESCE($3, actual_spread),
    actual_pnl = COALESCE($4, actual_pnl),
    slippage = COALESCE($5, slippage),
    duration_ms = COALESCE($6, duration_ms),
    status = COALESCE($7, status)
WHERE id = $1
"""

GET_LOG = """
SELECT * FROM auto_execution_log WHERE id = $1
"""

LIST_LOG = """
SELECT DISTINCT ON (arb_id) *
FROM auto_execution_log
WHERE status != 'pending'
ORDER BY arb_id, created_at DESC
"""

LIST_LOG_DEDUPED = """
SELECT * FROM (
    SELECT DISTINCT ON (arb_id) *
    FROM auto_execution_log
    WHERE status != 'pending'
    ORDER BY arb_id, created_at DESC
) sub
ORDER BY created_at DESC
LIMIT $1
"""

INSERT_POSITION = """
INSERT INTO auto_execution_positions (
    id, arb_id, poly_market_id, kalshi_ticker,
    entry_spread, entry_cost_usd, status, max_hold_minutes
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""

CLOSE_POSITION = """
UPDATE auto_execution_positions
SET status = 'closed',
    current_value_usd = $2,
    closed_at = NOW()
WHERE id = $1
"""

GET_OPEN_POSITIONS = """
SELECT * FROM auto_execution_positions
WHERE status = 'open'
ORDER BY opened_at DESC
"""

GET_RISK_POSITIONS = """
SELECT
    arb_id,
    COALESCE(NULLIF(poly_market_id, ''), NULLIF(kalshi_ticker, ''), arb_id) AS market_id,
    entry_cost_usd,
    NULL::NUMERIC AS entry_price,
    NULL::INTEGER AS size_contracts,
    'arb' AS pipeline_type
FROM auto_execution_positions
WHERE status = 'open'
UNION ALL
SELECT
    arb_id,
    market_id,
    NULL::NUMERIC AS entry_cost_usd,
    entry_price,
    size_contracts,
    'flip' AS pipeline_type
FROM flippening_auto_positions
WHERE status IN ('open', 'exit_pending', 'exit_failed')
"""

ABANDON_EXPIRED_POSITIONS = """
UPDATE auto_execution_positions
SET status = 'abandoned',
    closed_at = NOW()
WHERE status = 'open'
  AND max_hold_minutes IS NOT NULL
  AND opened_at + (max_hold_minutes || ' minutes')::INTERVAL < NOW()
RETURNING id, arb_id, poly_market_id, kalshi_ticker, max_hold_minutes
"""

GET_DAILY_STATS = """
SELECT
    COUNT(*) FILTER (
        WHERE status IN ('executed', 'failed', 'partial')
    ) AS total_trades,
    COUNT(*) FILTER (WHERE actual_pnl > 0) AS wins,
    COUNT(*) FILTER (WHERE actual_pnl <= 0 AND status IN ('executed', 'partial')) AS losses,
    COALESCE(SUM(actual_pnl), 0) AS total_pnl,
    COALESCE(AVG(trigger_spread_pct) FILTER (
        WHERE status IN ('executed', 'failed', 'partial')
    ), 0) AS avg_spread,
    COALESCE(AVG(slippage) FILTER (
        WHERE status IN ('executed', 'partial')
    ), 0) AS avg_slippage,
    COUNT(*) FILTER (
        WHERE critic_verdict->>'approved' = 'false'
    ) AS critic_rejections,
    COUNT(*) FILTER (
        WHERE status = 'breaker_blocked'
    ) AS breaker_trips
FROM auto_execution_log
WHERE created_at >= (NOW() - ($1 || ' days')::interval)
  AND status != 'pending'
"""

GET_TODAY_REALIZED_PNL = """
SELECT COALESCE(SUM(actual_pnl), 0) AS total_pnl
FROM auto_execution_log
WHERE status IN ('executed', 'partial')
  AND actual_pnl IS NOT NULL
  AND created_at >= CURRENT_DATE
"""

GET_LATEST_REALIZED_LOSS = """
SELECT arb_id, actual_pnl, created_at
FROM auto_execution_log
WHERE status IN ('executed', 'partial')
  AND actual_pnl < 0
ORDER BY created_at DESC
LIMIT 1
"""
