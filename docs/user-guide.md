# Arb Scanner Dashboard — User Guide

This guide explains every screen of the Arb Scanner Dashboard, how to interpret the data, and how to take action on execution tickets.

## Table of Contents

- [Starting the Dashboard](#starting-the-dashboard)
- [How Prediction Markets Work](#how-prediction-markets-work)
- [The Two Engines](#the-two-engines)
- [Tab 1: Opportunities](#tab-1-opportunities)
- [Tab 2: Health](#tab-2-health)
- [Tab 3: Alerts](#tab-3-alerts)
- [Tab 4: Tickets](#tab-4-tickets)
- [Tab 5: Flippenings](#tab-5-flippenings)
- [Tab 6: Discovery](#tab-6-discovery)
- [Tab 7: WS Health](#tab-7-ws-health)
- [Understanding Execution Tickets](#understanding-execution-tickets)
- [Decision Framework](#decision-framework)

---

## Starting the Dashboard

```bash
# With database (full functionality)
uv run arb-scanner serve

# UI preview without database
uv run arb-scanner serve --no-db

# With embedded flippening engine (live price streaming)
uv run arb-scanner serve --flip-watch
```

The dashboard opens at `http://localhost:8000`. All data auto-refreshes every 30 seconds. Click **Refresh Now** in the footer for an immediate update.

The **Run Scan** button in the header triggers an on-demand arbitrage scan cycle. The status bar shows scan results or errors.

---

## How Prediction Markets Work

The scanner monitors two prediction market venues:

**Polymarket** — The larger venue. Binary outcome markets across sports, politics, crypto, entertainment, and more. Each contract pays $1.00 if the outcome is YES, $0.00 if NO. A YES price of $0.65 implies a 65% probability.

**Kalshi** — A regulated exchange with similar binary markets. Same $1.00 payout structure but different fee model and sometimes different pricing for equivalent events.

### Fee Structures

Understanding fees is critical because they eat into arbitrage profits:

| Venue | Fee Model | How It Works | Impact |
|-------|-----------|--------------|--------|
| Polymarket | 2% on net winnings | Buy YES at $0.45, win → winnings = $0.55 → fee = $0.011 | Light on expensive contracts, heavier on cheap ones |
| Kalshi | $0.07 per contract (flat) | Same fee whether you buy at $0.10 or $0.90 | Heavy on cheap contracts (7% on a $1.00 buy), light on expensive ones |

The dashboard shows profits **after fees**. When you see "Expected Profit: $9.40," fees have already been deducted.

---

## The Two Engines

The scanner runs two independent detection engines that produce execution tickets:

### Cross-Venue Arbitrage

Detects when you can buy YES on one venue and NO on the other for a combined cost below $1.00. Since one side is guaranteed to pay $1.00, the difference is risk-free profit (minus fees and slippage).

**Example:**
- Polymarket YES ask: $0.45
- Kalshi NO ask: $0.52
- Combined cost: $0.97
- Guaranteed payout: $1.00
- Gross profit: $0.03 per contract (3.1%)
- After fees: ~$0.009 per contract (0.9%)

### Flippening (Mean Reversion)

Detects temporary emotional overreactions in live event markets. When odds spike sharply against a favorite (panic selling), the engine bets on reversion back toward the pre-event baseline.

**Example:**
- Pre-game baseline: Lakers YES at $0.65
- Mid-game panic: Lakers YES drops to $0.48 (26% drop)
- Trade: Buy YES at $0.48
- Target exit: $0.60 (partial reversion)
- Stop loss: $0.41 (limits downside)
- Expected profit: 25% if target hit

---

## Tab 1: Opportunities

Shows recently detected cross-venue arbitrage opportunities.

### Recent Opportunities Table

Each row is one arbitrage detection:

| Column | What It Means |
|--------|---------------|
| **Pair** | Polymarket event ID (hover for full ID) |
| **Buy** | Venue where contract is cheaper — execute the BUY leg here |
| **Sell** | Venue where contract is more expensive — execute the SELL leg here |
| **Spread** | Net profit as a percentage of cost, after fees. Higher = better. |
| **Max Size** | Maximum USD position that can profitably execute at current book depth |
| **Depth** | Liquidity indicator. **checkmark** = sufficient depth. **warning** = thin books, expect slippage |
| **Annualized** | If the event resolves in N days, what the return looks like annualized |
| **Time** | When the opportunity was detected |

**Click any row** to open the Pair Detail panel with a spread history chart (last 24 hours). Use this to see whether the spread is stable, widening, or closing.

### Top Pairs (24h) Table

Aggregated view of the best-performing pairs over the last 24 hours:

| Column | What It Means |
|--------|---------------|
| **Peak Spread** | Highest net spread observed — the best moment to have traded |
| **Avg Spread** | Rolling average — shows if the spread is consistently profitable or just spiked once |
| **Detections** | How many times this pair was flagged. More detections = more persistent opportunity |

**Interpreting the data:** A pair with high peak spread but low avg spread had one brief spike — likely already closed. A pair with moderate but consistent avg spread across many detections is a more reliable target.

---

## Tab 2: Health

Monitors the scanner's operational metrics to ensure it's running correctly.

### Summary Cards

| Card | What It Means | Healthy Range |
|------|---------------|---------------|
| **Total Scans (24h)** | Number of completed scan cycles | 1,400+ (one per minute) |
| **Avg Duration** | How long each scan takes | < 30 seconds |
| **LLM Calls** | Claude API calls for contract matching | Proportional to new market pairs |
| **Errors** | Scan failures (API timeouts, parse errors) | < 5% of scans |
| **Opportunities** | Total arbs detected in the window | Varies by market conditions |

### Hourly Activity Chart

Stacked bar chart with three series:
- **Blue** = scans per hour (should be steady ~60/hr)
- **Green** = opportunities per hour (varies with market activity)
- **Red** = errors per hour (spikes indicate API issues)

**What to watch for:** A gap in blue bars means the scanner stopped. A spike in red bars means an API is failing. A sudden drop in green bars might mean markets have aligned (no arbs) or the scanner isn't reaching enough markets.

### Recent Scans Table

Detailed log of individual scan runs. Look for:
- **Duration**: Scans taking > 60s may be hitting rate limits
- **Markets**: Should show hundreds of markets per scan. A low count means an API might be down.
- **Errors**: Hover over the error count for details

---

## Tab 3: Alerts

Real-time trend alerts from the spread monitoring engine.

### Alert Types

| Type | Color | What It Means |
|------|-------|---------------|
| **convergence** | Orange | A spread is shrinking — the arb window is closing. Act now or skip. |
| **divergence** | Green | A spread is widening — the opportunity is growing. Good time to review. |
| **new_high** | Gold | A pair's spread hit its highest level — strongest signal yet for this pair. |
| **disappeared** | Gray | A pair is no longer available (market closed or delisted). |
| **health_consecutive_failures** | Red | Multiple scan cycles failed in a row — check API connectivity. |
| **health_zero_opps** | Red | A scan completed but found zero opportunities — unusual if markets are active. |

**Using alerts effectively:** Filter by type using the dropdown. Focus on **divergence** and **new_high** alerts for actionable opportunities. Use **convergence** to decide whether to skip a ticket (the window may have closed by the time you execute). Health alerts indicate system issues, not trading signals.

Each alert shows the pair involved, the spread before/after the change, and a timestamp. System alerts (health) show "System" instead of a pair.

---

## Tab 4: Tickets

The core action center. This is where you manage execution tickets — the system's trade recommendations.

### Filter Bar

Three filters at the top, applied together (AND logic):

| Filter | Options | Default |
|--------|---------|---------|
| **Status** | All, Pending, Approved, Executed, Expired, Cancelled | Pending |
| **Category** | Free text (e.g., "nba", "crypto", "politics") | Empty (all) |
| **Type** | All, Arbitrage, Flippening | All |

Filters update the table immediately. Start with "Pending" to see tickets awaiting your decision.

### Summary Metrics

Five cards showing performance over the last 30 days:

| Card | What It Means |
|------|---------------|
| **Total Tickets** | Count of tickets matching current filters |
| **Execution Rate** | Percentage of tickets you actually executed (executed / total). Low rate is normal — you should be selective. |
| **Avg Slippage** | Average difference between the system's suggested price and your actual entry price. Positive = you paid more than expected. |
| **Win Rate** | Of executed tickets with recorded P&L, what percentage were profitable |
| **Total P&L** | Sum of realized profit/loss across all executed tickets |

### Ticket Table

Each row shows one execution ticket:

| Column | What It Means |
|--------|---------------|
| **Market** | The event title (hover for full text) |
| **Category** | Market category (nba, crypto, etc.) |
| **Type** | "arbitrage" (cross-venue) or "flippening" (mean reversion) |
| **Cost** | Expected capital needed to enter the trade |
| **Profit** | Expected profit after fees (before slippage) |
| **Status** | Current lifecycle state (see below) |
| **Created** | When the ticket was generated |
| **Actions** | Available buttons based on status |

### Ticket Lifecycle

```
PENDING ──────┬──── Approve ────→ APPROVED ──┬── Execute ──→ EXECUTED
              │                              │
              ├──── Expire ─────→ EXPIRED    └── Cancel ───→ CANCELLED
              │
              └──── Cancel ─────→ CANCELLED
```

| Status | Badge Color | Meaning | Available Actions |
|--------|-------------|---------|-------------------|
| **Pending** | Orange | New ticket, awaiting review | Approve, Expire, View |
| **Approved** | Green | You've decided to act on this ticket | Execute, Cancel, View |
| **Executed** | Blue | Trade was placed and recorded | View only (terminal) |
| **Expired** | Gray | Ticket was dismissed (opportunity passed) | View only (terminal) |
| **Cancelled** | Red | Ticket was cancelled after approval | View only (terminal) |

### Taking Action on Tickets

#### Reviewing a ticket

Click any row or the **View** button to open the detail modal. The modal shows:

**For arbitrage tickets:**
- **Leg 1**: Which venue, which side (YES/NO), at what price, for how much
- **Leg 2**: The other venue, opposite side, price, size
- **Expected cost and profit**: The math behind the recommendation
- **Action log**: Full history of status changes and annotations

**For flippening tickets:**
- **Leg 1 (Entry)**: Buy side at entry price on Polymarket
- **Leg 2 (Exit)**: Sell target price, stop loss, and max hold time
- **Category**: Which market category (e.g., "nba")
- **Action log**: History of actions taken

#### Approving a ticket

Click **Approve** to indicate you intend to act on this ticket. This changes the status from Pending to Approved and enables the Execute and Cancel buttons. Approving doesn't execute any trade — it's a personal workflow step to separate "interesting" from "committed."

#### Executing a ticket

Click **Execute** (only available on Approved tickets) to open the execution recording modal:

| Field | Purpose | Required? |
|-------|---------|-----------|
| **Actual Entry Price** | The price you actually got filled at | No (but recommended for slippage tracking) |
| **Actual Size (USD)** | The actual position size you took | No |
| **Notes** | Any context about the execution | No |

Click **Confirm Execution** to record. The system automatically calculates slippage (actual price minus suggested price) and logs everything to the action trail.

#### Expiring a ticket

Click **Expire** on a Pending ticket to dismiss it. Use this when:
- The spread has already closed
- You checked and prices have moved
- The opportunity window has passed
- You don't want to act on this category/type

#### Cancelling a ticket

Click **Cancel** on an Approved ticket. A prompt asks for an optional reason. Use this when:
- Prices moved after you approved but before you could execute
- You changed your mind after further analysis
- Market conditions changed

#### Adding a note

In the detail modal, click **Add Note** to attach an annotation without changing status. Use this to record observations like "spread widening, watching" or "liquidity too thin on Kalshi side."

---

## Tab 5: Flippenings

Monitors the mean reversion engine's live activity.

### Live Price Ticker

The status banner at the top shows engine state:
- **Green "Live - connected"**: WebSocket streaming active, prices updating in real time
- **Gray "Engine not active"**: Run `flip-watch` to start the engine
- **Red "Disconnected - reconnecting..."**: Connection dropped, auto-recovering

### Live Prices Table

Real-time market snapshots while the flippening engine is active:

| Column | What It Means |
|--------|---------------|
| **Market** | Event title (hover for full) |
| **Category** | Market category (nba, crypto, etc.) |
| **YES Mid** | Current midpoint price for YES outcome |
| **Baseline** | Reference price captured at event start (or rolling window) |
| **Deviation** | How far current price has moved from baseline, as a percentage. Color-coded: green (< 5%), amber (5-10%), red (> 10%). Large deviations are potential flippening signals. |
| **Sparkline** | Mini chart of the last 60 price ticks — shows recent trajectory |
| **Spread** | Bid-ask spread (tighter = more liquid) |
| **Depth** | Order book depth as "bids/asks" count |

**What to watch for:** Markets with **red deviation** (> 10%) are the ones most likely to generate flippening tickets. The sparkline shows whether the move is accelerating, stabilizing, or reverting.

### Stats Cards

Aggregate performance metrics for the flippening engine:

| Card | What It Means |
|------|---------------|
| **Total Signals** | How many entry signals the engine has generated |
| **Win Rate** | Percentage of closed positions that hit their target (vs stop/timeout) |
| **Avg P&L** | Average realized profit per closed signal |
| **Avg Hold** | Average time positions were held before exit |

### Active Flippenings Table

Currently open positions (signals generated but not yet exited):

| Column | What It Means |
|--------|---------------|
| **Sport** | Market category |
| **Side** | BUY or SELL — direction of the mean reversion bet |
| **Entry** | Price at which the signal was generated |
| **Target** | Exit price for profit (partial reversion toward baseline) |
| **Stop** | Stop loss price (maximum acceptable loss) |
| **Size** | Suggested position size in USD (scaled by confidence) |
| **Confidence** | Signal quality score (0-100%). Higher = stronger spike pattern. |

### Recent History Table

Completed flippening signals:

| Column | What It Means |
|--------|---------------|
| **Entry / Exit** | Prices at signal generation and closure |
| **P&L** | Realized profit/loss. Positive = hit target or exited profitably. |
| **Hold** | Duration in minutes |
| **Outcome** | Why the position closed: `target_hit` (profit), `stop_triggered` (loss capped), `game_ended` (event resolved), `timeout` (held too long) |

---

## Tab 6: Discovery

Monitors how well the market classifier is categorizing markets.

### Summary Cards

| Card | What It Means |
|------|---------------|
| **Total Scanned** | Markets evaluated by the classifier |
| **Classified** | Markets successfully assigned a category |
| **Hit Rate** | Classification success rate. Should stay above 70%. |
| **Unclassified** | Markets the classifier couldn't categorize — potential blind spots |

### Charts

- **Markets per Category**: Bar chart showing how many markets exist in each category. Uneven distribution is normal (sports dominate).
- **Hit Rate Over Time (24h)**: Line chart showing classification accuracy trend. A declining line means the classifier is struggling with new market formats.
- **Classification Methods**: Doughnut chart showing how markets are being classified (by slug, tag, title, or fuzzy match).

### Degradation Alerts

Lists alerts triggered when classification health dropped below thresholds. A "hit rate dropped below 70%" alert means many new markets aren't being categorized — the flippening engine is potentially missing opportunities.

---

## Tab 7: WS Health

Monitors the WebSocket connection that streams live prices from Polymarket.

### Connection Status

- **Green "Connected"**: Normal operation
- **Orange "Stalled"**: Connection alive but no messages received (market might be quiet, or data pipeline stalled)
- **Red "Disconnected"**: Connection lost, auto-reconnecting

### Metrics

| Card | What It Means |
|------|---------------|
| **Messages Received** | Total WebSocket messages since engine started |
| **Parse Success** | Messages successfully parsed into order book updates |
| **Parse Failed** | Messages that failed validation — could indicate API schema changes |
| **Cache Hit Rate** | Order book cache efficiency. High rate = fewer redundant API calls. |

### Schema Match Rate

A gauge and trend chart showing what percentage of incoming messages match the expected format. A sudden drop indicates Polymarket may have changed their WebSocket API format.

### Throughput Chart

Three overlaid lines showing message flow over time:
- **Blue**: Raw messages received per interval
- **Green**: Successfully parsed messages
- **Gold dashed**: 30-second rolling average

A gap between blue and green means messages are arriving but failing to parse. A drop to zero across all lines means the connection is dead.

### Stall & Reconnect Log

Event-by-event log of connection state changes. Use this to diagnose connectivity issues or see how quickly the system recovers from disconnections.

---

## Understanding Execution Tickets

### Arbitrage Tickets

An arbitrage ticket says: "Right now, you can buy YES on one venue and NO on the other for less than $1.00. The difference is your guaranteed profit."

**Inside the ticket:**

```
Leg 1: BUY YES on Polymarket at $0.45 for $1,000
Leg 2: BUY NO on Kalshi at $0.52 for $1,000

Expected Cost:   $970.00 (per $1,000 notional)
Expected Profit:   $9.40 (after fees)
```

**Before approving, verify:**

1. **Are prices still there?** Open both venues and check live quotes. Spreads close fast — often within minutes of detection. If the spread has narrowed below the fee threshold, expire the ticket.

2. **Is there enough liquidity?** Check the order book depth on both sides. If the books are thin (few contracts at the quoted price), you'll experience slippage. The **Depth** indicator on the Opportunities tab helps with this.

3. **Can you execute both legs quickly?** Arbitrage requires near-simultaneous execution. If you buy YES on Polymarket but Kalshi's NO price moves before you can buy, you're no longer hedged — you're making a directional bet.

4. **Is the profit worth the effort?** A $9.40 profit on $970 capital is 0.97%. After accounting for slippage (typically 0.5-2%), the real profit might be $0-5. Consider whether the risk of partial fills justifies the return.

### Flippening Tickets

A flippening ticket says: "This market just had an emotional spike away from its baseline. History suggests it will revert. Here's the trade."

**Inside the ticket:**

```
Leg 1 (Entry): BUY NO on Polymarket at $0.48 for $75
Leg 2 (Exit):  SELL NO at target $0.60, stop $0.41, max hold 45 min

Expected Cost:    $75.00
Expected Profit:  $28.13 (if target hit)
Category:         nba
Confidence:       80%
```

**Before approving, verify:**

1. **Is the spike real?** Check the live price on the Flippenings tab. If the price has already started reverting, you may be entering late. If it's still falling, the spike might not be emotional — it could be new information (injury, ejection).

2. **What's the confidence?** Scores above 75% indicate strong spike patterns (fast move, strong favorite, large deviation). Scores near 60% are marginal — the system barely passed its own threshold.

3. **What's the game state?** For sports markets, check:
   - Is the game in progress? (Ideal — live spikes revert fastest)
   - Is it late in the game? (Higher risk — less time for reversion)
   - Did something fundamental change? (Star player injured = new information, not a spike)

4. **Is the target realistic?** The target is typically 70% of the way back to baseline. If baseline was $0.65 and entry is $0.48, the target is $0.48 + ($0.65 - $0.48) x 0.70 = $0.60. Check whether the market has ever traded near $0.60 recently.

5. **What's your exit plan?** The system sets a stop loss at 15% below entry. If you enter at $0.48, the stop is at $0.41. Maximum loss per contract: $0.07. With $75 position: max loss ~$11. Are you comfortable with that?

### The Difference at a Glance

| | Arbitrage | Flippening |
|--|-----------|------------|
| **Risk** | Low (hedged both sides) | Medium (directional bet) |
| **Profit per trade** | Small (0.5-2%) | Larger (5-40%) |
| **Time to execute** | Seconds (both legs fast) | Minutes to hours |
| **Failure mode** | Partial fill (one leg only) | Market doesn't revert |
| **Capital needed** | Higher (buying both sides) | Lower (one side only) |
| **Venues** | Both Polymarket + Kalshi | Polymarket only |
| **Frequency** | Rare (efficient markets) | More frequent (event-driven) |

---

## Decision Framework

### Quick Decision Checklist for Arbitrage

- [ ] Spread still exists at quoted prices
- [ ] Both order books have sufficient depth
- [ ] Net profit exceeds likely slippage (1-2%)
- [ ] You can execute both legs within seconds
- [ ] Depth indicator shows checkmark (not warning)

**If any box is unchecked → Expire the ticket.**

### Quick Decision Checklist for Flippenings

- [ ] Confidence score > 70%
- [ ] Spike is emotional (no fundamental news)
- [ ] Event is still live with time remaining
- [ ] Price hasn't already started reverting
- [ ] You accept the stop-loss scenario (max loss)
- [ ] Order book has liquidity at your entry price

**If the spike is from real news → Expire the ticket.**
**If confidence is marginal and you're unsure → Expire the ticket.**

### Reading the Action Log

After executing a ticket, the action log in the detail modal tracks everything:

| Action | What Happened |
|--------|---------------|
| **approve** | You approved the ticket for execution |
| **execute** | You recorded the execution with actual prices |
| **expire** | You dismissed the ticket |
| **cancel** | You cancelled after approving |
| **annotate** | You added a note without changing status |

Each action shows a timestamp, and if execution data was recorded, the entry price and computed slippage. Use this log to review your decision quality over time. The summary metrics (win rate, avg slippage, total P&L) aggregate this data to show your performance trends.

### Slippage

Slippage is the difference between the price the system suggested and the price you actually got:

```
Slippage = Actual Entry Price - Suggested Entry Price
```

- **Positive slippage**: You paid more than expected (common in fast-moving markets)
- **Negative slippage**: You got a better price than expected (rare but possible)
- **Zero slippage**: You filled at exactly the suggested price

Typical slippage ranges:
- **Arbitrage**: 0.5-2% per leg (significant because profits are thin)
- **Flippening**: 1-5% (less impactful because profit targets are larger)

The **Avg Slippage** metric on the Tickets tab tracks your realized slippage over time. If it's consistently eating your profits, consider being more selective (only take tickets with wider spreads).
