"""SQL query constants for the flippening_auto_positions table."""

INSERT_POSITION = """
INSERT INTO flippening_auto_positions
    (arb_id, market_id, token_id, side, size_contracts,
     entry_price, entry_order_id, max_hold_minutes,
     market_title, market_slug)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
RETURNING id
"""

_POS_COLS = """id, arb_id, market_id, token_id, side, size_contracts,
       entry_price, entry_order_id, exit_order_id, status, opened_at, max_hold_minutes,
       market_title, market_slug"""

GET_OPEN_POSITION = f"""
SELECT {_POS_COLS}
FROM flippening_auto_positions
WHERE market_id = $1 AND status IN ('open', 'exit_failed')
LIMIT 1
"""

CLOSE_POSITION = """
UPDATE flippening_auto_positions
SET status = 'closed',
    exit_order_id = $2,
    exit_price = $3,
    realized_pnl = $4,
    exit_reason = $5,
    closed_at = NOW()
WHERE market_id = $1 AND status IN ('open', 'exit_pending', 'exit_failed')
"""

MARK_EXIT_PENDING = """
UPDATE flippening_auto_positions
SET status = 'exit_pending',
    exit_order_id = $2,
    exit_price = $3,
    exit_reason = $4
WHERE market_id = $1 AND status IN ('open', 'exit_pending', 'exit_failed')
"""

MARK_EXIT_FAILED = """
UPDATE flippening_auto_positions
SET status = 'exit_failed'
WHERE market_id = $1 AND status IN ('open', 'exit_pending')
"""

GET_POSITION_BY_ARB_ID = f"""
SELECT {_POS_COLS}
FROM flippening_auto_positions
WHERE arb_id = $1
ORDER BY opened_at DESC
LIMIT 1
"""

GET_OPEN_POSITIONS_LIST = f"""
SELECT {_POS_COLS}
FROM flippening_auto_positions
WHERE status IN ('open', 'exit_pending', 'exit_failed')
ORDER BY opened_at ASC
"""

GET_EXIT_PENDING_POSITIONS = f"""
SELECT {_POS_COLS}
FROM flippening_auto_positions
WHERE status = 'exit_pending'
ORDER BY opened_at ASC
"""

ABANDON_EXPIRED_POSITIONS = """
UPDATE flippening_auto_positions
SET status = 'abandoned',
    exit_reason = 'hold_time_exceeded',
    closed_at = NOW()
WHERE status IN ('open', 'exit_failed')
  AND max_hold_minutes IS NOT NULL
  AND opened_at + (max_hold_minutes || ' minutes')::INTERVAL < NOW()
RETURNING id, arb_id, market_id, market_title, max_hold_minutes,
          EXTRACT(EPOCH FROM (NOW() - opened_at)) / 60 AS held_minutes
"""

GET_ORPHANED_POSITIONS = """
SELECT p.id, p.arb_id, p.market_id, p.token_id, p.side,
       p.size_contracts, p.entry_price, p.entry_order_id,
       p.status, p.opened_at, p.max_hold_minutes,
       COALESCE(NULLIF(p.market_title, ''), e.market_title, '') AS market_title,
       COALESCE(NULLIF(p.market_slug, ''), '') AS market_slug
FROM flippening_auto_positions p
LEFT JOIN LATERAL (
    SELECT market_title FROM flippening_events
    WHERE market_id = p.market_id
    ORDER BY detected_at DESC LIMIT 1
) e ON true
WHERE p.status IN ('open', 'exit_pending', 'exit_failed')
ORDER BY p.opened_at ASC
"""
