-- Add category columns to execution_tickets for filtering.
ALTER TABLE execution_tickets
    ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS category_type TEXT NOT NULL DEFAULT '';

-- Action log for ticket lifecycle tracking.
CREATE TABLE IF NOT EXISTS flippening_ticket_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actual_entry_price DECIMAL,
    actual_size_usd DECIMAL,
    actual_exit_price DECIMAL,
    actual_pnl DECIMAL,
    slippage DECIMAL,
    notes TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for efficient lookups.
CREATE INDEX IF NOT EXISTS idx_ticket_actions_ticket_id
    ON flippening_ticket_actions (ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_actions_created_at
    ON flippening_ticket_actions (created_at);
CREATE INDEX IF NOT EXISTS idx_execution_tickets_status_created
    ON execution_tickets (status, created_at);
CREATE INDEX IF NOT EXISTS idx_execution_tickets_category
    ON execution_tickets (category);

-- Backfill category from flippening_events where ticket_type = 'flippening'.
UPDATE execution_tickets t
SET category = COALESCE(e.category, ''),
    category_type = COALESCE(e.category_type, '')
FROM flippening_events e
WHERE t.arb_id = e.id
  AND t.ticket_type = 'flippening'
  AND t.category = '';
