CREATE TABLE IF NOT EXISTS flippening_discovery_health (
    id BIGSERIAL PRIMARY KEY,
    cycle_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_scanned INT NOT NULL,
    sports_found INT NOT NULL,
    hit_rate DOUBLE PRECISION NOT NULL,
    by_sport JSONB NOT NULL DEFAULT '{}',
    overrides_applied INT NOT NULL DEFAULT 0,
    exclusions_applied INT NOT NULL DEFAULT 0,
    unclassified_candidates INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_discovery_health_ts
    ON flippening_discovery_health (cycle_timestamp DESC);
