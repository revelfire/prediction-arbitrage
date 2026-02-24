CREATE TABLE IF NOT EXISTS scan_logs (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    poly_markets_fetched INTEGER NOT NULL DEFAULT 0,
    kalshi_markets_fetched INTEGER NOT NULL DEFAULT 0,
    candidate_pairs INTEGER NOT NULL DEFAULT 0,
    llm_evaluations INTEGER NOT NULL DEFAULT 0,
    opportunities_found INTEGER NOT NULL DEFAULT 0,
    errors JSONB NOT NULL DEFAULT '[]'
);
