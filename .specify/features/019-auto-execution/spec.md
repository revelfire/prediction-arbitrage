# 019 — Automated Execution Pipeline

## Overview

Enable fully automated trade execution: when the system detects an arbitrage opportunity that exceeds configurable thresholds, it places both legs without operator intervention. Includes size limits, slippage guards, circuit breakers, position caps, and a comprehensive audit trail. Builds on the execution infrastructure from feature 018.

## Motivation

Manual one-click execution (018) still requires the operator to be watching the dashboard. Many arbitrage opportunities appear during off-hours or resolve within seconds — faster than any human can react. Auto-execution captures these fleeting opportunities while maintaining safety through configurable guardrails, hard limits, and circuit breakers that halt trading when conditions deteriorate.

## Constitution Amendment

**Principle I** (as amended by feature 018) reads:

> Order placement MUST require explicit operator initiation. No orders may be placed without a prior operator action in the same session. The system MUST NOT place orders autonomously.

This feature amends Principle I to:

> Order placement MUST default to operator-initiated (one-click). Autonomous execution is available as an **opt-in mode** that MUST be explicitly enabled via configuration (`auto_execution.enabled: true`) AND confirmed via a CLI flag (`--auto-execute`) or dashboard toggle. Auto-execution MUST enforce all configured guardrails (size limits, spread thresholds, circuit breakers, position caps). The operator MUST be able to halt auto-execution instantly via dashboard kill switch or CLI signal.

**Rationale**: Auto-execution is the logical progression from one-click. The key safety property shifts from "human clicks every trade" to "human sets parameters and monitors, system executes within those parameters." All guardrails are defense-in-depth — multiple independent checks must pass before any order is placed.

**Version bump**: 2.0.0 → 3.0.0 (MAJOR — principle change).

## Prerequisites

- Feature 018 (One-Click Execution) must be complete. This feature extends 018's venue clients, credential management, and execution record tables.

## Functional Requirements

### FR-001: Auto-Execution Mode Toggle

The system has three execution modes:

- **Off** (default): Detection only. No orders placed. Status quo behavior.
- **Manual**: One-click execution from dashboard (feature 018).
- **Auto**: System places orders automatically when criteria are met.

Mode is set via:
- `auto_execution.enabled: true` in config.yaml (persistent).
- `--auto-execute` flag on `flip-watch` / `watch` CLI commands (session-level).
- Dashboard toggle (sets a runtime flag, does not modify config file).

All three must agree: config must allow it, CLI must enable it, and dashboard kill switch must be "on". If any is off, auto-execution is disabled.

### FR-002: Execution Criteria

An opportunity triggers auto-execution only when ALL of the following are true:

1. **Spread threshold**: Net spread after fees exceeds `auto_execution.min_spread_pct` (default: 3%).
2. **Confidence threshold**: Match confidence (from Claude semantic matcher) exceeds `auto_execution.min_confidence` (default: 0.90).
3. **Minimum volume**: Both venues show 24h volume above `auto_execution.min_volume_usd` (default: $5,000).
4. **Price freshness**: Prices on both venues are less than `auto_execution.max_price_age_seconds` old (default: 15s).
5. **Not excluded**: Market is not in `auto_execution.excluded_categories` list.
6. **Position cap**: Total open positions do not exceed `auto_execution.max_open_positions` (default: 5).
7. **Daily loss limit**: Cumulative daily realized loss does not exceed `auto_execution.daily_loss_limit_usd` (default: $200).
8. **Circuit breaker**: Not tripped (see FR-005).

### FR-003: Position Sizing

Auto-execution uses a fixed-fraction sizing model:

- Base size: `auto_execution.base_size_usd` (default: $25).
- Scale factor: Multiply base by `spread / min_spread` (wider spread = more conviction), capped at `auto_execution.max_size_usd` (default: $100).
- Per-market cap: No more than `auto_execution.max_per_market_usd` (default: $200) across all positions in the same market.
- Minimum size: Orders below `auto_execution.min_size_usd` (default: $10) are skipped (not worth the fees).

Formula: `size = min(base * (spread / min_spread), max_size)`, subject to per-market and balance caps.

### FR-004: Slippage Protection

Before placing each order:

1. Re-fetch live prices from both venues (REST, not cached WebSocket data).
2. Compare to the price at detection time.
3. If slippage exceeds `auto_execution.max_slippage_pct` (default: 1.5%), abort execution.
4. Use limit orders only (never market orders). Set limit price at `detection_price + max_slippage` to allow small movement.
5. If a limit order does not fill within `auto_execution.fill_timeout_seconds` (default: 60), cancel it.

