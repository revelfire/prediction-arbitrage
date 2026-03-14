# Auto-Execution Guide

This guide covers the autonomous execution pipeline — an AI-gated system that detects and executes arbitrage opportunities without manual intervention. It layers on top of the one-click execution engine described in [wallet-setup.md](wallet-setup.md).

---

## Prerequisites

- Wallet credentials configured per [wallet-setup.md](wallet-setup.md) (Polymarket private key + Kalshi API key)
- Capital controls tuned and tested with at least one manual execution
- Migrations 021 and 022 applied (`uv run arb-scanner migrate`)
- Funded accounts on both venues with sufficient balance for your configured `base_size_usd`

---

## How It Works

The auto-execution pipeline processes each detected opportunity through a 10-step gate sequence:

```
Opportunity Detected
  │
  ├─ 1. Mode check (must be "auto")
  ├─ 2. Per-ticket lock (prevents duplicate execution)
  ├─ 3. Criteria evaluation (8 independent checks)
  ├─ 4. Position sizing (spread-scaled formula)
  ├─ 5. AI Trade Critic (mechanical flags → Claude gate)
  ├─ 6. Slippage check (live price re-fetch)
  ├─ 7. Execute both legs (Polymarket + Kalshi)
  ├─ 8. Record to audit log
  ├─ 9. Update circuit breakers
  └─ 10. Send notification
```

For **flippening tickets**, a position record is written to `flippening_auto_positions` after step 7 so that the exit signal (when detected later by the live engine) can automatically place a sell order to close the position.

A trade can be rejected at steps 3, 4, 5, or 6. Every decision is logged with full context for post-trade review.

---

## Three Modes

| Mode | Behavior |
|------|----------|
| `off` | Pipeline disabled. No opportunities processed. |
| `manual` | Opportunities logged but not executed. Use for monitoring before going live. |
| `auto` | Full autonomous execution through all 10 gates. |

Set the mode via config, CLI flag, or dashboard toggle.

---

## Configuration

All auto-execution settings live under `auto_execution:` in `config.yaml`:

```yaml
auto_execution:
  enabled: true
  mode: "off"                    # Start conservative

  # Criteria gates
  min_spread_pct: 0.03           # Minimum 3% spread to consider
  max_spread_pct: 0.50           # Reject spreads above 50% (likely data error)
  min_confidence: 0.62           # Minimum match confidence
  min_liquidity_usd: 100.0       # Minimum market liquidity
  allowed_categories: []         # Empty = all categories; ["nba", "nfl"] to restrict
  blocked_categories: []         # Categories to always exclude
  allowed_ticket_types:
    - "arbitrage"
    - "flippening"

  # Sizing
  base_size_usd: 25.0            # Starting position size
  max_size_usd: 50.0             # Hard cap per trade
  min_size_usd: 5.0              # Skip if computed size < $5
  max_per_market_usd: 100.0      # Max total exposure per market

  # Slippage
  max_slippage_pct: 0.02         # Abort if prices moved > 2% since detection

  # Safety limits
  daily_loss_limit_usd: 200.0    # Circuit breaker trips at $200 daily loss
  max_daily_trades: 50           # Hard cap on trades per day
  max_consecutive_failures: 3    # Breaker trips after 3 consecutive failures
  cooldown_seconds: 30           # Wait between successive auto-trades
  confidence_guardrail_enabled: true      # Raise threshold if recent execution failures spike
  confidence_guardrail_window_attempts: 20
  confidence_guardrail_fail_rate: 0.55
  confidence_guardrail_raise_to: 0.65
  failure_probe_cooldown_min_seconds: 15  # Floor for breaker probe cooldown
  failure_probe_cooldown_max_seconds: 300 # Ceiling for breaker probe cooldown
  failure_probe_backoff_multiplier: 1.5   # Cooldown growth after failed probe
  failure_probe_recovery_multiplier: 0.75 # Cooldown shrink after successful probe

  # Flippening exit
  stop_loss_aggression_pct: 0.02 # Discount stop-loss limit price by 2% for fill priority
  exit_pending_stale_seconds: 30 # If pending sell is older than this, retry
  exit_retry_max_attempts: 4      # Max cancel/reprice retries per position
  exit_retry_reprice_pct: 0.02    # Lower retry limit by 2% each attempt
  exit_retry_min_price: 0.01      # Absolute floor for retry pricing

  # AI Trade Critic
  critic:
    enabled: true
    model: "claude-haiku-4-5-20251001"
    timeout_seconds: 5.0
    skip_below_spread_pct: 0.05  # Skip Claude evaluation for strong-signal spreads
    price_staleness_seconds: 60  # Flag prices older than 60s
    anomaly_spread_pct: 0.30     # Flag spreads > 30%
    min_book_depth_contracts: 10 # Flag thin order books (arbitrage tickets only)
    max_risk_flags: 3            # Auto-reject if > 3 mechanical flags
```

