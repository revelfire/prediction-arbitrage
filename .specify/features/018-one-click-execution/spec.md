# 018 — One-Click Execution

## Overview

Enable operators to execute both legs of an arbitrage trade directly from the dashboard with a single click. Integrates with Polymarket's CLOB API (via `py-clob-client` SDK) and Kalshi's trading API (REST with RSA-PSS auth) to place limit orders on both venues simultaneously.

## Motivation

Currently the system detects arbitrage opportunities and presents execution tickets, but the operator must manually navigate to each venue, find the right market, and place orders by hand. This friction window — typically 30-90 seconds — often exceeds the lifespan of the opportunity. One-click execution from the ticket detail modal eliminates this delay while keeping the human in the loop: the operator reviews the opportunity, confirms the parameters, and clicks once to execute both legs.

## Constitution Amendment

**Principle I** currently reads:

> The system MUST produce execution tickets but MUST NEVER place orders or interact with trading APIs.

This feature amends Principle I to:

> The system MUST produce execution tickets. Order placement MUST require explicit operator initiation (click or API call with confirmation). No orders may be placed without a prior operator action in the same session. The system MUST NOT place orders autonomously (see feature 019 for opt-in auto-execution).

**Rationale**: The original principle was appropriate for a detection-only MVP. As the system matures, operator-initiated execution is the natural next step — it preserves human-in-the-loop oversight while eliminating the manual friction that makes opportunities expire before they can be captured.

**Version bump**: 1.1.0 → 2.0.0 (MAJOR — principle change).

## Functional Requirements

### FR-001: Venue Credential Management

Secure storage and validation of venue trading credentials:

- **Polymarket**: Ethereum private key (for CLOB signing), USDC balance check on Polygon.
- **Kalshi**: RSA private key (PEM format) for RSA-PSS request signing, API key ID.
- Credentials stored as environment variables (`POLY_PRIVATE_KEY`, `KALSHI_API_KEY_ID`, `KALSHI_RSA_PRIVATE_KEY_PATH`), never in config files or database.
- On dashboard load, `GET /api/execution/status` returns which venues have valid credentials configured (boolean flags, never the credentials themselves).
- Credential validation on startup: attempt a read-only API call (Polymarket: get balance; Kalshi: get account) and log success/failure.

### FR-002: Pre-Execution Validation

Before placing any order, the system validates:

- **Credential check**: Both venue credentials are configured and valid.
- **Balance check**: Sufficient USDC (Polymarket) and USD (Kalshi) to cover the trade at the requested size.
- **Price staleness**: Current venue prices are less than 30 seconds old (re-fetch if stale).
- **Spread check**: The arbitrage spread still exceeds the minimum threshold after re-fetching live prices.
- **Size limits**: Requested size does not exceed the per-trade maximum from config.
- **Slippage guard**: If the current best price has moved more than `max_slippage_pct` (default 2%) from the ticket's original price, warn the operator and require re-confirmation.

Validation results displayed in the ticket detail modal before the Execute button becomes active.

### FR-003: Order Placement — Polymarket

Place a limit order on Polymarket's CLOB:

- Use `py-clob-client` SDK (`ClobClient.create_and_post_order()`).
- Order type: GTC (Good Till Cancelled) limit order at the current best bid/ask.
- Sign with operator's Ethereum private key (EIP-712 typed data signature on Polygon).
- Token: USDC on Polygon network.
- Map ticket leg to correct CLOB token ID and side (BUY YES or BUY NO).
- Capture order ID from response for status tracking.

### FR-004: Order Placement — Kalshi

Place a limit order on Kalshi's exchange:

- `POST /trade-api/v2/portfolio/orders` with RSA-PSS signed request.
- Auth: RSA-PSS signature over `timestamp + method + path` (timestamp in milliseconds).
- Order type: limit order, `action: "buy"`, specify `yes_price` or `no_price` in cents.
- Use `count` for number of contracts (derive from dollar size / price).
- Map ticket leg to correct Kalshi ticker and side.
- Capture order ID from response for status tracking.

### FR-005: Execution Workflow UI

In the ticket detail modal, add an "Execute Trade" section:

1. **Pre-flight panel**: Shows validation results (balances, price freshness, spread status) with green/red indicators.
2. **Size input**: Pre-filled with ticket's suggested size. Operator can adjust. Shown in USD.
3. **Price summary**: Current best prices on both venues, expected cost, expected profit after fees.
4. **Execute button**: Disabled until all validations pass. Single click places both orders.
5. **Execution progress**: After click, show real-time status for each leg (Submitting → Confirmed → Filled / Failed).
6. **Result summary**: Final order IDs, fill prices, actual cost, actual spread captured.