### FR-005: Circuit Breakers

Three independent circuit breakers that halt all auto-execution when tripped:

1. **Loss circuit breaker**: Triggered when cumulative daily realized loss exceeds `daily_loss_limit_usd`. Resets at midnight UTC.
2. **Failure circuit breaker**: Triggered after `auto_execution.max_consecutive_failures` (default: 3) consecutive order failures. Resets after `auto_execution.failure_cooldown_minutes` (default: 30).
3. **Spread anomaly breaker**: Triggered when a detected spread is more than `auto_execution.anomaly_spread_pct` (default: 15%) — likely a data error or stale price. Resets after manual operator review.

When any breaker trips:
- All pending auto-executions are cancelled.
- Dashboard shows a prominent alert with the reason.
- Webhook alert dispatched to Slack/Discord.
- Operator must acknowledge to resume (for anomaly breaker) or wait for cooldown (for others).

### FR-006: Execution Pipeline

The auto-execution pipeline runs as a background task within `flip-watch` / `watch`:

1. Opportunity detected by arb calculator / flippening engine.
2. Execution criteria check (FR-002) — all must pass.
3. Position sizing (FR-003).
4. Pre-execution validation (same as 018 FR-002: balances, credentials, staleness).
5. Slippage check (FR-004).
6. Place both orders concurrently (asyncio.gather with individual error handling).
7. Record results to `execution_orders` and `execution_results` tables.
8. Dispatch execution notification webhook.
9. If partial fill → flag and alert, do not auto-cancel.
10. Circuit breaker evaluation after each execution.

### FR-007: Audit Trail

Every auto-execution records:

- Trigger: which opportunity, what spread, what criteria values at decision time.
- Pre-execution state: balances, prices, validation results.
- Orders: venue, side, requested price, limit price, fill price, slippage.
- Result: actual spread captured, fees paid, net P&L.
- Duration: time from detection to both fills.
- Circuit breaker state at time of execution.

All records stored in `auto_execution_log` table for post-hoc analysis.

### FR-008: Dashboard Auto-Execution Panel

New dashboard section showing:

- **Mode indicator**: Off / Manual / Auto, with toggle button.
- **Kill switch**: Red button that immediately disables auto-execution.
- **Active guardrails**: Current threshold values and how close to limits.
- **Circuit breaker status**: Green (OK) / Red (Tripped) with reason and reset time.
- **Today's stats**: Executions count, win/loss, total P&L, average slippage.
- **Recent auto-executions**: Last 20 auto-trades with status and P&L.
- **Position summary**: Open positions by market with current value vs. entry.

### FR-009: REST API Endpoints

- `GET /api/auto-execution/status` — Current mode, circuit breaker state, guardrail values.
- `POST /api/auto-execution/enable` — Enable auto-execution (requires valid credentials).
- `POST /api/auto-execution/disable` — Disable auto-execution (kill switch).
- `GET /api/auto-execution/log?limit=50` — Auto-execution audit log.
- `GET /api/auto-execution/positions` — Current open positions.
- `GET /api/auto-execution/stats?days=7` — Performance statistics.
- `POST /api/auto-execution/circuit-breaker/reset` — Manually reset a circuit breaker.

### FR-010: Notification Integration

Auto-execution events dispatched via existing webhook infrastructure:

- **Order placed**: Venue, side, size, price.
- **Order filled**: Fill price, slippage, P&L.
- **Order failed**: Venue, error message.
- **Partial execution**: Which leg succeeded, which failed.
- **Circuit breaker tripped**: Which breaker, reason, when it resets.
- **Daily summary**: Total trades, P&L, win rate, slippage (dispatched at midnight UTC).

## Database Changes

### New table: `auto_execution_log`

