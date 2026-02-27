-- 020: Execution order tracking for one-click execution
-- Stores per-leg orders and aggregate execution results.

CREATE TABLE IF NOT EXISTS execution_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    arb_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    venue_order_id TEXT,
    side TEXT NOT NULL,
    requested_price NUMERIC NOT NULL,
    fill_price NUMERIC,
    size_usd NUMERIC NOT NULL,
    size_contracts INTEGER,
    status TEXT NOT NULL DEFAULT 'submitting',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_execution_orders_arb_id
    ON execution_orders (arb_id);
CREATE INDEX IF NOT EXISTS idx_execution_orders_status
    ON execution_orders (status);
CREATE INDEX IF NOT EXISTS idx_execution_orders_created
    ON execution_orders (created_at);

CREATE TABLE IF NOT EXISTS execution_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    arb_id TEXT NOT NULL UNIQUE,
    total_cost_usd NUMERIC,
    actual_spread NUMERIC,
    slippage_from_ticket NUMERIC,
    poly_order_id UUID,
    kalshi_order_id UUID,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_execution_results_arb_id
    ON execution_results (arb_id);
