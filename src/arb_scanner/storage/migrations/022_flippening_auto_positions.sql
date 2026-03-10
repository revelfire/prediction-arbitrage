-- Tracks open Polymarket positions entered via auto-execution for flippening trades
CREATE TABLE IF NOT EXISTS flippening_auto_positions (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    arb_id          TEXT NOT NULL,
    market_id       TEXT NOT NULL,
    token_id        TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('yes', 'no')),
    size_contracts  INTEGER NOT NULL,
    entry_price     NUMERIC(10, 6) NOT NULL,
    entry_order_id  TEXT DEFAULT '',
    exit_order_id   TEXT DEFAULT '',
    exit_price      NUMERIC(10, 6),
    realized_pnl    NUMERIC(10, 4),
    status          TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'closed', 'exit_failed', 'abandoned')),
    exit_reason     TEXT DEFAULT '',
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ
);

-- Enforce at most one open position per market
CREATE UNIQUE INDEX IF NOT EXISTS flippening_auto_positions_market_open
    ON flippening_auto_positions (market_id)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_flip_auto_pos_status
    ON flippening_auto_positions (status);

CREATE INDEX IF NOT EXISTS idx_flip_auto_pos_arb_id
    ON flippening_auto_positions (arb_id);
