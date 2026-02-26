CREATE TABLE IF NOT EXISTS ws_telemetry (
    id             BIGSERIAL PRIMARY KEY,
    snapshot_time  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    messages_received   INT NOT NULL,
    messages_parsed     INT NOT NULL,
    messages_failed     INT NOT NULL,
    messages_ignored    INT NOT NULL,
    schema_match_rate   DOUBLE PRECISION NOT NULL,
    book_cache_hit_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    connection_state    TEXT NOT NULL DEFAULT 'unknown'
);

CREATE INDEX IF NOT EXISTS idx_ws_telemetry_snapshot_time
    ON ws_telemetry (snapshot_time DESC);
