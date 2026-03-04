# 021 — Flippening Exit Execution

## Overview

When auto-execution mode places a flippening entry (buy), the system currently stops there.
Exit conditions (price reversion, stop-loss, timeout, market resolution) are detected and
alerted, but no sell order is ever placed — the operator must exit manually. This feature
closes the loop: when auto-exec is active and an exit condition fires for a position that
was actually entered, the system places the sell order automatically.

## Motivation

Feature 019 (Auto-Execution) explicitly deferred exit execution as out-of-scope:
> "Exit execution (auto-selling positions based on spread convergence)."

That deferral was intentional — entry is the harder, time-sensitive side. But with entry
automated, the gap is now visible: the operator is alerted "time to exit" but still has to
log into Polymarket manually, find the position, and enter a sell. During off-hours this can
cost significant P&L, and it defeats part of the purpose of running auto-exec.

Exit execution is simpler than entry: the position already exists, the token is known, the
size is known, and the exit price is computed by the signal generator. This feature wires
those together.

## Prerequisites

- Feature 018 (One-Click Execution) — venue executors, credential management, `execution_orders` table.
- Feature 019 (Auto-Execution Pipeline) — `AutoExecutionPipeline`, `ExecutionOrchestrator`, circuit breakers.
- Feature 013 (Event Market Reversion / Flippening Engine) — `ExitSignal`, `handle_exit()`, `GameManager`.

## Functional Requirements

### FR-001: Open Position Registry

When auto-exec places a flippening entry and the order returns status `submitted` or
`filled`, record the position in a new `flippening_auto_positions` table.

Fields:
- `id` UUID PK
- `arb_id` — references the execution ticket
- `market_id` — Polymarket market slug or ID
- `token_id` — the CLOB token ID that was purchased (YES or NO token)
- `side` — `yes` or `no` (which token is held)
- `size_contracts` — number of contracts purchased
- `entry_price` — price per contract at entry (from `ExecutionResult`)
- `venue_order_id` — Polymarket CLOB order ID from entry
- `status` — `open` | `closed` | `exit_failed` | `abandoned`
- `opened_at`, `closed_at` TIMESTAMPTZ

The position is written by `AutoExecutionPipeline._execute_pipeline()` after a successful
`orchestrator.execute()`. Only tickets of `ticket_type = "flippening"` are registered.

### FR-002: Exit Signal Wiring

`handle_exit()` in `flippening/_orch_processing.py` is the single point where all exit
conditions converge (REVERSION, STOP_LOSS, TIMEOUT, RESOLUTION, DISCONNECT). After
persisting the exit signal and buffering the alert, it must also call the exit executor
when auto mode is active — mirroring the `_feed_auto_pipeline()` pattern used for entries.

```python
await _feed_exit_pipeline(event, entry, exit_sig, config)
```

This is a fire-and-forget async call wrapped in try/except, identical in structure to
`_feed_auto_pipeline()`.

### FR-003: Exit Order Placement

`_feed_exit_pipeline()` resolves the auto pipeline and calls a new method
`process_exit()` on `AutoExecutionPipeline`.

`process_exit(exit_sig, entry_sig, event)`:
1. Guard: mode must be `auto`.
2. Look up open position for `event.market_id` from `flippening_auto_positions` where
   `status = "open"`. If none exists, log and return (entry was never placed or already closed).
3. If position `status = "open"` but entry `venue_order_id` is unknown, skip (entry
   is still pending — EC-002 handling is to wait; the next exit signal cycle will retry).
4. Build a `SellRequest`: same `token_id` as the open position, same `size_contracts`,
   `action = "sell"`, `side = position.side`, `price = exit_sig.exit_price`.
5. For `STOP_LOSS` exits: use a more aggressive limit — `exit_sig.exit_price * 0.98`
   (accept up to 2% worse than stop price to ensure fill). Configurable via
   `auto_execution.stop_loss_aggression_pct` (default: 0.02).
6. Place sell order via `PolymarketExecutor.place_order()`.
7. Record to `execution_orders` table (same as entry orders, `action = "sell"`).
8. On success (status `submitted` or `filled`): mark position `status = "closed"`,
   set `closed_at`. Dispatch exit notification webhook.
