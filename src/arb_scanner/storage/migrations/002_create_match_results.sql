CREATE TABLE IF NOT EXISTS match_results (
    poly_event_id TEXT NOT NULL,
    kalshi_event_id TEXT NOT NULL,
    match_confidence DOUBLE PRECISION NOT NULL,
    resolution_equivalent BOOLEAN NOT NULL,
    resolution_risks JSONB NOT NULL DEFAULT '[]',
    safe_to_arb BOOLEAN NOT NULL,
    reasoning TEXT NOT NULL DEFAULT '',
    matched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ttl_expires TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (poly_event_id, kalshi_event_id)
);
CREATE INDEX IF NOT EXISTS idx_match_ttl ON match_results (ttl_expires);