```sql
CREATE TABLE auto_execution_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    arb_id TEXT NOT NULL,
    trigger_spread_pct NUMERIC NOT NULL,
    trigger_confidence NUMERIC,
    trigger_volume_poly NUMERIC,
    trigger_volume_kalshi NUMERIC,
    criteria_snapshot JSONB NOT NULL,      -- all criteria values at decision time
    pre_exec_balances JSONB,              -- {polymarket_usdc, kalshi_usd}
    size_usd NUMERIC NOT NULL,
    sizing_rationale TEXT,                 -- "base=$25, scale=1.5x, spread=4.5%"
    execution_result_id UUID REFERENCES execution_results(id),
    actual_spread NUMERIC,
    actual_pnl NUMERIC,
    slippage NUMERIC,
    duration_ms INTEGER,                  -- detection to fill time
    circuit_breaker_state JSONB,          -- state of all breakers at exec time
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### New table: `auto_execution_positions`

```sql
CREATE TABLE auto_execution_positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    arb_id TEXT NOT NULL,
    poly_market_id TEXT,
    kalshi_ticker TEXT,
    entry_spread NUMERIC NOT NULL,
    entry_cost_usd NUMERIC NOT NULL,
    current_value_usd NUMERIC,
    status TEXT NOT NULL DEFAULT 'open',  -- open, closed, expired
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ
);
```

## Configuration

```yaml
auto_execution:
  enabled: false                          # Master switch (default off)
  min_spread_pct: 0.03                    # 3% minimum spread to trigger
  min_confidence: 0.90                    # Claude match confidence floor
  min_volume_usd: 5000                    # 24h volume floor per venue
  max_price_age_seconds: 15               # Prices must be this fresh
  excluded_categories: []                 # Categories to never auto-trade

  # Sizing
  base_size_usd: 25.0                    # Base position size
  max_size_usd: 100.0                    # Maximum per-trade
  min_size_usd: 10.0                     # Skip if below this
  max_per_market_usd: 200.0              # Cap per market across positions

  # Protection
  max_slippage_pct: 0.015                # 1.5% slippage abort
  fill_timeout_seconds: 60               # Cancel unfilled orders after
  max_open_positions: 5                  # Position cap
  daily_loss_limit_usd: 200.0            # Daily loss circuit breaker

  # Circuit breakers
  max_consecutive_failures: 3             # Failure breaker threshold
  failure_cooldown_minutes: 30            # Failure breaker reset time
  anomaly_spread_pct: 0.15               # Anomaly breaker threshold (15%)
```

## Edge Cases

- EC-001: Auto-execute and manual execute on same ticket simultaneously → Acquire a per-ticket lock. First execution wins, second gets "already executing" error.
- EC-002: Both venues rate-limited simultaneously → Trip failure circuit breaker after max retries. Alert operator.
- EC-003: Market resolves between detection and fill → The limit order sits unfilled. Cancel after `fill_timeout_seconds`. Record as "market_resolved" in audit log.
- EC-004: Config changed while auto-execution is running → New config values take effect on the next execution cycle (not mid-execution).
- EC-005: Dashboard kill switch hit during active execution → Complete the in-flight execution (do not cancel mid-order), then halt. No new executions.
- EC-006: Balance drops below minimum during a batch of executions → Skip remaining opportunities, log "insufficient_balance". Do not trip circuit breaker (not a failure).
- EC-007: Daily loss limit hit intra-day → Immediately halt all auto-execution. Alert via webhook. Resume next day at midnight UTC.
- EC-008: Anomaly spread detected (>15%) → Halt auto-execution. Require manual operator acknowledgment to resume. Likely data error.
- EC-009: WebSocket disconnects during auto-execution mode → Fall back to REST polling for price validation. Log degraded mode. Do not halt auto-execution unless prices become stale.

## Success Criteria

- SC-001: Auto-execution places both legs within 3 seconds of opportunity detection.
- SC-002: Circuit breakers halt trading within 1 execution cycle of triggering condition.
- SC-003: Dashboard kill switch disables auto-execution within 1 second.
- SC-004: All auto-executions have complete audit trail entries.
- SC-005: Position sizing respects all configured caps.
- SC-006: Slippage guard prevents execution when price moves beyond threshold.
- SC-007: Daily summary webhook fires at midnight UTC with accurate stats.
- SC-008: Zero orders placed when `auto_execution.enabled` is false.
- SC-009: All quality gates pass.

## Dependencies

- Feature 018 (One-Click Execution) — venue clients, credential management, execution tables.
- All dependencies from 018 (`py-clob-client`, `cryptography`, `web3`/`eth-account`).

## Out of Scope

- Dynamic threshold adjustment based on historical performance (ML-based sizing).
- Multi-leg strategies beyond two-venue arbitrage.
- Exit execution (auto-selling positions based on spread convergence).
- Tax reporting or accounting integration.
- Backtesting auto-execution strategy against historical data (use existing replay/evaluate).
- Cross-chain bridging or automatic USDC provisioning.