9. On failure: mark position `status = "exit_failed"`. Do NOT retry automatically
   (EC-004). Dispatch failure alert — operator must close manually.

### FR-004: Deduplication

Each market can only have one open position at a time (enforced by DB unique constraint on
`(market_id, status = "open")`). If a second exit signal fires for the same market while
the first sell is in flight, `process_exit()` returns immediately (position is already
`closed` or transitioning).

### FR-005: Sell Order Mechanics (Polymarket CLOB)

Polymarket CLOB allows selling tokens you hold via a sell limit order. The `place_order()`
call uses:
- `token_id`: the held token (YES or NO token ID from `CategoryMarket.token_for_side()`)
- `side`: `sell_yes` or `sell_no` (matching the held token side)
- `price`: the limit price (sell won't fill below this)
- `size_contracts`: matching the entry size

No changes to `PolymarketExecutor.place_order()` are needed — it already handles sell-side
order placement. The `OrderRequest.side` value of `sell_yes` / `sell_no` is already part of
the `OrderSide` type.

### FR-006: Circuit Breaker Integration

Exit placement failures count toward the failure circuit breaker (same as entry failures).
This is important: if the venue is rejecting sell orders (e.g., market resolved, API issue),
the breaker trips after `max_consecutive_failures` and halts further auto-exec until the
operator investigates.

Exit placements are NOT subject to the anomaly spread breaker or daily loss limit
(exits reduce risk, not add it).

### FR-007: Orphan Detection on Startup

When the flippening orchestrator starts, it queries `flippening_auto_positions WHERE
status = "open"` and logs a warning for each. These are positions that were opened in a
previous session and were not exited (process crash, restart, etc.). A Slack/Discord alert
is dispatched listing each orphaned position with its market, side, size, and entry price,
prompting the operator to close manually.

This is a passive check — no automatic action is taken on orphans.

### FR-008: Dashboard Integration

The existing Tickets tab already shows flippening tickets and execution orders. No new tab
is needed. Extend the ticket detail modal:

- Show open position record (if exists): contracts held, entry price, current P&L estimate.
- Show exit order (if placed): status, exit price, realized P&L.
- Add "Exit Now" button (1-click manual exit) — visible only when position is open,
  regardless of auto/manual mode. This provides the operator a fast path if auto-exit
  failed or auto mode is off.

### FR-009: REST API Endpoint

- `POST /api/execution/flip-exit/{arb_id}` — Manually trigger exit for an open flippening
  position. Respects all preflight checks (credentials, balances). Returns execution result.
  This powers the "Exit Now" button from FR-008.

### FR-010: Notifications

Exit execution events dispatched via existing `dispatch_auto_exec_alert()`:
- `exit_placed`: Sell order submitted — venue, side, size, limit price, exit reason.
- `exit_filled`: Sell confirmed filled — fill price, realized P&L.
- `exit_failed`: Sell rejected — error, arb_id, prompt to close manually.
- `orphan_detected`: On startup, list of open positions from prior session.

Uses the same `notif.effective_auto_exec_slack` URL as entry execution alerts.

## Database Changes

### New table: `flippening_auto_positions`

```sql
CREATE TABLE flippening_auto_positions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    arb_id          TEXT NOT NULL,
    market_id       TEXT NOT NULL,
    token_id        TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('yes', 'no')),
    size_contracts  INTEGER NOT NULL,
    entry_price     NUMERIC(10, 6) NOT NULL,
    venue_order_id  TEXT,
    exit_order_id   TEXT,
    exit_price      NUMERIC(10, 6),
    realized_pnl    NUMERIC(10, 4),
    status          TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'closed', 'exit_failed', 'abandoned')),
    exit_reason     TEXT,
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ
);

CREATE UNIQUE INDEX flippening_auto_positions_market_open
    ON flippening_auto_positions (market_id)
    WHERE status = 'open';

CREATE INDEX flippening_auto_positions_status
    ON flippening_auto_positions (status);
```

### Migration

Migration `018_flippening_auto_positions.sql` (next after migration 017).

## New Modules

```
src/arb_scanner/
├── execution/
│   ├── flip_exit_executor.py   # process_exit() logic + sell order construction
│   └── flip_position_repo.py   # CRUD for flippening_auto_positions
├── flippening/
│   └── _orch_exit.py           # _feed_exit_pipeline() — mirrors _orch_processing.py pattern
```

### `flip_exit_executor.py`

Single public function: `async def process_exit(...)` — implements FR-003.
Internal helpers: `_build_sell_request()`, `_stop_loss_price()`.
Max 50 lines per function, max 300 lines total.

### `flip_position_repo.py`

- `insert_position(arb_id, market_id, token_id, side, size_contracts, entry_price, venue_order_id)`
- `get_open_position(market_id) -> dict | None`
- `close_position(market_id, exit_order_id, exit_price, realized_pnl, exit_reason)`
- `mark_exit_failed(market_id)`
- `get_orphaned_positions() -> list[dict]`

### `_orch_exit.py`

`async def _feed_exit_pipeline(event, entry, exit_sig, config)` — wraps
`AutoExecutionPipeline.process_exit()`, identical error-handling pattern to
`_feed_auto_pipeline()`.

## Configuration

```yaml
auto_execution:
  # ... existing config ...
  stop_loss_aggression_pct: 0.02   # Accept up to 2% worse price on stop-loss exits
  exit_timeout_seconds: 120        # Alert if sell not filled within this time (future FR)
```

## Edge Cases

- **EC-001**: Exit fires but no open position exists (entry was never placed, e.g. auto mode was off, or criteria rejected the entry) → `process_exit()` finds no open position, logs `exit_skipped_no_position`, returns. No order placed.
- **EC-002**: Exit fires while entry order is `submitted` but not yet `filled` → Position exists with `venue_order_id` set. Place sell anyway at limit — if entry fills later, sell will absorb it. If entry never fills, the sell limit will sit in the book unfilled; cancel via `exit_timeout_seconds` in a future feature.
- **EC-003**: Multiple exit signals for the same market (e.g., REVERSION fires, then 30 seconds later TIMEOUT also fires) → Unique index on `(market_id, status="open")` ensures only one row. After first exit places the sell and closes/fails the position, subsequent signals find no open position and skip gracefully.
- **EC-004**: Sell order fails (venue rejected, API error) → Mark position `exit_failed`. Alert operator. Do NOT retry. Circuit breaker counts this as a failure. Operator uses "Exit Now" button (FR-008) or manual Polymarket UI.
- **EC-005**: Market resolved between entry and exit signal → Polymarket CLOB rejects sell on resolved market. Same as EC-004 — `exit_failed`, alert, manual resolution (redemption in Polymarket UI).
- **EC-006**: Auto mode disabled while positions are open → Existing positions remain tracked. Re-enabling auto mode resumes exit monitoring. Operator is alerted on next startup (FR-007 orphan check).
- **EC-007**: Process restarts with open positions → FR-007 orphan detection fires on startup, alerting operator. System does NOT auto-exit on restart (conservative — requires explicit re-entry of the position into the monitoring cycle).
- **EC-008**: `size_contracts` in position mismatches entry fill (partial fill) → Stretch: for now, record the requested size. Future improvement: query the venue order to get actual filled contracts before placing sell.

## Success Criteria

- SC-001: When auto mode is active and an exit condition fires for an open position, a sell order is placed within 5 seconds of `handle_exit()` being called.
- SC-002: Position registry is updated atomically — no position can appear `open` after a successful sell.
- SC-003: Orphan alert fires on startup when `flippening_auto_positions` has open rows from a prior session.
- SC-004: Exit failures do not trip the loss circuit breaker (only the failure breaker).
- SC-005: "Exit Now" button in dashboard works for open positions regardless of auto/manual mode.
- SC-006: All auto-exit orders appear in the ticket detail execution orders section.
- SC-007: Realized P&L is stored in `flippening_auto_positions.realized_pnl` after exit fills.
- SC-008: All quality gates pass (ruff, mypy --strict, pytest ≥70% coverage).

## Out of Scope

- Fill monitoring / polling after sell is placed (sell is fire-and-forget in this feature).
- Partial position exits (all-or-nothing sell at entry size).
- Kalshi exit execution (flippening is Polymarket-only; Kalshi exit would require a separate feature).
- Automatic retry on exit failure.
- Tax lot tracking or realized P&L reporting beyond the `flippening_auto_positions` table.
- Exit execution for arbitrage positions (arbitrage has two legs; exit is venue-specific and asymmetric).
