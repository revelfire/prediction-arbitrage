"""SQL query constants for trade history and backtesting."""

INSERT_TRADE = """
INSERT INTO imported_trades (
    market_name, action, usdc_amount, token_amount,
    token_name, timestamp, tx_hash, condition_id
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (tx_hash) DO NOTHING
"""

GET_TRADES_BASE = """
SELECT market_name, action, usdc_amount, token_amount,
       token_name, timestamp, tx_hash, condition_id, imported_at
FROM imported_trades
"""

UPSERT_POSITION = """
INSERT INTO trade_positions (
    market_name, token_name, cost_basis, tokens_held,
    avg_entry_price, realized_pnl, unrealized_pnl,
    status, fee_paid, first_trade_at, last_trade_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT (market_name, token_name) DO UPDATE SET
    cost_basis = EXCLUDED.cost_basis,
    tokens_held = EXCLUDED.tokens_held,
    avg_entry_price = EXCLUDED.avg_entry_price,
    realized_pnl = EXCLUDED.realized_pnl,
    unrealized_pnl = EXCLUDED.unrealized_pnl,
    status = EXCLUDED.status,
    fee_paid = EXCLUDED.fee_paid,
    first_trade_at = EXCLUDED.first_trade_at,
    last_trade_at = EXCLUDED.last_trade_at
"""

GET_POSITIONS_BASE = """
SELECT market_name, token_name, cost_basis, tokens_held,
       avg_entry_price, realized_pnl, unrealized_pnl,
       status, fee_paid, first_trade_at, last_trade_at
FROM trade_positions
"""

GET_PORTFOLIO_AGGREGATE = """
SELECT
    COALESCE(SUM(realized_pnl), 0) AS total_realized_pnl,
    COALESCE(SUM(unrealized_pnl), 0) AS total_unrealized_pnl,
    COALESCE(SUM(fee_paid), 0) AS total_fees,
    COALESCE(SUM(cost_basis), 0) AS total_capital_deployed,
    COUNT(*) AS position_count,
    COUNT(*) FILTER (WHERE realized_pnl > 0) AS win_count,
    COUNT(*) FILTER (WHERE realized_pnl <= 0 AND status != 'open') AS loss_count
FROM trade_positions
"""

GET_DAILY_PNL = """
SELECT DATE(last_trade_at) AS trade_date,
       SUM(realized_pnl) AS daily_pnl
FROM trade_positions
WHERE realized_pnl != 0
GROUP BY DATE(last_trade_at)
ORDER BY trade_date
"""

GET_CAPITAL_FLOWS = """
SELECT market_name, action, usdc_amount, token_amount,
       token_name, timestamp, tx_hash, condition_id, imported_at
FROM imported_trades
WHERE action IN ('Deposit', 'Withdraw')
ORDER BY timestamp
"""

UPSERT_CATEGORY_PERFORMANCE = """
INSERT INTO category_performance (
    category, win_rate, avg_pnl, trade_count, total_pnl,
    profit_factor, avg_hold_minutes, signal_alignment_rate,
    aligned_win_rate, contrary_win_rate, computed_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT (category) DO UPDATE SET
    win_rate = EXCLUDED.win_rate,
    avg_pnl = EXCLUDED.avg_pnl,
    trade_count = EXCLUDED.trade_count,
    total_pnl = EXCLUDED.total_pnl,
    profit_factor = EXCLUDED.profit_factor,
    avg_hold_minutes = EXCLUDED.avg_hold_minutes,
    signal_alignment_rate = EXCLUDED.signal_alignment_rate,
    aligned_win_rate = EXCLUDED.aligned_win_rate,
    contrary_win_rate = EXCLUDED.contrary_win_rate,
    computed_at = EXCLUDED.computed_at
"""

GET_CATEGORY_PERFORMANCE = """
SELECT category, win_rate, avg_pnl, trade_count, total_pnl,
       profit_factor, avg_hold_minutes, signal_alignment_rate,
       aligned_win_rate, contrary_win_rate, computed_at
FROM category_performance
ORDER BY category
"""

UPSERT_OPTIMAL_PARAM = """
INSERT INTO optimal_params (
    category, param_name, optimal_value,
    win_rate_at_optimal, sweep_date
) VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (category, param_name) DO UPDATE SET
    optimal_value = EXCLUDED.optimal_value,
    win_rate_at_optimal = EXCLUDED.win_rate_at_optimal,
    sweep_date = EXCLUDED.sweep_date
"""

GET_OPTIMAL_PARAMS = """
SELECT category, param_name, optimal_value,
       win_rate_at_optimal, sweep_date
FROM optimal_params
"""
