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
       entry_price, entry_order_id, status, opened_at, max_hold_minutes,
       market_title, market_slug"""

GET_OPEN_POSITION = f"""
SELECT {_POS_COLS}
FROM flippening_auto_positions
WHERE market_id = $1 AND status = 'open'
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
WHERE market_id = $1 AND status = 'open'
"""

MARK_EXIT_FAILED = """
UPDATE flippening_auto_positions
SET status = 'exit_failed'
WHERE market_id = $1 AND status = 'open'
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
WHERE status = 'open'
ORDER BY opened_at ASC
"""

GET_ORPHANED_POSITIONS = f"""
SELECT {_POS_COLS}
FROM flippening_auto_positions
WHERE status = 'open'
ORDER BY opened_at ASC
"""