> **Enforced gates:** `min_liquidity_usd`, `max_daily_trades`, and `allowed_ticket_types` are now enforced at evaluation time. `min_liquidity_usd` applies to arbitrage tickets only (combined Polymarket + Kalshi depth). `max_daily_trades` counts executed trades since midnight UTC.

### Starting Conservative

Begin with these training-wheels settings:

1. Set `mode: "manual"` — watch what _would_ execute for a few hours
2. Set `base_size_usd: 10.0` and `max_size_usd: 25.0` — small positions
3. Set `daily_loss_limit_usd: 25.0` — tight loss limit
4. Set `max_daily_trades: 5` — limit volume while gaining confidence
5. Review the audit log in the dashboard before switching to `mode: "auto"`

---

## Enabling Auto-Execution

### Via CLI

```bash
# Flippening engine with auto-execution enabled
uv run arb-scanner flip-watch --auto-execute

# With category filter
uv run arb-scanner flip-watch --auto-execute --categories nba,nfl
```

The `--auto-execute` flag sets `mode: "auto"` and `enabled: true` for the session.

> **Note:** `--auto-execute` now initializes the full execution pipeline at startup. This requires `DATABASE_URL` and `POLY_PRIVATE_KEY` environment variables. The command will fail fast with a clear error if either is missing.

### Via Dashboard

1. Navigate to the **Auto-Exec** tab
2. Select **auto** from the mode dropdown
3. Set **Min Confidence** (optional) and click **Set** to apply live
4. Click **Set Mode**

### Via API

```bash
# Enable auto mode
curl -X POST http://localhost:8000/api/auto-execution/enable \
  -H "Content-Type: application/json" \
  -d '{"mode": "auto"}'

# Update min confidence live (no restart)
curl -X POST http://localhost:8000/api/auto-execution/config \
  -H "Content-Type: application/json" \
  -d '{"min_confidence": 0.62}'

# Check status
curl http://localhost:8000/api/auto-execution/status
```

---

## The AI Trade Critic

The critic is a pre-execution risk gate. It runs in two stages:

### Stage 1: Mechanical Flags

Rule-based checks run on every opportunity (no API call, sub-millisecond):

| Check | Triggers When | Example |
|-------|---------------|---------|
| **Stale data** | Price age > `price_staleness_seconds` | "stale_data: price age 75s" |
| **Anomalous spread** | Spread > `anomaly_spread_pct` | "anomalous_spread: 35.00%" |
| **Low book depth** | Depth < `min_book_depth_contracts` (arbitrage tickets only) | "low_depth_poly_depth: 3" |
| **Price symmetry** | yes + no deviates from 1.0 by > 5% | "price_symmetry: yes=0.550" |
| **Category risk** | Title contains "cancelled", "postponed", "suspended", "voided", "disputed" | "category_risk: 'suspended' in title" |

> **Note:** Book depth checks are skipped entirely for `ticket_type: "flippening"`. Flippening trades are single-venue timing plays where order book depth is not meaningful.

### Stage 2: Claude Evaluation

If mechanical flags are raised, the critic calls Claude (default: Haiku for speed and cost) with the trade context. Claude returns a JSON verdict:

```json
{
  "approved": false,
  "risk_flags": ["stale_data", "event_uncertainty"],
  "reasoning": "Price data is 75 seconds old and the event title suggests potential postponement.",
  "confidence": 0.85
}
```

### Key Behaviors

- **Strong-signal bypass.** When spread > `skip_below_spread_pct` (default 5%), Claude is skipped entirely. These are high-conviction trades.
- **No flags = skip Claude entirely.** Most trades never hit the API. This keeps latency low and costs near zero.
- **Too many flags = auto-reject.** If mechanical flags exceed `max_risk_flags`, the trade is rejected without calling Claude.
- **Fail-open on errors.** If Claude times out or returns an error, the trade proceeds. Circuit breakers are the safety net, not the AI.
- **API key fallback.** The critic uses `critic.api_key` if set, otherwise falls back to the main `claude.api_key`.

