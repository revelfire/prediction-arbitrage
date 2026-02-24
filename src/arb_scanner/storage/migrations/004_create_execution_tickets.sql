CREATE TABLE IF NOT EXISTS execution_tickets (
    arb_id TEXT PRIMARY KEY REFERENCES arb_opportunities(id),
    leg_1 JSONB NOT NULL,
    leg_2 JSONB NOT NULL,
    expected_cost DECIMAL NOT NULL,
    expected_profit DECIMAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
