# Implementation Plan: Flippening Engine (Mean Reversion on Live Sports)

**Branch**: `008-flippening-engine` | **Date**: 2026-02-25 | **Spec**: [spec.md](spec.md)

## Summary

Add a parallel engine for detecting intra-venue mean reversion opportunities ("flippenings") on live Polymarket sports markets. The engine streams real-time prices via WebSocket (with REST polling fallback), captures pre-game baseline odds, detects emotional overreaction spikes, generates entry/exit signals with pricing, and dispatches alerts via existing Slack/Discord webhooks. Shares storage, notification, and dashboard infrastructure with the existing cross-venue arb scanner.

## Technical Context

**New Dependencies**: `websockets` (async WebSocket client, BSD license)
**New Package**: `src/arb_scanner/flippening/` (new subpackage — engine is large enough to warrant its own namespace)
**New Models**: `FlippeningEvent`, `EntrySignal`, `ExitSignal`, `GameState`, `GamePhase` enum, `ExitReason` enum, `SpikeDirection` enum, `FlippeningConfig`, `SportOverride`
**New Tables**: `flippening_baselines`, `flippening_events`, `flippening_signals` (migration 012)
**New CLI**: `flip-watch`, `flip-history`, `flip-stats` commands
**New API Routes**: `GET /api/flippenings/active`, `GET /api/flippenings/history`, `GET /api/flippenings/stats`
**Modified Modules**: `models/config.py`, `models/arbitrage.py` (ticket_type field), `cli/app.py`, `api/app.py`, `config.example.yaml`

## Constitution Check

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Human-in-the-Loop | PASS | Generates tickets + alerts, never places orders |
| II. Pydantic at Every Boundary | PASS | All new models are Pydantic v2 |
| III. Async-First I/O | PASS | WebSocket via websockets lib, REST via httpx, DB via asyncpg |
| IV. Structured Logging | PASS | structlog for all connection, detection, signal events |
| V. Two-Pass Matching | N/A | No cross-venue matching — parallel pipeline |
| VI. Configuration Over Code | PASS | All thresholds, sports, overrides in config.yaml |

## Project Structure (new/modified files)

```text
src/arb_scanner/
├── flippening/                     # NEW SUBPACKAGE
│   ├── __init__.py                 # NEW: public exports
│   ├── ws_client.py               # NEW: Polymarket WebSocket client (FR-001, FR-015)
│   ├── sports_filter.py           # NEW: sports market discovery + categorization (FR-002)
│   ├── game_manager.py            # NEW: game lifecycle tracking (FR-003, FR-004)
│   ├── spike_detector.py          # NEW: spike detection + confidence scoring (FR-005, FR-006)
│   ├── signal_generator.py        # NEW: entry/exit signal generation (FR-007, FR-008)
│   ├── orchestrator.py            # NEW: flip-watch main loop (wires everything together)
│   └── alert_formatter.py         # NEW: Slack/Discord payload builders for flip alerts (FR-010)
├── models/
│   ├── flippening.py              # NEW: all flippening data models (FR-005–FR-009)
│   └── config.py                  # MODIFY: add FlippeningConfig, SportOverride, add to Settings
├── models/
│   └── arbitrage.py               # MODIFY: add ticket_type field to ExecutionTicket (FR-009)
├── storage/
│   ├── _flippening_queries.py     # NEW: SQL constants for flippening tables
│   ├── flippening_repository.py   # NEW: FlippeningRepository class (FR-011)
│   └── migrations/
│       └── 012_create_flippening_tables.sql  # NEW (FR-011)
├── cli/
│   ├── flippening_commands.py     # NEW: register(app) with flip-watch, flip-history, flip-stats (FR-013)
│   └── app.py                     # MODIFY: register flippening commands
├── api/
│   ├── routes_flippening.py       # NEW: /api/flippenings/* endpoints (FR-014)
│   └── app.py                     # MODIFY: include flippening router
└── api/static/
    ├── index.html                 # MODIFY: add Flippenings tab
    └── app.js                     # MODIFY: add flippening tab logic

config.example.yaml                # MODIFY: add flippening section

tests/
├── unit/
│   ├── test_ws_client.py          # NEW: ~8 tests
│   ├── test_sports_filter.py      # NEW: ~10 tests
│   ├── test_game_manager.py       # NEW: ~12 tests
│   ├── test_spike_detector.py     # NEW: ~15 tests (core logic)
│   ├── test_signal_generator.py   # NEW: ~12 tests
│   ├── test_flip_orchestrator.py  # NEW: ~8 tests
│   ├── test_alert_formatter.py    # NEW: ~6 tests
│   └── test_flippening_models.py  # NEW: ~10 tests
├── integration/
│   └── test_flippening_pipeline.py # NEW: ~10 tests (mocked WS → spike → signal → persist)
```

