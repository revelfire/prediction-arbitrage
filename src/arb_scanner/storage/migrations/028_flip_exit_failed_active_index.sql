-- Treat exit_failed as active inventory for market-level locking.
DROP INDEX IF EXISTS flippening_auto_positions_market_open;
DROP INDEX IF EXISTS flippening_auto_positions_market_active;

CREATE UNIQUE INDEX IF NOT EXISTS flippening_auto_positions_market_active
    ON flippening_auto_positions (market_id)
    WHERE status IN ('open', 'exit_pending', 'exit_failed');
