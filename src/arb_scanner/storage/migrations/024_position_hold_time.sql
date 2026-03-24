-- Add max_hold_minutes to position tables for dashboard hold time display
ALTER TABLE flippening_auto_positions
    ADD COLUMN IF NOT EXISTS max_hold_minutes INTEGER;

ALTER TABLE auto_execution_positions
    ADD COLUMN IF NOT EXISTS max_hold_minutes INTEGER;
