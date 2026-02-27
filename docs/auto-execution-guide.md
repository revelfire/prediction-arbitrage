# Auto-Execution Guide

This guide covers the autonomous execution pipeline — an AI-gated system that detects and executes arbitrage opportunities without manual intervention. It layers on top of the one-click execution engine described in [wallet-setup.md](wallet-setup.md).

---

## Prerequisites

- Wallet credentials configured per [wallet-setup.md](wallet-setup.md) (Polymarket private key + Kalshi API key)
- Capital controls tuned and tested with at least one manual execution
- Migration 021 applied (`uv run arb-scanner migrate`)
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

  # Criteria gates (FR-002)
  min_spread_pct: 0.03           # Minimum 3% spread to consider
  max_spread_pct: 0.25           # Reject spreads above 25% (likely data error)
  min_confidence: 0.70           # Minimum match confidence
  allowed_categories: []         # Empty = all categories; ["nba", "nfl"] to restrict
  allowed_ticket_types:
    - "arbitrage"

  # Sizing (FR-003)
  base_size_usd: 25.0            # Starting position size
  max_size_usd: 100.0            # Hard cap per trade
  min_size_usd: 5.0              # Skip if computed size < $5
  per_market_max_usd: 200.0      # Max total exposure per market
  balance_pct_cap: 0.10          # Never use more than 10% of balance

  # Slippage (FR-004)
  max_slippage_pct: 0.02         # Abort if prices moved > 2% since detection

  # Safety limits (FR-005)
  daily_loss_limit_usd: 50.0     # Circuit breaker trips at $50 daily loss
  max_daily_trades: 20           # Hard cap on trades per day
  max_open_positions: 5          # Max concurrent positions
  max_consecutive_failures: 3    # Breaker trips after 3 consecutive failures

  # AI Trade Critic
  critic:
    enabled: true
    model: "claude-haiku-4-5-20251001"
    timeout_seconds: 5.0
    price_staleness_seconds: 30  # Flag prices older than 30s
    anomaly_spread_pct: 0.30     # Flag spreads > 30%
    min_book_depth_contracts: 10 # Flag thin order books
    max_risk_flags: 5            # Auto-reject if > 5 mechanical flags
```

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

### Via Dashboard

1. Navigate to the **Auto-Exec** tab
2. Select **auto** from the mode dropdown
3. Click **Set Mode**

### Via API

```bash
# Enable auto mode
curl -X POST http://localhost:8000/api/auto-execution/enable \
  -H "Content-Type: application/json" \
  -d '{"mode": "auto"}'

# Check status
curl http://localhost:8000/api/auto-execution/status
```

---

## The AI Trade Critic

The critic is a pre-execution risk gate. It runs in two stages:

### Stage 1: Mechanical Flags

Five rule-based checks run on every opportunity (no API call, sub-millisecond):

| Check | Triggers When | Example |
|-------|---------------|---------|
| **Stale data** | Price age > `price_staleness_seconds` | "stale_data: price age 45s" |
| **Anomalous spread** | Spread > `anomaly_spread_pct` | "anomalous_spread: 35.00%" |
| **Low book depth** | Depth < `min_book_depth_contracts` | "low_depth_poly_depth: 3" |
| **Price symmetry** | yes + no deviates from 1.0 by > 5% | "price_symmetry: yes=0.550" |
| **Category risk** | Title contains "cancelled", "postponed", "suspended", "voided", "disputed" | "category_risk: 'suspended' in title" |

### Stage 2: Claude Evaluation

If mechanical flags are raised, the critic calls Claude (default: Haiku for speed and cost) with the trade context. Claude returns a JSON verdict:

```json
{
  "approved": false,
  "risk_flags": ["stale_data", "event_uncertainty"],
  "reasoning": "Price data is 45 seconds old and the event title suggests potential postponement.",
  "confidence": 0.85
}
```

### Key Behaviors

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

Trips after `max_consecutive_failures` consecutive execution failures (exchange errors, timeouts). Resets on next successful trade.

### 3. Anomaly Breaker

Trips when a spread exceeds `anomaly_spread_pct` (extreme pricing anomaly). **Requires manual acknowledgement** — it does not auto-reset.

### Resetting Breakers

**Dashboard:** The Auto-Exec tab shows three indicators (green/red). When the anomaly breaker trips, it stays red until manually reset.

**API:**
```bash
# Reset the anomaly breaker
curl -X POST http://localhost:8000/api/auto-execution/circuit-breaker/reset \
  -H "Content-Type: application/json" \
  -d '{"breaker_type": "anomaly"}'

