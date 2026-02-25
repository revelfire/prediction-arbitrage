# Feature Specification: Flippening Engine (Mean Reversion on Live Sports)

**Feature**: `008-flippening-engine` | **Date**: 2026-02-25 | **Status**: Draft
**Depends on**: `006-dashboard-web-ui` (complete), `007-local-embeddings` (complete)

## Problem Statement

The scanner currently detects cross-venue arbitrage: risk-free spreads between Polymarket and Kalshi on the same event. This requires two venues to misprice the same contract. A completely different — and far more frequent — class of opportunity exists on Polymarket alone: **intra-venue mean reversion on live sporting events**.

During live games, casual bettors overreact emotionally to mid-game momentum shifts (a lucky punch, a scoring run, an early goal). These panic-driven trades push odds far from their statistically fair value. The odds almost always snap back within 15–30 minutes as the better team reasserts dominance. This is a "flippening" — a rapid, emotion-driven spike that mean-reverts.

The current system cannot detect these opportunities because:
1. It polls via REST every 60 seconds — too slow for events that spike and revert in minutes.
2. It has no concept of "fair value baseline" within a single venue — it only compares prices across venues.
3. It has no sports-specific filtering, game lifecycle tracking, or real-time price streaming.
4. It has no single-venue directional entry/exit logic — everything assumes hedged cross-venue pairs.

## Solution

Add a **parallel engine** alongside the existing cross-venue arb scanner that:
1. Connects to the Polymarket CLOB WebSocket for real-time price updates on sports markets.
2. Identifies sports markets with active (live) games and captures pre-game baseline odds.
3. Detects rapid price spikes (flippenings) that deviate significantly from baseline within a short window.
4. Generates entry signals with target exit prices based on mean reversion toward baseline.
5. Monitors open signals for reversion (exit) or stop-loss triggers.
6. Dispatches alerts through existing Slack/Discord webhooks and generates execution tickets for human operators.
7. Persists all flippening events, signals, and outcomes for analysis.

This engine shares infrastructure (Polymarket client, storage, notifications, dashboard, config) but introduces a new pipeline pattern: **real-time spike detection** on a single venue, as opposed to the existing **poll-based cross-venue matching**.

## Constitutional Notes

- **Principle I (Human-in-the-Loop)**: Preserved. The flippening engine generates execution tickets and alerts. It MUST NOT place orders. Automated execution is explicitly deferred to a future feature requiring a constitutional amendment.
- **Principle III (Async-First I/O)**: WebSocket connections are async I/O, consistent with this principle.
- **Principle V (Two-Pass Matching)**: Not applicable to this engine — no cross-venue matching is performed. The flippening engine introduces a complementary pipeline: real-time spike detection within a single venue.

## User Stories

### US1: Live Flippening Detection (P1)
**As a** market operator, **I want** to be alerted in real time when a sports market on Polymarket experiences an emotional overreaction spike, **so that** I can buy the dip before odds revert.

### US2: Entry Signal with Pricing (P1)
**As a** market operator, **I want** each flippening alert to include the entry price, target exit price, stop-loss level, and suggested position size, **so that** I can make a quick execution decision.

### US3: Exit Signal on Reversion (P1)
**As a** market operator, **I want** to be notified when a position I entered has reverted to the target exit price, **so that** I can close the position and lock in profits.

### US4: Game Schedule Awareness (P1)
**As a** market operator, **I want** the system to automatically discover sports markets with upcoming or live games and start monitoring them, **so that** I don't need to manually subscribe to individual markets.

### US5: Sport-Specific Tuning (P2)
**As a** market operator, **I want** spike detection thresholds to be configurable per sport (NBA, NHL, NFL, soccer, etc.), **so that** the system accounts for different volatility profiles across sports.

### US6: Flippening History (P2)
**As a** market operator, **I want** to review past flippenings with outcomes (reverted, stopped out, timed out), **so that** I can evaluate the strategy's performance over time.

### US7: Dashboard Flippenings Tab (P2)
**As a** market operator, **I want** a dedicated tab in the web dashboard showing active and recent flippenings with real-time price charts, **so that** I can visually monitor live games.

### US8: Execution Tickets for Flippenings (P1)
**As a** market operator, **I want** flippening signals to generate execution tickets (consistent with the existing ticket system), **so that** I can approve and track them through the same workflow.

