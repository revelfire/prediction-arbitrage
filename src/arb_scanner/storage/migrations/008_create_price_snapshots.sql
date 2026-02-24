CREATE TABLE IF NOT EXISTS market_price_snapshots (
    id             BIGSERIAL PRIMARY KEY,
    venue          TEXT NOT NULL,
    event_id       TEXT NOT NULL,
    yes_bid        DECIMAL(10,4) NOT NULL,
    yes_ask        DECIMAL(10,4) NOT NULL,
    no_bid         DECIMAL(10,4) NOT NULL,
    no_ask         DECIMAL(10,4) NOT NULL,
    volume_24h     DECIMAL(16,2) NOT NULL DEFAULT 0,
    snapshotted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_snapshots_venue_event_time
    ON market_price_snapshots (venue, event_id, snapshotted_at DESC);
