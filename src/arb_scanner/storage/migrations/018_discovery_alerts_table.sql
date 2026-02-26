-- 018: Discovery alerts table for degradation tracking
CREATE TABLE IF NOT EXISTS flippening_discovery_alerts (
    id SERIAL PRIMARY KEY,
    alert_text TEXT NOT NULL,
    category TEXT DEFAULT '',
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_disc_alerts_created
    ON flippening_discovery_alerts(created_at DESC);
