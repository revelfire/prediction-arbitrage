-- Drop the FK constraint so flippening tickets can use event_id as arb_id.
ALTER TABLE execution_tickets
    DROP CONSTRAINT IF EXISTS execution_tickets_arb_id_fkey;

-- Add ticket_type column to distinguish arb vs flippening tickets.
ALTER TABLE execution_tickets
    ADD COLUMN IF NOT EXISTS ticket_type TEXT NOT NULL DEFAULT 'arbitrage';
