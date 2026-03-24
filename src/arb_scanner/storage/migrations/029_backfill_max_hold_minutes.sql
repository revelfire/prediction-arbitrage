-- Backfill NULL max_hold_minutes with default 45 and add DEFAULT constraint.
-- Fixes bug where positions with NULL max_hold_minutes were never auto-closed.
UPDATE flippening_auto_positions
SET max_hold_minutes = 45
WHERE max_hold_minutes IS NULL;

ALTER TABLE flippening_auto_positions
    ALTER COLUMN max_hold_minutes SET DEFAULT 45;

UPDATE auto_execution_positions
SET max_hold_minutes = 45
WHERE max_hold_minutes IS NULL;

ALTER TABLE auto_execution_positions
    ALTER COLUMN max_hold_minutes SET DEFAULT 45;
