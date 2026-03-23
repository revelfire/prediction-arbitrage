"""SQL query constants for execution order tracking."""

INSERT_ORDER = """
INSERT INTO execution_orders (
    id, arb_id, venue, venue_order_id, side,
    requested_price, fill_price, size_usd, size_contracts, status,
    error_message
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
"""

UPDATE_ORDER_STATUS = """
UPDATE execution_orders
SET status = $2,
    fill_price = COALESCE($3, fill_price),
    venue_order_id = COALESCE($4, venue_order_id),
    error_message = COALESCE($5, error_message),
    updated_at = NOW()
WHERE id = $1
"""

GET_ORDERS_FOR_TICKET = """
SELECT id, arb_id, venue, venue_order_id, side,
       requested_price, fill_price, size_usd, size_contracts,
       status, error_message, created_at, updated_at
FROM execution_orders
WHERE arb_id = $1
ORDER BY created_at
"""

GET_ORDER_BY_ID = """
SELECT id, arb_id, venue, venue_order_id, side,
       requested_price, fill_price, size_usd, size_contracts,
       status, error_message, created_at, updated_at
FROM execution_orders
WHERE id = $1
LIMIT 1
"""

GET_OPEN_ORDERS = """
SELECT id, arb_id, venue, venue_order_id, side,
       requested_price, size_usd, status, created_at
FROM execution_orders
WHERE status IN ('submitting', 'submitted')
ORDER BY created_at
"""

COUNT_OPEN_POSITIONS = """
SELECT COUNT(DISTINCT arb_id)
FROM execution_orders
WHERE status IN ('submitting', 'submitted', 'filled')
"""

INSERT_RESULT = """
INSERT INTO execution_results (
    id, arb_id, total_cost_usd, actual_spread,
    slippage_from_ticket, poly_order_id, kalshi_order_id, status
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (arb_id) DO NOTHING
"""

GET_RESULT = """
SELECT id, arb_id, total_cost_usd, actual_spread,
       slippage_from_ticket, poly_order_id, kalshi_order_id,
       status, created_at
FROM execution_results
WHERE arb_id = $1
"""

GET_DAILY_PNL = """
SELECT COALESCE(SUM(slippage_from_ticket), 0) AS daily_pnl
FROM execution_results
WHERE created_at >= (CURRENT_DATE AT TIME ZONE 'UTC')
  AND status IN ('complete', 'partial')
"""

GET_MARKET_EXPOSURE = """
SELECT COALESCE(SUM(eo.size_usd), 0) AS total_exposure
FROM execution_orders eo
WHERE eo.arb_id IN (
    SELECT arb_id FROM execution_tickets
    WHERE leg_1::text LIKE '%' || $1 || '%'
       OR leg_2::text LIKE '%' || $1 || '%'
)
AND eo.status IN ('submitted', 'filled')
"""