## Functional Requirements

### FR-001: Polymarket WebSocket Client
The system MUST implement an async WebSocket client that connects to the Polymarket CLOB WebSocket API for real-time order book and trade updates. The client MUST:
- Subscribe to price updates for specific market token IDs
- Reconnect automatically on disconnect with exponential backoff (max 60s)
- Emit structured price update events consumable by the spike detector
- Respect rate limits and connection limits imposed by the CLOB API
- Log all connection state changes (connected, disconnected, reconnecting) via structlog

### FR-002: Sports Market Discovery
The system MUST filter Polymarket markets to identify sports events. Detection MUST use the Gamma API response metadata: `groupItemTitle`, `groupSlug`, tags, or category fields that indicate sports. A configurable allowlist of sport categories (default: `["nba", "nhl", "nfl", "mlb", "epl", "ufc", "tennis", "cricket", "college-basketball", "college-football", "esports"]`) MUST control which sports are monitored. Markets not matching any allowed sport MUST be ignored.

### FR-003: Game Lifecycle Manager
The system MUST track each sports market through a lifecycle: `upcoming` -> `live` -> `completed`. Transitions:
- `upcoming`: Market exists, game start time is in the future (or within a configurable pre-game window, default 30 minutes).
- `live`: Game start time has passed and market is still active (not resolved).
- `completed`: Market has resolved or game end time has passed.
The lifecycle manager MUST use the market's `expiry` field and any available start time metadata from the Gamma API (`startDate`, `endDate`, or event-level timestamps). If no explicit start time is available, the system MUST fall back to monitoring active sports markets and inferring "live" status from rapid price movement.

### FR-004: Baseline Odds Capture
The system MUST capture and store baseline odds for each sports market. Baseline capture occurs:
- At game start time (preferred): Record YES/NO mid-prices when the game transitions to `live`.
- On first connection (fallback): If the system connects to an already-live game, record the current price as baseline with a `late_join` flag.
Baselines MUST be stored in the `flippening_baselines` table with fields: `market_id`, `token_id`, `baseline_yes_price`, `baseline_no_price`, `captured_at`, `late_join` (bool), `sport`, `game_start_time`.

### FR-005: Spike Detection Engine
The system MUST implement a `SpikeDetector` that processes real-time price updates and detects flippenings. A flippening is detected when:
- The current YES price deviates from the baseline by more than `spike_threshold_pct` (default 15 percentage points, e.g., baseline 67% -> current 52% = 15pt drop).
- The deviation occurred within `spike_window_minutes` (default 10 minutes) — i.e., the price was near baseline recently.
- The spike direction is AGAINST the pre-game favorite (the side with higher baseline odds drops).

The detector MUST emit a `FlippeningEvent` containing: market info, baseline odds, spike magnitude (percentage points), spike direction, current price, timestamp, and sport.

