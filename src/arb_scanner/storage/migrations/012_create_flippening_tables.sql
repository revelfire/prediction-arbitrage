-- Migration 012: Create flippening engine tables
-- Supports the mean reversion / flippening detection engine for live sports.

-- Baseline odds captured at game start
CREATE TABLE IF NOT EXISTS flippening_baselines (
    id              BIGSERIAL PRIMARY KEY,
    market_id       TEXT NOT NULL,
    token_id        TEXT NOT NULL,
    baseline_yes    NUMERIC(10,6) NOT NULL,
    baseline_no     NUMERIC(10,6) NOT NULL,
    sport           TEXT NOT NULL,
    game_start_time TIMESTAMPTZ,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    late_join       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_flip_baselines_market
    ON flippening_baselines (market_id);

-- Detected flippening events (emotional overreaction spikes)
CREATE TABLE IF NOT EXISTS flippening_events (
    id              TEXT PRIMARY KEY,
    market_id       TEXT NOT NULL,
    market_title    TEXT NOT NULL,
    baseline_yes    NUMERIC(10,6) NOT NULL,
    spike_price     NUMERIC(10,6) NOT NULL,
    spike_magnitude NUMERIC(10,6) NOT NULL,
    spike_direction TEXT NOT NULL,
    confidence      NUMERIC(10,6) NOT NULL,
    sport           TEXT NOT NULL,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flip_events_detected
    ON flippening_events (detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_flip_events_sport
    ON flippening_events (sport, detected_at DESC);

-- Entry and exit signals
CREATE TABLE IF NOT EXISTS flippening_signals (
    id              TEXT PRIMARY KEY,
    event_id        TEXT NOT NULL REFERENCES flippening_events(id),
    signal_type     TEXT NOT NULL,
    side            TEXT NOT NULL,
    price           NUMERIC(10,6) NOT NULL,
    target_exit     NUMERIC(10,6),
    stop_loss       NUMERIC(10,6),
    suggested_size  NUMERIC(12,2),
    exit_reason     TEXT,
    realized_pnl    NUMERIC(12,6),
    hold_minutes    NUMERIC(10,2),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flip_signals_event
    ON flippening_signals (event_id);

CREATE INDEX IF NOT EXISTS idx_flip_signals_created
    ON flippening_signals (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_flip_signals_type
    ON flippening_signals (signal_type, created_at DESC);