# Reset all breakers
curl -X POST http://localhost:8000/api/auto-execution/circuit-breaker/reset \
  -H "Content-Type: application/json" \
  -d '{"breaker_type": "all"}'
```

---

## Position Sizing

Size is computed using a spread-scaled formula:

```
raw_size = base_size_usd * (spread_pct / min_spread_pct)
```

Then capped by three limits:
1. `max_size_usd` — hard cap per trade
2. `per_market_max_usd - current_exposure` — per-market exposure limit
3. `available_balance * balance_pct_cap` — percentage of available balance

If the final size is below `min_size_usd`, the trade is skipped.

### Example

With defaults (`base=25, min_spread=3%, max=100`):

| Spread | Raw Size | After Caps |
|--------|----------|------------|
| 3% | $25.00 | $25.00 |
| 5% | $41.67 | $41.67 |
| 10% | $83.33 | $83.33 |
| 15% | $125.00 | $100.00 (max cap) |

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
- **Kill switch:** Red button, immediately halts all auto-execution

### Circuit Breakers
Three status indicators showing green (clear) or red (tripped) for each breaker type. Hover for trip reason when active.

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

---

## API Endpoints

All endpoints are under `/api/auto-execution/`:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/status` | Mode, breaker state, config summary, critic config |
| POST | `/enable` | Set mode (body: `{"mode": "auto"}`) |
| POST | `/disable` | Kill switch |
| GET | `/log?limit=50` | Audit log entries |
| GET | `/positions` | Currently open positions |
| GET | `/stats?days=7` | Performance statistics over N days |
| POST | `/circuit-breaker/reset` | Reset a breaker (body: `{"breaker_type": "anomaly"}`) |

---

## Notifications

Auto-execution events are dispatched to the same Slack/Discord webhooks used for arbitrage alerts. Events include:

- **Trade executed** — with spread, size, slippage, and critic verdict summary
- **Trade failed** — with error context
- **Circuit breaker tripped** — which breaker and why

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

Query the log via the dashboard Auto-Exec tab, the `/api/auto-execution/log` endpoint, or directly in PostgreSQL:

```sql
SELECT * FROM auto_execution_log
ORDER BY created_at DESC
LIMIT 20;
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
- `max_open_positions` reached — close or wait for existing positions to resolve

### Critic rejecting everything

1. Check the mechanical flags in the log — are they legitimate warnings?
2. If `price_staleness_seconds` is too aggressive, increase it (e.g., 30 → 60)
3. If `min_book_depth_contracts` is too high for your markets, lower it
4. Disable the critic temporarily with `critic.enabled: false` to confirm it's the bottleneck

### Circuit breaker stuck

The anomaly breaker requires manual reset. Use the dashboard or API:
```bash
curl -X POST http://localhost:8000/api/auto-execution/circuit-breaker/reset \
  -H "Content-Type: application/json" \
  -d '{"breaker_type": "anomaly"}'
```

Loss and failure breakers auto-reset (midnight UTC and on next success, respectively).

### Slippage rejections

If trades are frequently rejected for slippage:
- Increase `max_slippage_pct` (e.g., 0.02 → 0.03)
- This is often caused by fast-moving markets where prices shift between detection and execution
- Consider whether the detection-to-execution latency can be reduced