### FR-006: Flippening Confidence Scoring
Each detected flippening MUST receive a confidence score (0.0–1.0) based on:
- **Spike magnitude**: Larger deviations from baseline = higher confidence (they're more likely emotional overreaction).
- **Baseline strength**: Higher pre-game favorite probability = higher confidence (67% favorites revert more reliably than 55% favorites).
- **Spike speed**: Faster moves (more points per minute) = higher confidence of emotional overreaction vs. genuine information.
- **Sport modifier**: Configurable per-sport multiplier (some sports revert more reliably than others).
The confidence formula MUST be documented in code and configurable via weights in config.

### FR-007: Entry Signal Generation
When a flippening is detected with confidence >= `min_confidence` (default 0.60), the system MUST generate an `EntrySignal`:
- `side`: Which side to buy (the side that dropped — buy the dip).
- `entry_price`: Current ask price for that side.
- `target_exit_price`: Price at which to sell, calculated as baseline minus `reversion_target_pct` (default 0.70, meaning we target 70% of the way back to baseline). Example: baseline 67%, spike to 52%, target = 52% + (67% - 52%) * 0.70 = 62.5%.
- `stop_loss_price`: Price below which to cut losses, calculated as `entry_price - stop_loss_pct * entry_price` (default `stop_loss_pct` = 0.15).
- `suggested_size_usd`: Position size based on configurable `base_position_usd` (default $100) scaled by confidence. MUST NOT exceed `max_position_usd` (default $500).
- `expected_profit_pct`: `(target_exit_price - entry_price) / entry_price`.
- `max_hold_minutes`: Maximum time to hold before timeout exit (default 45 minutes).

### FR-008: Reversion Monitor
After an entry signal is generated, the system MUST monitor the market for exit conditions. The monitor MUST check real-time price updates against three exit triggers:
1. **Target hit**: Current bid price >= `target_exit_price` → emit `ExitSignal` with `reason=reversion`.
2. **Stop-loss hit**: Current bid price <= `stop_loss_price` → emit `ExitSignal` with `reason=stop_loss`.
3. **Timeout**: `max_hold_minutes` elapsed since entry signal → emit `ExitSignal` with `reason=timeout`, using current bid price as exit price.
Each exit signal MUST include the realized P&L (exit_price - entry_price) as both absolute and percentage.

### FR-009: Execution Ticket Generation
For each entry signal, the system MUST generate an `ExecutionTicket` compatible with the existing ticket system. The ticket MUST include:
- `leg_1`: Buy order details (venue=polymarket, side, price, size).
- `leg_2`: Sell order details (venue=polymarket, side, target_exit_price, size) — this is a limit sell to be placed manually.
- `expected_cost`: entry_price * size.
- `expected_profit`: (target_exit_price - entry_price) * size.
- `ticket_type`: `"flippening"` (to distinguish from cross-venue arb tickets, which are `"arbitrage"`).
- `status`: `"pending"` (same lifecycle as existing tickets).

### FR-010: Alert Dispatch
Flippening alerts MUST dispatch through the existing `dispatch_webhook()` infrastructure. Two alert types:
- **Entry alert**: Fired when a flippening is detected. Includes: market title, sport, baseline odds, current odds, spike magnitude, confidence, entry price, target exit, suggested size, expected profit.
- **Exit alert**: Fired when an exit signal triggers. Includes: market title, reason (reversion/stop_loss/timeout), entry price, exit price, realized P&L, hold duration.
Both alert types MUST have distinct emoji/color to differentiate from existing arb and trend alerts. Entry alerts MUST be dispatched with urgency (no batching delay).

### FR-011: Flippening Persistence
The system MUST persist flippening data to PostgreSQL:
- `flippening_baselines` table: market_id, token_id, baseline prices, sport, game_start_time, captured_at, late_join.
- `flippening_events` table: id, market_id, baseline prices, spike_price, spike_magnitude_pct, spike_direction, confidence, sport, detected_at.
- `flippening_signals` table: id, event_id, signal_type (entry/exit), side, price, target_exit_price, stop_loss_price, suggested_size, exit_reason (null for entry), realized_pnl (null for entry), created_at.
Migration MUST be numbered sequentially after existing migrations.

### FR-012: Flippening Configuration
The system MUST add `FlippeningConfig` to `Settings` with fields:
- `enabled` (bool, default false — opt-in for v1)
- `sports` (list[str], default ["nba", "nhl", "nfl", "mlb", "epl", "ufc"])
- `spike_threshold_pct` (float, default 0.15 — 15 percentage points)
- `spike_window_minutes` (int, default 10)
- `min_confidence` (float, default 0.60)
- `reversion_target_pct` (float, default 0.70)
- `stop_loss_pct` (float, default 0.15)
- `base_position_usd` (float, default 100.0)
- `max_position_usd` (float, default 500.0)
- `max_hold_minutes` (int, default 45)
- `pre_game_window_minutes` (int, default 30)
- `ws_reconnect_max_seconds` (int, default 60)
- `sport_overrides` (dict[str, dict], default {} — per-sport threshold overrides)

### FR-013: CLI Commands
The system MUST add these CLI commands:

**`flip-watch`**: Start the flippening engine. Connects to WebSocket, discovers sports markets, monitors for flippenings, dispatches alerts. Options: `--sports` (comma-separated sport filter), `--min-confidence` (override), `--dry-run` (detect but don't persist or alert).

**`flip-history`**: Review past flippenings. Options: `--last N` (default 20), `--sport` (filter), `--outcome` (reversion/stop_loss/timeout), `--since ISO8601`, `--format (table|json)`.

**`flip-stats`**: Aggregated flippening performance. Options: `--sport` (filter), `--since ISO8601`. Output: total signals, win rate, avg profit per signal, avg hold time, profit by sport, profit by confidence bucket.

### FR-014: Dashboard Integration
The system MUST add a "Flippenings" tab to the existing web dashboard with:
- **Active flippenings**: Table of current entry signals awaiting reversion, with live price updates.
- **Recent history**: Table of completed flippenings with outcome, P&L, hold duration.
- **Sport breakdown**: Summary cards showing win rate and total P&L per sport.
Corresponding API endpoints:
- `GET /api/flippenings/active` — Current open signals.
- `GET /api/flippenings/history?limit=N&sport=X` — Completed flippenings.
- `GET /api/flippenings/stats?sport=X` — Aggregated performance.

### FR-015: Polymarket WebSocket API Integration
The WebSocket client MUST connect to the Polymarket CLOB WebSocket endpoint. The exact URL and subscription protocol MUST be determined during implementation by consulting:
1. The Polymarket CLOB API documentation (https://docs.polymarket.com/)
2. The `py-clob-client` open-source SDK for reference
If no WebSocket API is publicly available, the system MUST fall back to high-frequency REST polling of the CLOB `/book` endpoint at a configurable interval (default 5 seconds), using the existing `PolymarketClient.fetch_orderbook()` method with a dedicated rate limiter.

## Success Criteria

- SC-001: `flip-watch --dry-run` connects to Polymarket, discovers sports markets, and logs baseline captures for live games
- SC-002: Spike detector correctly identifies flippenings in synthetic test data (mocked WebSocket price stream with known spikes)
- SC-003: Entry signals include valid entry price, target exit, stop-loss, and position size within configured bounds
- SC-004: Reversion monitor correctly triggers exit signals for all three conditions (target hit, stop-loss, timeout) in mocked data
- SC-005: Execution tickets for flippenings are persisted and appear in the existing ticket management workflow (dashboard + CLI)
- SC-006: Slack/Discord alerts fire for both entry and exit signals with distinct formatting
- SC-007: `flip-history` and `flip-stats` return correct data from persisted flippenings
- SC-008: Dashboard "Flippenings" tab renders active signals and history
- SC-009: All existing tests still pass (no regressions)
- SC-010: All quality gates pass (ruff, mypy --strict, 70% coverage)

## Edge Cases

### EC-001: Late Join to Live Game
System connects to a game already in progress. Baseline is captured from current price with `late_join=true`. Confidence scoring MUST apply a penalty (configurable, default 0.8x multiplier) since we don't know the true opening odds.

### EC-002: Multiple Flippenings in Same Game
A single game may produce multiple spikes (e.g., lead changes). Each spike MUST be evaluated independently. The system MUST NOT generate a new entry signal if an existing signal for the same market is still open (awaiting exit).

### EC-003: Game Resolves During Monitoring
If the market resolves while a position is open (signal awaiting exit), the system MUST emit an `ExitSignal` with `reason=resolution` and the final resolution price ($1.00 or $0.00).

### EC-004: WebSocket Disconnection During Active Signal
If the WebSocket disconnects while signals are active, the system MUST attempt reconnection. If reconnection fails within `max_hold_minutes`, all open signals MUST be closed with `reason=disconnect` and the last known price.

### EC-005: No Sports Markets Available
If no sports markets match the configured sport allowlist, the system MUST log a warning and continue running (checking periodically for new markets).

### EC-006: Baseline Drift
If a game's odds drift significantly before a spike (e.g., due to an injury announcement), the system SHOULD update the baseline if the drift is gradual (< 2 points per minute over > 5 minutes). Sudden moves MUST NOT update the baseline — they may themselves be flippenings.

## Out of Scope

- Automated order placement on Polymarket (requires constitutional amendment to Principle I)
- Kalshi flippening detection (Kalshi's sports market volume is too low currently; can be added later)
- Cross-venue flippening arbitrage (buying the flippening dip on one venue while hedging on the other)
- Full backtesting framework with historical data replay (separate feature 009)
- Kelly Criterion position sizing based on historical win rate (requires backtesting data from feature 009)
- ML-based spike prediction or game outcome modeling
- Live game data integration (scores, play-by-play) from third-party sports APIs
