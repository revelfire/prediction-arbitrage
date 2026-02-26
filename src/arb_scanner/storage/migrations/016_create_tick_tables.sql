-- Tick capture tables for backtesting & historical replay (012)

CREATE TABLE IF NOT EXISTS flippening_price_ticks (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    yes_bid NUMERIC(10,6) NOT NULL,
    yes_ask NUMERIC(10,6) NOT NULL,
    no_bid NUMERIC(10,6) NOT NULL,
    no_ask NUMERIC(10,6) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    synthetic_spread BOOLEAN NOT NULL DEFAULT FALSE,
    book_depth_bids INT NOT NULL DEFAULT 0,
    book_depth_asks INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ticks_market_ts
    ON flippening_price_ticks (market_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_ticks_ts
    ON flippening_price_ticks (timestamp);

CREATE TABLE IF NOT EXISTS flippening_baseline_drifts (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT NOT NULL,
    old_yes NUMERIC(10,6) NOT NULL,
    new_yes NUMERIC(10,6) NOT NULL,
    drift_reason TEXT NOT NULL DEFAULT 'gradual',
    drifted_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drifts_market_ts
    ON flippening_baseline_drifts (market_id, drifted_at);
