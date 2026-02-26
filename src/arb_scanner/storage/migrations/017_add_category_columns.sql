-- Migration 017: Add category columns for event market reversion support

ALTER TABLE flippening_baselines
    ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS category_type TEXT NOT NULL DEFAULT 'sport',
    ADD COLUMN IF NOT EXISTS baseline_strategy TEXT NOT NULL DEFAULT 'first_price';

UPDATE flippening_baselines SET category = sport WHERE category = '';

ALTER TABLE flippening_events
    ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS category_type TEXT NOT NULL DEFAULT 'sport';

UPDATE flippening_events SET category = sport WHERE category = '';

CREATE INDEX IF NOT EXISTS idx_flip_events_category
    ON flippening_events (category, detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_flip_baselines_category
    ON flippening_baselines (category);
