-- Trade history tables for imported trades and backtesting analysis

CREATE TABLE IF NOT EXISTS imported_trades (
    id              BIGSERIAL PRIMARY KEY,
    market_name     TEXT NOT NULL,
    action          TEXT NOT NULL,
    usdc_amount     NUMERIC NOT NULL,
    token_amount    NUMERIC NOT NULL,
    token_name      TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    tx_hash         TEXT NOT NULL UNIQUE,
    condition_id    TEXT,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_imported_trades_market
    ON imported_trades (market_name);
CREATE INDEX IF NOT EXISTS idx_imported_trades_timestamp
    ON imported_trades (timestamp);
CREATE INDEX IF NOT EXISTS idx_imported_trades_action
    ON imported_trades (action);

CREATE TABLE IF NOT EXISTS trade_positions (
    id              BIGSERIAL PRIMARY KEY,
    market_name     TEXT NOT NULL,
    token_name      TEXT NOT NULL,
    cost_basis      NUMERIC NOT NULL DEFAULT 0,
    tokens_held     NUMERIC NOT NULL DEFAULT 0,
    avg_entry_price NUMERIC NOT NULL DEFAULT 0,
    realized_pnl    NUMERIC NOT NULL DEFAULT 0,
    unrealized_pnl  NUMERIC NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'closed', 'resolved')),
    fee_paid        NUMERIC NOT NULL DEFAULT 0,
    first_trade_at  TIMESTAMPTZ NOT NULL,
    last_trade_at   TIMESTAMPTZ NOT NULL,
    UNIQUE (market_name, token_name)
);

CREATE TABLE IF NOT EXISTS category_performance (
    id                      BIGSERIAL PRIMARY KEY,
    category                TEXT NOT NULL UNIQUE,
    win_rate                DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_pnl                 DOUBLE PRECISION NOT NULL DEFAULT 0,
    trade_count             INTEGER NOT NULL DEFAULT 0,
    total_pnl               DOUBLE PRECISION NOT NULL DEFAULT 0,
    profit_factor           DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_hold_minutes        DOUBLE PRECISION NOT NULL DEFAULT 0,
    signal_alignment_rate   DOUBLE PRECISION NOT NULL DEFAULT 0,
    aligned_win_rate        DOUBLE PRECISION NOT NULL DEFAULT 0,
    contrary_win_rate       DOUBLE PRECISION NOT NULL DEFAULT 0,
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS optimal_params (
    id                  BIGSERIAL PRIMARY KEY,
    category            TEXT NOT NULL,
    param_name          TEXT NOT NULL,
    optimal_value       DOUBLE PRECISION NOT NULL,
    win_rate_at_optimal DOUBLE PRECISION NOT NULL,
    sweep_date          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (category, param_name)
);