### FR-006: Execution Record Persistence

Store execution results in the database:

- Link to the originating execution ticket (`arb_id`).
- Per-leg: venue, order ID, side, requested price, fill price, size, status, timestamp.
- Aggregate: total cost, actual spread captured, slippage from ticket price.
- Status lifecycle: `submitting` → `submitted` → `filled` / `partially_filled` / `failed` / `cancelled`.

### FR-007: Execution Status API

- `GET /api/execution/status` — Venue credential status and account balances.
- `POST /api/execution/execute/{arb_id}` — Place both legs. Body: `{ "size_usd": 50.0 }`.
- `GET /api/execution/orders/{arb_id}` — Execution result for a ticket.
- `DELETE /api/execution/orders/{order_id}` — Cancel a pending order on a venue.

### FR-008: Error Handling and Partial Fills

- If one leg fails after the other succeeds, immediately flag as "partial execution" with a dashboard alert.
- Do NOT auto-cancel the successful leg (operator decides).
- Log the full error response from the failed venue.
- Partial executions show a prominent warning banner in the ticket detail.
- Operator can manually cancel the successful leg via the cancel endpoint.

### FR-009: Capital-Aware Position Sizing

Operator-configurable capital management that prevents oversized trades:

- **Percentage-of-balance sizing**: Default trade size computed as a percentage of the smaller venue balance (default 2%). Operator can override in the preflight panel up to the hard cap.
- **Per-venue allocation**: Maximum percentage of each venue's balance deployable in a single trade (default 5%). Prevents draining one venue in a single trade.
- **Total exposure cap**: Maximum USD deployed across all open/pending execution orders (default 25% of total portfolio). New executions blocked when cap is reached.
- **Minimum reserve**: Always keep a minimum balance on each venue (default $50) to cover gas/fees and allow partial exits. Execution blocked if trade would drop below reserve.
- **Size display**: Preflight panel shows suggested size (% of balance), max allowable size, and remaining capacity before exposure cap.

### FR-010: Liquidity Validation

Before execution, validate that order book depth can absorb the requested size without excessive slippage:

- **Book depth check**: Fetch full order book (not just top-of-book) for both legs. Walk the book to estimate the volume-weighted average price (VWAP) for the requested size.
- **Estimated slippage**: Compare VWAP to the top-of-book price. Display in preflight panel.
- **Depth warning**: If estimated slippage exceeds `max_slippage_pct`, show a warning and reduce suggested size to the amount the book can absorb within slippage tolerance.
- **Minimum depth**: Reject execution if either leg's book has fewer than `min_book_depth_contracts` (default 20) contracts available within the slippage band.
- **Depth display**: Show available depth at the target price band for each leg in the preflight panel.

### FR-011: Portfolio Exposure Limits

Guard rails to prevent capital concentration and runaway losses:

- **Daily loss limit**: Maximum realized + unrealized loss per calendar day (UTC). Default $100. When breached, execution is blocked until next UTC day with a dashboard warning.
- **Max open positions**: Maximum number of concurrent open execution orders (default 5). New executions blocked when limit reached.
- **Per-market concentration**: Maximum USD exposure to any single market/event pair (default 10% of total portfolio). Prevents doubling down on the same arb.
- **Cooldown after loss**: After a losing trade (negative P&L on fill), enforce a configurable cooldown period (default 5 minutes) before allowing the next execution. Prevents revenge trading.

## Database Changes

### New table: `execution_orders`

```sql
CREATE TABLE execution_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    arb_id TEXT NOT NULL REFERENCES execution_tickets(arb_id),
    venue TEXT NOT NULL,              -- 'polymarket' or 'kalshi'
    venue_order_id TEXT,              -- order ID from venue API
    side TEXT NOT NULL,               -- 'buy_yes', 'buy_no', 'sell_yes', 'sell_no'
    requested_price NUMERIC NOT NULL,
    fill_price NUMERIC,
    size_usd NUMERIC NOT NULL,
    size_contracts INTEGER,
    status TEXT NOT NULL DEFAULT 'submitting',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### New table: `execution_results`

```sql
CREATE TABLE execution_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    arb_id TEXT NOT NULL UNIQUE REFERENCES execution_tickets(arb_id),
    total_cost_usd NUMERIC,
    actual_spread NUMERIC,
    slippage_from_ticket NUMERIC,
    poly_order_id UUID REFERENCES execution_orders(id),
    kalshi_order_id UUID REFERENCES execution_orders(id),
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, complete, partial, failed
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## Configuration

