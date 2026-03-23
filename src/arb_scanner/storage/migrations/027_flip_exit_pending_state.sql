-- Add explicit exit_pending state for submitted-but-unconfirmed flip exits
ALTER TABLE flippening_auto_positions
    DROP CONSTRAINT IF EXISTS flippening_auto_positions_status_check;

ALTER TABLE flippening_auto_positions
    ADD CONSTRAINT flippening_auto_positions_status_check
    CHECK (status IN ('open', 'exit_pending', 'closed', 'exit_failed', 'abandoned'));

-- Keep one active position per market (open or pending exit)
DROP INDEX IF EXISTS flippening_auto_positions_market_open;
CREATE UNIQUE INDEX IF NOT EXISTS flippening_auto_positions_market_active
    ON flippening_auto_positions (market_id)
    WHERE status IN ('open', 'exit_pending');
