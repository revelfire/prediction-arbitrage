-- Capital manager state persistence for crash recovery
CREATE TABLE IF NOT EXISTS capital_manager_state (
    id          INTEGER PRIMARY KEY DEFAULT 1,
    daily_pnl   NUMERIC NOT NULL DEFAULT 0,
    daily_pnl_date TEXT NOT NULL DEFAULT '',
    last_loss_at TIMESTAMPTZ,
    open_positions JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT single_row CHECK (id = 1)
);

INSERT INTO capital_manager_state (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;