---

## Circuit Breakers

Three independent circuit breakers protect against runaway losses:

### 1. Loss Breaker

Trips when cumulative daily P&L exceeds `daily_loss_limit_usd`. Auto-resets at midnight UTC.

### 2. Failure Breaker

Trips after `max_consecutive_failures` consecutive execution failures (exchange errors, timeouts). Resets on next successful trade. While tripped, the flip pipeline allows one timed probe attempt per cooldown window so it can recover without remaining fully blocked.

### 3. Anomaly Breaker

Trips when a spread exceeds `anomaly_spread_pct` (extreme pricing anomaly). **Requires manual acknowledgement** — it does not auto-reset.

### Resetting Breakers

**Dashboard:** The Auto-Exec tab shows three indicators (green/red). When the anomaly breaker trips, it stays red until manually reset.

**API:**
```bash
# Reset the anomaly breaker (arb pipeline only)
curl -X POST http://localhost:8000/api/auto-execution/circuit-breaker/reset \
  -H "Content-Type: application/json" \
  -d '{"breaker_type": "anomaly", "pipeline": "arb"}'

# Reset all breakers on all pipelines
curl -X POST http://localhost:8000/api/auto-execution/circuit-breaker/reset \
  -H "Content-Type: application/json" \
  -d '{"breaker_type": "all", "pipeline": "all"}'
```

---

## Position Sizing

Size is computed using a spread-scaled formula:

```
raw_size = base_size_usd * (spread_pct / min_spread_pct)
```

Then capped by three limits:
1. `max_size_usd` — hard cap per trade
2. `max_per_market_usd - current_exposure` — per-market exposure limit
3. `available_balance * 0.5` — never use more than 50% of available balance

If the final size is below `min_size_usd`, the trade is skipped.

### Example

With defaults (`base=25, min_spread=3%, max=50`):

| Spread | Raw Size | After Caps |
|--------|----------|------------|
| 3% | $25.00 | $25.00 |
| 5% | $41.67 | $41.67 |
| 8% | $66.67 | $50.00 (max cap) |
| 15% | $125.00 | $50.00 (max cap) |

---

## Flippening Exit Execution

When the pipeline auto-executes a **flippening** entry trade, it records the open position in the `flippening_auto_positions` table. When the live engine later fires an exit signal for that market, the pipeline automatically places a Polymarket sell order to close the position.

### Exit Signal Flow

```
GameManager detects exit condition
  → handle_exit() in _orch_processing.py
  → _feed_exit_pipeline() in _orch_exit.py
  → AutoExecutionPipeline.process_exit()
  → FlipExitExecutor.execute_exit()
  → Polymarket sell order placed
  → Position marked closed with realized P&L
```

> Exit orders that fail are marked `exit_failed` (still treated as active inventory). Pending exits are monitored and retried automatically by the stale watchdog.

### Stale Pending Exit Watchdog

If a sell order sits in `exit_pending` too long:

1. Check order age against `exit_pending_stale_seconds`
2. Cancel stale venue order
3. Reprice down by `exit_retry_reprice_pct`
4. Place replacement sell order
5. Repeat until `exit_retry_max_attempts` reached

When retry budget is exhausted, the position is marked `exit_failed`.

### Exit Reasons

| Reason | Description | Price Adjustment |
|--------|-------------|-----------------|
| `reversion` | Price reverted to target | Exact `target_exit_price` |
| `stop_loss` | Price hit stop-loss | `stop_loss_price × (1 - stop_loss_aggression_pct)` |
| `timeout` | Max hold time exceeded | `target_exit_price` |
| `resolution` | Market resolved | `target_exit_price` |

The `stop_loss_aggression_pct` discount (default 2%) lowers the stop-loss limit price to improve fill probability when the market is moving against you.

### Orphan Detection

On startup, the flippening orchestrator queries `flippening_auto_positions` for active rows (`open`, `exit_pending`, `exit_failed`). If found, a Slack/Discord alert is dispatched listing each orphaned position.

After startup, periodic tasks automatically:
- attempt timeout exits for stale active positions
- reconcile pending exits against venue state
- retry stale pending sells with cancel/reprice logic

### Manual Exit

An open position can be exited manually from the ticket detail modal in the dashboard (**Exit Now** button, visible for executed flippening tickets with an open position) or via the API:

```bash
# Check if a position is open
curl http://localhost:8000/api/execution/flip-position/{arb_id}

# Trigger a manual exit (places sell at entry price)
curl -X POST http://localhost:8000/api/execution/flip-exit/{arb_id}
```

