-- Migration 010: Create trend_alerts table for audit trail of trend-based alerts.

CREATE TABLE IF NOT EXISTS trend_alerts (
    id              BIGSERIAL PRIMARY KEY,
    alert_type      TEXT NOT NULL,
    poly_event_id   TEXT,
    kalshi_event_id TEXT,
    spread_before   NUMERIC(10,6),
    spread_after    NUMERIC(10,6),
    message         TEXT NOT NULL,
    dispatched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trend_alerts_dispatched
    ON trend_alerts (dispatched_at DESC);

CREATE INDEX IF NOT EXISTS idx_trend_alerts_type
    ON trend_alerts (alert_type, dispatched_at DESC);
