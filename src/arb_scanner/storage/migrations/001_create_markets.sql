CREATE TABLE IF NOT EXISTS markets (
    venue TEXT NOT NULL,
    event_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    resolution_criteria TEXT NOT NULL DEFAULT '',
    yes_bid DECIMAL NOT NULL,
    yes_ask DECIMAL NOT NULL,
    no_bid DECIMAL NOT NULL,
    no_ask DECIMAL NOT NULL,
    volume_24h DECIMAL NOT NULL DEFAULT 0,
    expiry TIMESTAMPTZ,
    fees_pct DECIMAL NOT NULL DEFAULT 0,
    fee_model TEXT NOT NULL DEFAULT 'on_winnings',
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_data JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (venue, event_id)
);