The manual exit places a limit sell at the recorded entry price, regardless of current market price, giving the position a chance to exit at break-even.

---

## Kill Switch

The kill switch immediately disables auto-execution. Use it if you see unexpected behavior.

**Dashboard:** Red **KILL** button on the Auto-Exec tab.

**API:**
```bash
curl -X POST http://localhost:8000/api/auto-execution/disable
```

**Programmatic:** The pipeline exposes `pipeline.kill()` which sets mode to "off" and sets the killed flag. No trades will execute until mode is explicitly re-enabled.

After a kill, you must explicitly set mode back to "auto" to resume. The kill is not automatically reversed.

---

## Dashboard: Auto-Exec Tab

The Auto-Exec tab provides real-time visibility into autonomous execution:

### Controls Row
- **Mode selector:** Dropdown (off / manual / auto) with Set Mode button
- **Min Confidence:** Numeric input + Set button; applies at runtime without restart
- **Runtime confidence labels:** Shows current live threshold and guardrail fail-rate window
- **Kill switch:** Red button, immediately halts all auto-execution

### Circuit Breakers
Three status indicators showing green (clear) or red (tripped) for each breaker type. Hover for trip reason when active.

### Flip Failure Probe
Five cards show failure-breaker recovery telemetry:
- **Attempts:** Probe trade attempts made while failure breaker was tripped
- **Successes / Failures:** Probe outcomes
- **Success Rate:** Probe success ratio
- **Next Window:** Next eligible probe time (or `open`/`active`)

Probe cooldown is adaptive: failed probes increase the cooldown window; successful probes decrease it (bounded to a safe minimum/maximum).

### Flip Exit Watchdog
Six cards show pending-exit recovery telemetry:
- **Stale Detected**
- **Retries Placed**
- **Retry Closed**
- **Cancel Failed**
- **Retry Failed**
- **Retry Exhausted**

### Today's Stats
Four summary cards:
- **Trades:** Count of executed trades today
- **Win/Loss:** Win rate percentage
- **P&L:** Cumulative profit/loss for the day
- **Avg Slippage:** Mean slippage across today's trades

### Trade Log
Table of the 20 most recent auto-execution entries:

| Column | Description |
|--------|-------------|
| Time | When the opportunity was processed |
| Arb ID | Opportunity identifier |
| Spread | Detection-time spread percentage |
| Size | Position size in USD |
| Critic | Approved/Rejected badge with risk flag count |
| Status | executed, rejected, critic_rejected, or failed |
| Duration | Pipeline processing time in ms |

Above the log table, a **Top rejection/failure reasons** panel summarizes the most frequent recent blockers and execution errors.

### Ticket Detail: Flippening Positions

When viewing an executed flippening ticket, the detail modal shows an **Open Position** card displaying:
- Position status (open / closed / exit_failed)
- Side (YES/NO), contract count, entry price
- Exit price and realized P&L (once closed)
- **Exit Now** button — visible only when status is `open`

---

## API Endpoints

### Auto-Execution (`/api/auto-execution/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/status` | Mode, breaker state, config summary, critic config |
| POST | `/enable` | Set mode (body: `{"mode": "auto"}`) |
| POST | `/disable` | Kill switch |
| POST | `/config` | Update runtime knobs (currently `min_confidence`) |
| GET | `/log?limit=50` | Audit log entries |
| GET | `/positions` | Currently open positions |
| GET | `/stats?days=7` | Performance statistics over N days |
| POST | `/circuit-breaker/reset` | Reset a breaker (body: `{"breaker_type": "anomaly"}`) |

### Flippening Positions (`/api/execution/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/flip-position/{arb_id}` | Open position for a ticket (404 if none) |
| POST | `/flip-exit/{arb_id}` | Manually trigger exit (places limit sell at entry price) |

---

## Notifications

Auto-execution events are dispatched to the same Slack/Discord webhooks used for arbitrage alerts. Events include:

- **Trade executed** — with spread, size, slippage, and critic verdict summary
- **Trade failed** — with error context
- **Circuit breaker tripped** — which breaker and why
- **Orphaned positions detected** — on startup, if prior session left open positions

Configure webhooks in `config.yaml` under `notifications:`:

```yaml
notifications:
  slack_webhook: "https://hooks.slack.com/services/..."
  discord_webhook: "https://discord.com/api/webhooks/..."
```

---

## Audit Trail

