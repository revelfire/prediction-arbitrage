-- Add market title and slug to flippening positions for dashboard display
ALTER TABLE flippening_auto_positions ADD COLUMN IF NOT EXISTS market_title TEXT DEFAULT '';
ALTER TABLE flippening_auto_positions ADD COLUMN IF NOT EXISTS market_slug TEXT DEFAULT '';
