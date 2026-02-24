CREATE TABLE IF NOT EXISTS arb_opportunities (
    id TEXT PRIMARY KEY,
    poly_event_id TEXT NOT NULL,
    kalshi_event_id TEXT NOT NULL,
    buy_venue TEXT NOT NULL,
    sell_venue TEXT NOT NULL,
    cost_per_contract DECIMAL NOT NULL,
    gross_profit DECIMAL NOT NULL,
    net_profit DECIMAL NOT NULL,
    net_spread_pct DECIMAL NOT NULL,
    max_size DECIMAL NOT NULL,
    annualized_return DECIMAL,
    depth_risk BOOLEAN NOT NULL DEFAULT FALSE,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_arb_detected ON arb_opportunities (detected_at DESC);