Every opportunity processed by the pipeline is logged to `auto_execution_log` with:

- Trigger spread and confidence at detection time
- Criteria evaluation snapshot (which checks passed/failed)
- Pre-execution balances on both venues
- Computed position size
- Full critic verdict (approved, risk flags, reasoning, confidence)
- Execution result ID (links to `execution_orders` table)
- Actual spread and slippage post-execution
- Circuit breaker state at time of trade
- Pipeline processing duration in milliseconds
- Status: `executed`, `rejected`, `critic_rejected`, or `failed`

Flippening position history is in `flippening_auto_positions`:

```sql
-- Recent auto-execution log
SELECT * FROM auto_execution_log
ORDER BY created_at DESC
LIMIT 20;

-- Open flippening positions
SELECT * FROM flippening_auto_positions
WHERE status = 'open';

-- Closed positions with P&L
SELECT arb_id, market_id, side, size_contracts,
       entry_price, exit_price, realized_pnl, exit_reason, closed_at
FROM flippening_auto_positions
WHERE status = 'closed'
ORDER BY closed_at DESC;
```

---

## Troubleshooting

### Pipeline not processing opportunities

1. Check mode is "auto": `curl http://localhost:8000/api/auto-execution/status`
2. Check no circuit breakers are tripped (status response includes `circuit_breakers` array)
3. Check `enabled: true` in config
4. If using `--auto-execute` CLI flag, confirm the flag is being passed

### All trades rejected

Check the audit log for rejection reasons:
```bash
curl http://localhost:8000/api/auto-execution/log?limit=5
```

Common causes:
- `min_spread_pct` too high — lower it if legitimate spreads are being filtered
- `min_confidence` too high — check if match confidence scores are consistently below threshold
- `daily_loss_limit_usd` hit — loss breaker tripped, resets at midnight UTC
- `max_daily_trades` reached — hard cap for the day, resets at midnight UTC

You can tune `min_confidence` live from the Auto-Exec tab, or via API:
```bash
curl -X POST http://localhost:8000/api/auto-execution/config \
  -H "Content-Type: application/json" \
  -d '{"min_confidence": 0.62}'
```

### Critic rejecting everything

1. Check the mechanical flags in the log — are they legitimate warnings?
2. If `price_staleness_seconds` is too aggressive, increase it (e.g., 60 → 120)
3. If `min_book_depth_contracts` is too high for your markets, lower it (only applies to arbitrage tickets)
4. Disable the critic temporarily with `critic.enabled: false` to confirm it's the bottleneck

### Circuit breaker stuck

The anomaly breaker requires manual reset. Use the dashboard or API:
```bash
curl -X POST http://localhost:8000/api/auto-execution/circuit-breaker/reset \
  -H "Content-Type: application/json" \
  -d '{"breaker_type": "anomaly"}'
```

Loss and failure breakers auto-reset (midnight UTC and on next success, respectively). In addition, failure-only trips on the flip pipeline now permit periodic probe attempts so the system can self-recover from transient venue issues instead of staying hard-blocked.

You can target specific pipelines when resetting breakers:
```bash
# Reset anomaly breaker on flippening pipeline only
curl -X POST http://localhost:8000/api/auto-execution/circuit-breaker/reset \
  -H "Content-Type: application/json" \
  -d '{"breaker_type": "anomaly", "pipeline": "flip"}'
```

### Slippage rejections

If trades are frequently rejected for slippage:
- Increase `max_slippage_pct` (e.g., 0.02 → 0.03)
- This is often caused by fast-moving markets where prices shift between detection and execution
- Consider whether the detection-to-execution latency can be reduced

### Flippening position not closing

If an exit signal fired but the position remains open (status `exit_failed`):
1. Check the structured logs for `flip_exit_order_failed` events
2. Verify your Polymarket private key is configured and has trading permissions
3. Use the dashboard **Exit Now** button or `POST /api/execution/flip-exit/{arb_id}` to retry manually
4. If neither works, close the position directly on Polymarket and update the row:

```sql
UPDATE flippening_auto_positions
SET status = 'abandoned'
WHERE arb_id = '<your-arb-id>' AND status = 'exit_failed';
```

### WebSocket stall detection

The flippening engine monitors WebSocket message flow. If no messages are received for 3 consecutive telemetry intervals (~90s), a forced reconnect is triggered automatically. Forced reconnects have a 60-second cooldown to prevent reconnect storms. Check structured logs for `ws_stall_reconnect` events.