```yaml
execution:
  enabled: false                     # Master kill switch (default off)
  max_size_usd: 100.0               # Per-trade hard cap
  max_slippage_pct: 0.02            # 2% slippage guard
  price_staleness_seconds: 30       # Max age of prices before re-fetch
  # Capital preservation
  pct_of_balance: 0.02              # Default size = 2% of smaller venue balance
  max_pct_per_venue: 0.05           # Max 5% of a single venue's balance per trade
  max_exposure_pct: 0.25            # Max 25% of total portfolio deployed at once
  min_reserve_usd: 50.0             # Keep at least $50 per venue for gas/fees
  daily_loss_limit_usd: 100.0       # Stop trading after $100 daily loss
  max_open_positions: 5             # Max concurrent open orders
  max_per_market_pct: 0.10          # Max 10% of portfolio on one market
  cooldown_after_loss_seconds: 300  # 5 min cooldown after a losing trade
  # Liquidity
  min_book_depth_contracts: 20      # Reject if fewer than 20 contracts in band
  # Venue configs
  polymarket:
    chain_id: 137                    # Polygon mainnet
    clob_api_url: "https://clob.polymarket.com"
    usdc_contract: "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
  kalshi:
    api_base_url: "https://trading-api.kalshi.com/trade-api/v2"
```

## Edge Cases

- EC-001: Insufficient balance on one venue → Block execution, show which venue is short and by how much.
- EC-002: Price moved beyond slippage guard → Show warning modal with old vs. new prices, require re-confirmation.
- EC-003: One leg succeeds, other fails → "Partial execution" alert. Do not auto-cancel. Operator decides.
- EC-004: Network timeout during order placement → Retry once with idempotency key. If still fails, mark as "unknown" and prompt operator to check venue directly.
- EC-005: Venue API rate limited → Queue and retry with backoff. Show "Rate limited, retrying..." status.
- EC-006: Order fills at different price than requested (market moved) → Record actual fill price, compute actual spread, flag if spread went negative.
- EC-007: Credentials not configured → "Execute" button disabled with tooltip explaining which credentials are missing.
- EC-008: WebSocket price feed stale → Re-fetch via REST before allowing execution.
- EC-009: Trade would exceed exposure cap → Block execution, show current exposure and remaining capacity.
- EC-010: Trade would breach daily loss limit → Block execution, show daily P&L and limit.
- EC-011: Order book too thin for requested size → Reduce suggested size to what the book can absorb, warn operator.
- EC-012: Trade on same market as existing open position → Block or warn based on per-market concentration limit.
- EC-013: Cooldown active after losing trade → Block execution, show countdown timer.
- EC-014: Trade would drop venue balance below minimum reserve → Block, show reserve requirement.

## Success Criteria

- SC-001: Both orders placed within 2 seconds of operator click.
- SC-002: Pre-flight validation completes in under 3 seconds (including balance checks).
- SC-003: Partial execution detected and flagged within 5 seconds.
- SC-004: Execution records persist correctly and display in ticket detail after page refresh.
- SC-005: Slippage guard triggers when price moves beyond threshold.
- SC-006: Credentials never appear in logs, API responses, or database.
- SC-007: All quality gates pass.
- SC-008: Suggested size respects percentage-of-balance and never exceeds per-venue cap.
- SC-009: Execution blocked when total exposure exceeds cap, with clear message.
- SC-010: Liquidity validation walks the book and shows estimated slippage before execution.
- SC-011: Daily loss limit blocks execution after threshold with reset at UTC midnight.

## Dependencies

- `py-clob-client` — Polymarket CLOB SDK (pip/uv installable).
- `cryptography` — RSA key loading and PSS signing for Kalshi auth.
- `web3` / `eth-account` — Ethereum signing for Polymarket (may be bundled in py-clob-client).

## Out of Scope

- Automated/unattended order placement (see feature 019).
- Order monitoring after fill (position tracking, exit execution).
- Multi-leg strategies beyond the two-venue arb (e.g., hedging).
- Fiat on-ramp / USDC bridging.
- Kelly criterion or dynamic edge-based sizing (fixed-fraction only in v1).
