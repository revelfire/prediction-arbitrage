-- Auto-execution pipeline tables
CREATE TABLE IF NOT EXISTS auto_execution_log (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    arb_id TEXT NOT NULL,
    trigger_spread_pct NUMERIC(10,6),
    trigger_confidence NUMERIC(10,6),
    criteria_snapshot JSONB DEFAULT '{}',
    pre_exec_balances JSONB DEFAULT '{}',
    size_usd NUMERIC(12,2),
    critic_verdict JSONB,
    execution_result_id TEXT,
    actual_spread NUMERIC(10,6),
    actual_pnl NUMERIC(12,2),
    slippage NUMERIC(10,6),
    duration_ms INTEGER,
    circuit_breaker_state JSONB DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auto_execution_positions (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    arb_id TEXT NOT NULL,
    poly_market_id TEXT DEFAULT '',
    kalshi_ticker TEXT DEFAULT '',
    entry_spread NUMERIC(10,6),
    entry_cost_usd NUMERIC(12,2),
    current_value_usd NUMERIC(12,2) DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_auto_exec_log_arb_id ON auto_execution_log(arb_id);
CREATE INDEX IF NOT EXISTS idx_auto_exec_log_status ON auto_execution_log(status);
CREATE INDEX IF NOT EXISTS idx_auto_exec_log_created ON auto_execution_log(created_at);
CREATE INDEX IF NOT EXISTS idx_auto_exec_pos_arb_id ON auto_execution_positions(arb_id);
CREATE INDEX IF NOT EXISTS idx_auto_exec_pos_status ON auto_execution_positions(status);