## Key Technical Decisions

### 1. New `flippening/` Subpackage (Not Inline in `engine/`)

The flippening engine is architecturally distinct from the cross-venue arb engine: different data flow (streaming vs polling), different pipeline (spike detection vs matching), different risk model (directional vs hedged). Placing it in `engine/calculator.py` or similar would violate single-responsibility and blow past the 300-line module limit. A dedicated subpackage keeps the existing engine untouched and follows the same modular pattern as `matching/` and `notifications/`.

### 2. WebSocket-First with REST Polling Fallback

Primary: Connect to Polymarket CLOB WebSocket at `wss://ws-subscriptions-clob.polymarket.com/ws/market` for sub-second price updates. The `websockets` library (already widely used in the Python async ecosystem) provides a clean async context manager API.

Fallback: If WebSocket connection fails or is unavailable, fall back to polling `PolymarketClient.fetch_orderbook()` every 5 seconds per market. This uses a dedicated `RateLimiter` instance (separate from the scan loop's rate limiter) to avoid contention.

The `ws_client.py` module abstracts both modes behind a unified `PriceStream` async iterator interface:

```python
class PriceStream(Protocol):
    async def subscribe(self, token_ids: list[str]) -> None: ...
    async def __aiter__(self) -> AsyncIterator[PriceUpdate]: ...
    async def close(self) -> None: ...
```

### 3. Sports Market Detection via Gamma API Metadata

The Gamma API returns `groupSlug` and tag-like fields on each market. Sports markets have slugs like `nba-*`, `nhl-*`, `epl-*`, `ufc-*`. The `sports_filter.py` module:
1. Fetches active Polymarket markets via the existing `PolymarketClient.fetch_markets()`.
2. Inspects `raw_data["groupSlug"]`, `raw_data["tags"]`, and `raw_data["groupItemTitle"]` for sport keyword matches.
3. Returns `list[SportsMarket]` — a thin wrapper around `Market` adding `sport: str` and `game_start_time: datetime | None`.

Sport detection is keyword-based (configurable allowlist), not ML/LLM. Fast, cheap, deterministic.

### 4. Game Lifecycle as a State Machine

```
upcoming ──(start_time passed)──→ live ──(resolved or expired)──→ completed
    │                               │
    └──(no start_time + rapid       └──(disconnect timeout)──→ completed
        price movement)──→ live
```

`GameManager` holds a `dict[str, GameState]` keyed by market event_id. Each `GameState` tracks:
- `phase: GamePhase` (upcoming/live/completed)
- `baseline: Baseline | None` (captured on live transition)
- `active_signal: EntrySignal | None` (one open signal per game, EC-002)
- `price_history: deque[PriceUpdate]` (rolling window for spike detection)
- `entered_live_at: datetime | None`

On each price update, `GameManager.process(update)` advances the state machine and delegates to `SpikeDetector` for live games.

### 5. Spike Detection Algorithm

The `SpikeDetector` processes a stream of `PriceUpdate` events for a single game:

```python
def check_spike(self, update: PriceUpdate, baseline: Baseline) -> FlippeningEvent | None:
    deviation = baseline.yes_price - update.yes_price  # positive = favorite dropped
    if deviation < self.config.spike_threshold_pct:
        return None  # not enough deviation

    # Check recency: was price near baseline within spike_window_minutes?
    recent_near_baseline = any(
        abs(p.yes_price - baseline.yes_price) < 0.05
        for p in self._recent_prices(within_minutes=self.config.spike_window_minutes)
    )
    if not recent_near_baseline:
        return None  # gradual drift, not a spike

    # Check direction: spike must be AGAINST the pre-game favorite
    if baseline.yes_price < 0.50 and deviation > 0:
        return None  # underdog dropping further isn't a flippening

    confidence = self._score_confidence(deviation, baseline, update)
    if confidence < self.config.min_confidence:
        return None

    return FlippeningEvent(...)
```

### 6. Confidence Scoring Formula

```python
def _score_confidence(self, deviation: float, baseline: Baseline, update: PriceUpdate) -> float:
    # Component 1: Spike magnitude (0-1, larger = higher)
    magnitude_score = min(deviation / 0.30, 1.0)  # caps at 30pt deviation

    # Component 2: Baseline strength (0-1, stronger favorite = higher)
    strength_score = max(baseline.yes_price - 0.50, 0.0) / 0.50  # 50% → 0, 100% → 1

    # Component 3: Spike speed (0-1, faster = higher)
    minutes_elapsed = (update.timestamp - self._first_move_time).total_seconds() / 60
    speed_score = min(1.0 / max(minutes_elapsed, 0.5), 1.0)  # instant → 1.0

    # Component 4: Sport modifier (configurable, default 1.0)
    sport_mod = self.config.sport_overrides.get(baseline.sport, {}).get("confidence_modifier", 1.0)

    # Weighted average (configurable weights)
    raw = (
        self.weights.magnitude * magnitude_score
        + self.weights.strength * strength_score
        + self.weights.speed * speed_score
    ) * sport_mod

    # Late join penalty
    if baseline.late_join:
        raw *= self.config.late_join_penalty  # default 0.8

    return min(max(raw, 0.0), 1.0)
```

Default weights: `magnitude=0.45, strength=0.30, speed=0.25`. These live in config, not code.

### 7. Entry/Exit Signal Flow

```
SpikeDetector.check_spike() → FlippeningEvent
    │
    ├─ GameManager checks: no open signal for this game (EC-002)
    │
    ├─ SignalGenerator.create_entry(event, config) → EntrySignal
    │   ├─ side = "yes" if favorite dropped, "no" if underdog
    │   ├─ entry_price = current ask for that side
    │   ├─ target_exit = entry + (baseline - entry) * reversion_target_pct
    │   ├─ stop_loss = entry * (1 - stop_loss_pct)
    │   └─ size = min(base_position * confidence, max_position)
    │
    ├─ Generate ExecutionTicket (ticket_type="flippening")
    │
    ├─ Dispatch entry alert (Slack/Discord)
    │
    ├─ Persist event + signal + ticket
    │
    └─ GameManager stores active_signal
        │
        ├─ (subsequent price updates) → ReversionMonitor.check_exit()
        │   ├─ bid >= target_exit → ExitSignal(reason=reversion)
        │   ├─ bid <= stop_loss → ExitSignal(reason=stop_loss)
        │   └─ elapsed >= max_hold → ExitSignal(reason=timeout)
        │
        └─ On ExitSignal:
            ├─ Calculate realized P&L
            ├─ Dispatch exit alert
            ├─ Persist exit signal
            └─ Clear active_signal (allow next flippening in same game)
```

### 8. Baseline Drift Handling (EC-006)

The `GameManager` tracks a `drift_accumulator` per game. On each price update:
- If `abs(price_delta_per_minute) < 2.0 points` sustained for > 5 minutes, update baseline to current price (gradual drift = new information, not overreaction).
- If `abs(price_delta) >= spike_threshold` within a short window, do NOT update baseline — evaluate as potential flippening instead.

This prevents the system from chasing a slowly shifting game while still detecting genuine emotional spikes.

### 9. ExecutionTicket Backward Compatibility

Add an optional `ticket_type: str = "arbitrage"` field to the existing `ExecutionTicket` model. Default remains `"arbitrage"` so all existing tickets are unaffected. Flippening tickets set `ticket_type="flippening"`. The dashboard and CLI filter/display by type.

### 10. Flippening Repository (Separate Class, Same Pool)

Following the `Repository` / `AnalyticsRepository` split pattern, create `FlippeningRepository` with its own class and query file. It shares the same `asyncpg.Pool` (passed at construction). This avoids bloating `repository.py` beyond 300 lines and keeps flippening logic cleanly separated.

## Data Flow

```
flip-watch CLI command
  │
  ├─ Load config (FlippeningConfig from Settings)
  │
  ├─ Sports Discovery (one-time + periodic refresh every 5 min)
  │   └─ PolymarketClient.fetch_markets() → sports_filter.classify() → list[SportsMarket]
  │
  ├─ Connect PriceStream (WebSocket or REST polling fallback)
  │   └─ Subscribe to token_ids of live/upcoming sports markets
  │
  ├─ GameManager.initialize(sports_markets)
  │   └─ For each market: create GameState(phase=upcoming|live)
  │   └─ For live markets: capture baseline (late_join=true if already started)
  │
  ├─ Main Loop: async for update in price_stream
  │   │
  │   ├─ GameManager.process(update)
  │   │   ├─ Advance lifecycle if needed (upcoming→live, live→completed)
  │   │   ├─ Capture baseline on live transition
  │   │   ├─ Update drift accumulator
  │   │   ├─ SpikeDetector.check_spike(update, baseline) → FlippeningEvent?
  │   │   └─ ReversionMonitor.check_exit(update, active_signal) → ExitSignal?
  │   │
  │   ├─ On FlippeningEvent (if no open signal):
  │   │   ├─ SignalGenerator.create_entry() → EntrySignal
  │   │   ├─ Generate ExecutionTicket
  │   │   ├─ dispatch_flip_alert(entry_alert)
  │   │   └─ repo.insert_flippening_event() + repo.insert_flippening_signal()
  │   │
  │   ├─ On ExitSignal:
  │   │   ├─ Calculate realized P&L
  │   │   ├─ dispatch_flip_alert(exit_alert)
  │   │   └─ repo.insert_flippening_signal(exit)
  │   │
  │   └─ On game completed: remove from GameManager
  │
  └─ Periodic: refresh sports markets (discover new games, prune completed)
```

## SQL Design

### Migration 012: Create flippening tables

```sql
-- Baseline odds captured at game start
CREATE TABLE IF NOT EXISTS flippening_baselines (
    id              BIGSERIAL PRIMARY KEY,
    market_id       TEXT NOT NULL,
    token_id        TEXT NOT NULL,
    baseline_yes    NUMERIC(10,6) NOT NULL,
    baseline_no     NUMERIC(10,6) NOT NULL,
    sport           TEXT NOT NULL,
    game_start_time TIMESTAMPTZ,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    late_join       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_flip_baselines_market
    ON flippening_baselines (market_id);

-- Detected flippening events (spikes)
CREATE TABLE IF NOT EXISTS flippening_events (
    id              TEXT PRIMARY KEY,
    market_id       TEXT NOT NULL,
    market_title    TEXT NOT NULL,
    baseline_yes    NUMERIC(10,6) NOT NULL,
    spike_price     NUMERIC(10,6) NOT NULL,
    spike_magnitude NUMERIC(10,6) NOT NULL,
    spike_direction TEXT NOT NULL,
    confidence      NUMERIC(10,6) NOT NULL,
    sport           TEXT NOT NULL,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flip_events_detected
    ON flippening_events (detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_flip_events_sport
    ON flippening_events (sport, detected_at DESC);

-- Entry and exit signals
CREATE TABLE IF NOT EXISTS flippening_signals (
    id              TEXT PRIMARY KEY,
    event_id        TEXT NOT NULL REFERENCES flippening_events(id),
    signal_type     TEXT NOT NULL,  -- 'entry' or 'exit'
    side            TEXT NOT NULL,  -- 'yes' or 'no'
    price           NUMERIC(10,6) NOT NULL,
    target_exit     NUMERIC(10,6),
    stop_loss       NUMERIC(10,6),
    suggested_size  NUMERIC(12,2),
    exit_reason     TEXT,  -- null for entry; 'reversion', 'stop_loss', 'timeout', 'resolution', 'disconnect'
    realized_pnl    NUMERIC(12,6),  -- null for entry
    hold_minutes    NUMERIC(10,2),  -- null for entry
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flip_signals_event
    ON flippening_signals (event_id);

CREATE INDEX IF NOT EXISTS idx_flip_signals_created
    ON flippening_signals (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_flip_signals_type
    ON flippening_signals (signal_type, created_at DESC);
```

## New Pydantic Models

```python
# models/flippening.py

class GamePhase(str, Enum):
    UPCOMING = "upcoming"
    LIVE = "live"
    COMPLETED = "completed"

class SpikeDirection(str, Enum):
    FAVORITE_DROP = "favorite_drop"    # pre-game favorite price fell
    UNDERDOG_RISE = "underdog_rise"    # underdog price spiked up

class ExitReason(str, Enum):
    REVERSION = "reversion"
    STOP_LOSS = "stop_loss"
    TIMEOUT = "timeout"
    RESOLUTION = "resolution"
    DISCONNECT = "disconnect"

class PriceUpdate(BaseModel):
    market_id: str
    token_id: str
    yes_bid: Decimal
    yes_ask: Decimal
    no_bid: Decimal
    no_ask: Decimal
    timestamp: datetime

class Baseline(BaseModel):
    market_id: str
    token_id: str
    yes_price: Decimal
    no_price: Decimal
    sport: str
    game_start_time: datetime | None
    captured_at: datetime
    late_join: bool

class FlippeningEvent(BaseModel):
    id: str  # uuid
    market_id: str
    market_title: str
    baseline_yes: Decimal
    spike_price: Decimal
    spike_magnitude_pct: Decimal
    spike_direction: SpikeDirection
    confidence: Decimal
    sport: str
    detected_at: datetime

class EntrySignal(BaseModel):
    id: str
    event_id: str
    side: str  # "yes" or "no"
    entry_price: Decimal
    target_exit_price: Decimal
    stop_loss_price: Decimal
    suggested_size_usd: Decimal
    expected_profit_pct: Decimal
    max_hold_minutes: int
    created_at: datetime

class ExitSignal(BaseModel):
    id: str
    event_id: str
    side: str
    exit_price: Decimal
    exit_reason: ExitReason
    realized_pnl: Decimal
    realized_pnl_pct: Decimal
    hold_minutes: Decimal
    created_at: datetime
```

## Config YAML Addition

```yaml
flippening:
  enabled: false
  sports:
    - nba
    - nhl
    - nfl
    - mlb
    - epl
    - ufc
  spike_threshold_pct: 0.15
  spike_window_minutes: 10
  min_confidence: 0.60
  reversion_target_pct: 0.70
  stop_loss_pct: 0.15
  base_position_usd: 100.0
  max_position_usd: 500.0
  max_hold_minutes: 45
  pre_game_window_minutes: 30
  ws_reconnect_max_seconds: 60
  late_join_penalty: 0.80
  confidence_weights:
    magnitude: 0.45
    strength: 0.30
    speed: 0.25
  sport_overrides:
    nfl:
      spike_threshold_pct: 0.12
      confidence_modifier: 1.1
    nba:
      spike_threshold_pct: 0.15
      confidence_modifier: 1.0
```

## Webhook Alert Payloads

### Entry Alert

| Platform | Emoji/Color | Header |
|----------|------------|--------|
| Slack | :rotating_light: | Flippening Detected |
| Discord | Orange (15105570) | Flippening Detected |

Fields: Market, Sport, Baseline Odds, Current Odds, Spike (pts), Confidence, Entry Price, Target Exit, Size, Expected Profit

### Exit Alert

| Platform | Emoji/Color | Header |
|----------|------------|--------|
| Slack (reversion) | :moneybag: | Flippening Reverted — Profit |
| Slack (stop_loss) | :x: | Flippening Stop-Loss Hit |
| Slack (timeout) | :hourglass: | Flippening Timed Out |
| Discord (reversion) | Green (3066993) | Flippening Reverted — Profit |
| Discord (stop_loss) | Red (15158332) | Flippening Stop-Loss Hit |
| Discord (timeout) | Gray (9807270) | Flippening Timed Out |

Fields: Market, Reason, Entry Price, Exit Price, P&L ($), P&L (%), Hold Time

## API Endpoints

```python
# routes_flippening.py

@router.get("/api/flippenings/active")
async def active_flippenings(repo) -> list[dict]:
    """Open entry signals awaiting exit."""

@router.get("/api/flippenings/history")
async def flippening_history(limit: int = 50, sport: str | None = None, repo) -> list[dict]:
    """Completed flippenings with outcomes."""

@router.get("/api/flippenings/stats")
async def flippening_stats(sport: str | None = None, since: str | None = None, repo) -> dict:
    """Aggregated performance: win rate, avg profit, by sport."""
```

## Implementation Phases

### Phase 1: Foundation (Models + Config + Storage)
Create all Pydantic models, FlippeningConfig, migration 012, FlippeningRepository. This is the data layer with no runtime behavior yet.

### Phase 2: Market Discovery + Game Lifecycle
Implement `sports_filter.py` and `game_manager.py`. At this point, `flip-watch --dry-run` can discover sports markets and track lifecycles.

### Phase 3: Price Streaming
Implement `ws_client.py` with WebSocket connection and REST polling fallback. Unified `PriceStream` interface.

### Phase 4: Spike Detection + Confidence Scoring
Implement `spike_detector.py`. This is the core algorithm — most tests target this module.

### Phase 5: Signal Generation + Reversion Monitoring
Implement `signal_generator.py` including entry signals, exit monitoring, and execution ticket generation.

### Phase 6: Alerting + Persistence
Implement `alert_formatter.py`, wire up `dispatch_webhook()`, wire up repository persistence. `flip-watch` is now fully functional.

### Phase 7: CLI + Dashboard + API
Implement `flippening_commands.py`, `routes_flippening.py`, dashboard tab. Read-only UI over the persisted data.

### Phase 8: Edge Cases + Polish
Handle EC-001 through EC-006, add integration tests, ensure all quality gates pass.
