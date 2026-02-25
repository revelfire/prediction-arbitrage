# Tasks: Flippening Engine (Mean Reversion on Live Sports)

**Input**: `/specs/008-flippening-engine/spec.md`, `/specs/008-flippening-engine/plan.md`
**Depends on**: `006-dashboard-web-ui` (complete), `007-local-embeddings` (complete)

## Autonomous Execution Notes

- Fix bugs as you find them
- Greenfield pre-1.0 — modify existing code directly
- All existing mocked tests MUST continue to pass
- Flippening tests use mocked data, no live API calls
- New dependency: `websockets` — add via `uv add websockets`

---

## Phase 1: Foundation (Models + Config + Storage)

- [x] T001 Create `src/arb_scanner/models/flippening.py` with all data models and enums:
  - `GamePhase(str, Enum)`: `UPCOMING`, `LIVE`, `COMPLETED`
  - `SpikeDirection(str, Enum)`: `FAVORITE_DROP`, `UNDERDOG_RISE`
  - `ExitReason(str, Enum)`: `REVERSION`, `STOP_LOSS`, `TIMEOUT`, `RESOLUTION`, `DISCONNECT`
  - `PriceUpdate(BaseModel)`: `market_id` (str), `token_id` (str), `yes_bid` (Decimal), `yes_ask` (Decimal), `no_bid` (Decimal), `no_ask` (Decimal), `timestamp` (datetime). Validator: all prices in [0.0, 1.0].
  - `Baseline(BaseModel)`: `market_id` (str), `token_id` (str), `yes_price` (Decimal), `no_price` (Decimal), `sport` (str), `game_start_time` (datetime | None), `captured_at` (datetime), `late_join` (bool, default False).
  - `SportsMarket(BaseModel)`: `market` (Market), `sport` (str), `game_start_time` (datetime | None), `token_id` (str).
  - `FlippeningEvent(BaseModel)`: `id` (str, uuid default), `market_id` (str), `market_title` (str), `baseline_yes` (Decimal), `spike_price` (Decimal), `spike_magnitude_pct` (Decimal), `spike_direction` (SpikeDirection), `confidence` (Decimal), `sport` (str), `detected_at` (datetime).
  - `EntrySignal(BaseModel)`: `id` (str, uuid default), `event_id` (str), `side` (str), `entry_price` (Decimal), `target_exit_price` (Decimal), `stop_loss_price` (Decimal), `suggested_size_usd` (Decimal), `expected_profit_pct` (Decimal), `max_hold_minutes` (int), `created_at` (datetime). Validators: `side` in {"yes", "no"}, `entry_price` in [0, 1], `target_exit_price` > `entry_price`, `stop_loss_price` < `entry_price`.
  - `ExitSignal(BaseModel)`: `id` (str, uuid default), `event_id` (str), `side` (str), `exit_price` (Decimal), `exit_reason` (ExitReason), `realized_pnl` (Decimal), `realized_pnl_pct` (Decimal), `hold_minutes` (Decimal), `created_at` (datetime).

- [x] T002 Add `ConfidenceWeights`, `SportOverride`, and `FlippeningConfig` to `src/arb_scanner/models/config.py`:
  - `ConfidenceWeights(BaseModel)`: `magnitude` (float, default 0.45), `strength` (float, default 0.30), `speed` (float, default 0.25). Validator: sum of weights must equal 1.0 (within tolerance 0.01).
  - `SportOverride(BaseModel)`: `spike_threshold_pct` (float | None, default None), `confidence_modifier` (float, default 1.0), `min_confidence` (float | None, default None).
  - `FlippeningConfig(BaseModel)`: `enabled` (bool, default False), `sports` (list[str], default ["nba", "nhl", "nfl", "mlb", "epl", "ufc"]), `spike_threshold_pct` (float, default 0.15), `spike_window_minutes` (int, default 10), `min_confidence` (float, default 0.60), `reversion_target_pct` (float, default 0.70), `stop_loss_pct` (float, default 0.15), `base_position_usd` (float, default 100.0), `max_position_usd` (float, default 500.0), `max_hold_minutes` (int, default 45), `pre_game_window_minutes` (int, default 30), `ws_reconnect_max_seconds` (int, default 60), `late_join_penalty` (float, default 0.80), `confidence_weights` (ConfidenceWeights, default factory), `sport_overrides` (dict[str, SportOverride], default {}), `polling_interval_seconds` (float, default 5.0).
  - Add `flippening: FlippeningConfig = FlippeningConfig()` to `Settings`.

- [x] T003 [P] Add `ticket_type: str = "arbitrage"` field to `ExecutionTicket` in `src/arb_scanner/models/arbitrage.py`. Add validator: `ticket_type` must be in `{"arbitrage", "flippening"}`. Default is `"arbitrage"` so existing tickets are unaffected.

- [x] T004 Create `src/arb_scanner/storage/migrations/012_create_flippening_tables.sql`:
  - `flippening_baselines` table: `id` BIGSERIAL PK, `market_id` TEXT NOT NULL, `token_id` TEXT NOT NULL, `baseline_yes` NUMERIC(10,6) NOT NULL, `baseline_no` NUMERIC(10,6) NOT NULL, `sport` TEXT NOT NULL, `game_start_time` TIMESTAMPTZ, `captured_at` TIMESTAMPTZ NOT NULL DEFAULT NOW(), `late_join` BOOLEAN NOT NULL DEFAULT FALSE. Index on `(market_id)`.
  - `flippening_events` table: `id` TEXT PK, `market_id` TEXT NOT NULL, `market_title` TEXT NOT NULL, `baseline_yes` NUMERIC(10,6) NOT NULL, `spike_price` NUMERIC(10,6) NOT NULL, `spike_magnitude` NUMERIC(10,6) NOT NULL, `spike_direction` TEXT NOT NULL, `confidence` NUMERIC(10,6) NOT NULL, `sport` TEXT NOT NULL, `detected_at` TIMESTAMPTZ NOT NULL DEFAULT NOW(). Indexes on `(detected_at DESC)` and `(sport, detected_at DESC)`.
  - `flippening_signals` table: `id` TEXT PK, `event_id` TEXT NOT NULL REFERENCES flippening_events(id), `signal_type` TEXT NOT NULL, `side` TEXT NOT NULL, `price` NUMERIC(10,6) NOT NULL, `target_exit` NUMERIC(10,6), `stop_loss` NUMERIC(10,6), `suggested_size` NUMERIC(12,2), `exit_reason` TEXT, `realized_pnl` NUMERIC(12,6), `hold_minutes` NUMERIC(10,2), `created_at` TIMESTAMPTZ NOT NULL DEFAULT NOW(). Indexes on `(event_id)`, `(created_at DESC)`, `(signal_type, created_at DESC)`.

- [x] T005 Create `src/arb_scanner/storage/_flippening_queries.py` with SQL constants:
  - `INSERT_BASELINE`: Insert into flippening_baselines.
  - `INSERT_EVENT`: Insert into flippening_events.
  - `INSERT_SIGNAL`: Insert into flippening_signals.
  - `GET_ACTIVE_SIGNALS`: Select entry signals that have no corresponding exit signal (LEFT JOIN flippening_signals exit ON entry.event_id = exit.event_id AND exit.signal_type = 'exit' WHERE exit.id IS NULL), ordered by created_at DESC.
  - `GET_HISTORY`: Select completed flippenings (entry JOIN exit on event_id) with optional sport filter, ordered by exit.created_at DESC, limit N.
  - `GET_STATS`: Aggregate query: count, win_rate (exit_reason='reversion'), avg_pnl, avg_hold_minutes, grouped by sport. Optional sport filter and since timestamp.
  - `GET_RECENT_EVENTS`: Select from flippening_events, ordered by detected_at DESC, limit N. Optional sport filter.

- [x] T006 Create `src/arb_scanner/storage/flippening_repository.py` with `FlippeningRepository` class:
  - Constructor takes `asyncpg.Pool`.
  - `async def insert_baseline(self, baseline: Baseline) -> None`
  - `async def insert_event(self, event: FlippeningEvent) -> None`
  - `async def insert_signal(self, signal: EntrySignal | ExitSignal) -> None` — detect type from signal_type field or isinstance check.
  - `async def get_active_signals(self, limit: int = 50) -> list[dict[str, Any]]`
  - `async def get_history(self, limit: int = 50, sport: str | None = None) -> list[dict[str, Any]]`
  - `async def get_stats(self, sport: str | None = None, since: datetime | None = None) -> dict[str, Any]`
  - `async def get_recent_events(self, limit: int = 50, sport: str | None = None) -> list[dict[str, Any]]`
  - All methods use queries from `_flippening_queries.py`. Return `dict[str, Any]` for flexibility.

- [x] T007 [P] Extend `config.example.yaml` with `flippening` section matching the FlippeningConfig defaults (enabled: false, sports list, all thresholds, confidence_weights, sport_overrides example for nfl/nba).

- [x] T008 Create `tests/unit/test_flippening_models.py` (~10 tests):
  - Test PriceUpdate rejects prices outside [0, 1]
  - Test PriceUpdate accepts valid prices
  - Test Baseline model round-trips correctly
  - Test FlippeningEvent uuid generation
  - Test EntrySignal validates side in {"yes", "no"}
  - Test EntrySignal rejects target_exit <= entry_price
  - Test EntrySignal rejects stop_loss >= entry_price
  - Test ExitSignal with each ExitReason variant
  - Test SportsMarket wraps Market correctly
  - Test ConfidenceWeights validator rejects sum != 1.0
  - Test FlippeningConfig defaults are correct
  - Test ExecutionTicket ticket_type defaults to "arbitrage"
  - Test ExecutionTicket accepts "flippening" ticket_type

**Quality gate**: All 5 gates. Existing tests must pass.

---

## Phase 2: Sports Market Discovery + Game Lifecycle

- [x] T009 Create `src/arb_scanner/flippening/__init__.py` with docstring and public re-exports (populated as modules are added).

- [x] T010 Create `src/arb_scanner/flippening/sports_filter.py`:
  - `classify_sports_markets(markets: list[Market], allowed_sports: list[str]) -> list[SportsMarket]`: Iterates markets, inspects `market.raw_data` for sport indicators.
  - `_detect_sport(raw_data: dict[str, object], allowed: set[str]) -> str | None`: Checks `raw_data["groupSlug"]` for sport prefix matches (e.g., slug starting with "nba-", "nhl-", "epl-"). Falls back to checking `raw_data.get("tags", [])` and `raw_data.get("groupItemTitle", "")` for keyword matches against allowed set. Returns lowercase sport string or None.
  - `_extract_game_start(raw_data: dict[str, object]) -> datetime | None`: Parses `raw_data.get("startDate")` or `raw_data.get("game_start_time")` as ISO datetime. Returns None if not present or unparseable.
  - `_extract_token_id(raw_data: dict[str, object]) -> str`: Extracts `clobTokenIds` from raw_data (JSON string), returns first token ID. Falls back to `raw_data.get("conditionId", "")`.

- [x] T011 Create `src/arb_scanner/flippening/game_manager.py`:
  - `GameState` dataclass (not Pydantic — internal state, not a boundary): `market_id` (str), `market_title` (str), `token_id` (str), `sport` (str), `phase` (GamePhase), `baseline` (Baseline | None), `active_signal` (EntrySignal | None), `price_history` (deque[PriceUpdate], maxlen=200), `game_start_time` (datetime | None), `entered_live_at` (datetime | None), `drift_accumulator` (list[tuple[datetime, Decimal]], tracks recent gradual price changes).
  - `GameManager` class:
    - Constructor takes `FlippeningConfig`, `SpikeDetector`, `SignalGenerator`.
    - `_games: dict[str, GameState]` keyed by market_id.
    - `initialize(sports_markets: list[SportsMarket]) -> None`: Create GameState for each market. Set phase=UPCOMING if game_start_time is in the future (or within pre_game_window), phase=LIVE if game_start_time has passed and market not resolved.
    - `process(update: PriceUpdate) -> tuple[FlippeningEvent | None, ExitSignal | None]`: Main dispatch. Advance lifecycle, update price_history, delegate to spike detector (if live + no active signal) and reversion monitor (if live + active signal). Return any event/exit produced.
    - `_advance_lifecycle(state: GameState, update: PriceUpdate) -> None`: Transition upcoming→live when start_time passed (capture baseline). Transition live→completed when market resolved.
    - `_capture_baseline(state: GameState, update: PriceUpdate, late_join: bool) -> Baseline`: Build Baseline from current mid-prices.
    - `_update_drift(state: GameState, update: PriceUpdate) -> None`: Track gradual price changes. If drift < 2pts/min over > 5 min, update baseline (EC-006).
    - `remove_game(market_id: str) -> None`: Remove completed game from _games.
    - `active_game_count` property: Return number of non-completed games.
    - `has_open_signal(market_id: str) -> bool`: Check if game has active_signal set.

- [x] T012 Create `tests/unit/test_sports_filter.py` (~10 tests):
  - Test classify with NBA slug in raw_data returns sport="nba"
  - Test classify with NFL slug returns sport="nfl"
  - Test classify ignores market with no sport indicators
  - Test classify respects allowed_sports filter (ignores cricket if not in list)
  - Test _detect_sport matches groupSlug prefix
  - Test _detect_sport falls back to tags
  - Test _detect_sport falls back to groupItemTitle keywords
  - Test _extract_game_start parses ISO datetime
  - Test _extract_game_start returns None for missing/invalid
  - Test _extract_token_id parses clobTokenIds JSON string

- [x] T013 Create `tests/unit/test_game_manager.py` (~12 tests):
  - Test initialize sets UPCOMING for future games
  - Test initialize sets LIVE for past-start games (with late_join baseline)
  - Test process advances UPCOMING→LIVE when start_time passes
  - Test baseline captured on LIVE transition
  - Test late_join flag set when connecting to already-live game
  - Test process delegates to spike_detector for LIVE games without active signal
  - Test process delegates to reversion monitor for LIVE games with active signal
  - Test process skips spike detection when active_signal exists (EC-002)
  - Test remove_game clears state
  - Test _update_drift updates baseline on gradual movement (EC-006)
  - Test _update_drift does NOT update baseline on sharp spike
  - Test has_open_signal returns correct bool

**Quality gate**: All 5 gates. Existing tests must pass.

---

## Phase 3: Price Streaming

- [x] T014 Add `websockets` dependency: `uv add websockets`.

- [x] T015 Create `src/arb_scanner/flippening/ws_client.py`:
  - `PriceStream(Protocol)`: defines `async def subscribe(self, token_ids: list[str]) -> None`, `def __aiter__(self) -> AsyncIterator[PriceUpdate]`, `async def __anext__(self) -> PriceUpdate`, `async def close(self) -> None`.
  - `WebSocketPriceStream` class:
    - Constructor takes `ws_url: str` (default `wss://ws-subscriptions-clob.polymarket.com/ws/market`), `reconnect_max_seconds: int`.
    - `_connection`: websockets connection (nullable).
    - `_subscribed_tokens: set[str]`.
    - `_queue: asyncio.Queue[PriceUpdate]` — internal buffer for parsed updates.
    - `async def subscribe(self, token_ids: list[str]) -> None`: Connect to WS, send subscription messages per CLOB protocol. Store token_ids. Start `_reader_task`.
    - `_reader_task`: Background asyncio.Task that reads WS messages, parses JSON into PriceUpdate, puts on queue. On disconnect, attempts reconnect with exponential backoff (1s, 2s, 4s, ... up to reconnect_max_seconds). On successful reconnect, re-subscribes to all tokens.
    - `__aiter__` / `__anext__`: Yield from _queue.
    - `async def close()`: Cancel reader task, close WS connection.
    - All state changes logged via structlog.
  - `PollingPriceStream` class (REST fallback):
    - Constructor takes `PolymarketClient` and `interval_seconds: float` (default 5.0).
    - `_polling_task`: Background asyncio.Task that polls `fetch_orderbook()` for each subscribed token at interval, parses into PriceUpdate, puts on queue.
    - Same `PriceStream` Protocol interface.
    - Uses dedicated `RateLimiter` (separate from scan loop).
  - `async def create_price_stream(config: FlippeningConfig, poly_client: PolymarketClient) -> PriceStream`: Try WebSocket first. If connection fails within 10s, log warning and fall back to PollingPriceStream. Return the working stream.

- [x] T016 Create `tests/unit/test_ws_client.py` (~8 tests):
  - Test WebSocketPriceStream subscribe sends subscription message (mock websockets)
  - Test WebSocketPriceStream parses incoming JSON into PriceUpdate
  - Test WebSocketPriceStream reconnects on disconnect (mock disconnect + reconnect)
  - Test WebSocketPriceStream exponential backoff respects max_seconds
  - Test PollingPriceStream polls at configured interval (mock fetch_orderbook)
  - Test PollingPriceStream yields PriceUpdates from polled data
  - Test create_price_stream falls back to polling on WS failure
  - Test close() cancels background tasks cleanly

**Quality gate**: All 5 gates. Existing tests must pass.

---

## Phase 4: Spike Detection + Confidence Scoring

- [x] T017 Create `src/arb_scanner/flippening/spike_detector.py`:
  - `SpikeDetector` class:
    - Constructor takes `FlippeningConfig`.
    - `check_spike(self, update: PriceUpdate, baseline: Baseline, price_history: deque[PriceUpdate]) -> FlippeningEvent | None`: Main entry point.
      1. Calculate `deviation = abs(baseline.yes_price - update.yes_mid)` where `yes_mid = (update.yes_bid + update.yes_ask) / 2`.
      2. Get effective threshold via `_get_threshold(baseline.sport)` (checks sport_overrides, falls back to global).
      3. If `deviation < threshold`: return None.
      4. Check recency: call `_was_near_baseline_recently(baseline, price_history)`. If not recent: return None (gradual drift, not spike).
      5. Check direction: call `_is_against_favorite(baseline, update)`. If not against favorite: return None.
      6. Score confidence: call `_score_confidence(deviation, baseline, update, price_history)`.
      7. Get effective min_confidence via `_get_min_confidence(baseline.sport)`.
      8. If `confidence < min_confidence`: return None.
      9. Determine `SpikeDirection` from whether favorite dropped or underdog rose.
      10. Return `FlippeningEvent(...)` with uuid, all fields populated.

- [x] T018 Add recency check to SpikeDetector: `_was_near_baseline_recently(self, baseline: Baseline, history: deque[PriceUpdate]) -> bool`. Scan price_history for any update within the last `spike_window_minutes` where `abs(price - baseline.yes_price) < 0.05`. Return True if found. This distinguishes sudden spikes from gradual drift.

- [x] T019 Add direction check to SpikeDetector: `_is_against_favorite(self, baseline: Baseline, update: PriceUpdate) -> bool`. If `baseline.yes_price >= 0.50` (YES is favorite), return True only if YES price dropped. If `baseline.yes_price < 0.50` (NO is favorite), return True only if NO price dropped (i.e., YES price rose). Spike must be AGAINST the pre-game favorite.

- [x] T020 Add confidence scoring to SpikeDetector: `_score_confidence(self, deviation: Decimal, baseline: Baseline, update: PriceUpdate, history: deque[PriceUpdate]) -> float`:
  - `magnitude_score = min(float(deviation) / 0.30, 1.0)` — caps at 30pt deviation.
  - `strength_score = max(float(max(baseline.yes_price, baseline.no_price)) - 0.50, 0.0) / 0.50` — stronger favorite = higher score.
  - `speed_score`: compute minutes since price was last near baseline. `min(1.0 / max(minutes_elapsed, 0.5), 1.0)` — instant moves score highest.
  - `sport_mod = config.sport_overrides.get(baseline.sport, SportOverride()).confidence_modifier`.
  - Weighted average: `raw = (weights.magnitude * magnitude + weights.strength * strength + weights.speed * speed) * sport_mod`.
  - Apply late_join_penalty if `baseline.late_join`: `raw *= config.late_join_penalty`.
  - Clamp to [0.0, 1.0].

- [x] T021 Add helper methods to SpikeDetector:
  - `_get_threshold(self, sport: str) -> float`: Check `sport_overrides[sport].spike_threshold_pct`, fall back to `config.spike_threshold_pct`.
  - `_get_min_confidence(self, sport: str) -> float`: Check `sport_overrides[sport].min_confidence`, fall back to `config.min_confidence`.
  - `_yes_mid(self, update: PriceUpdate) -> Decimal`: Return `(update.yes_bid + update.yes_ask) / 2`.

- [x] T022 Create `tests/unit/test_spike_detector.py` (~15 tests):
  - Test no spike when deviation below threshold
  - Test spike detected when deviation exceeds threshold
  - Test no spike when price drifted gradually (not near baseline recently)
  - Test spike detected when recent prices were near baseline
  - Test no spike when move is WITH the favorite (not against)
  - Test spike detected when move is AGAINST the favorite (YES favorite drops)
  - Test spike detected for NO favorite (YES price rises against NO favorite)
  - Test confidence score increases with larger magnitude
  - Test confidence score increases with stronger baseline favorite
  - Test confidence score increases with faster spike speed
  - Test sport_overrides threshold applied correctly
  - Test sport_overrides confidence_modifier scales score
  - Test late_join penalty reduces confidence
  - Test confidence clamped to [0.0, 1.0]
  - Test spike below min_confidence returns None

**Quality gate**: All 5 gates. Existing tests must pass.

---

## Phase 5: Signal Generation + Reversion Monitoring

- [x] T023 Create `src/arb_scanner/flippening/signal_generator.py`:
  - `SignalGenerator` class:
    - Constructor takes `FlippeningConfig`.
    - `create_entry(self, event: FlippeningEvent, current_ask: Decimal, baseline: Baseline) -> EntrySignal`:
      1. `side`: "yes" if `event.spike_direction == FAVORITE_DROP` and `baseline.yes_price >= 0.50`, else "no".
      2. `entry_price`: `current_ask` (the ask for the side we're buying).
      3. `target_exit_price`: `entry_price + (baseline_price - entry_price) * config.reversion_target_pct` where `baseline_price` is the baseline price for the chosen side.
      4. `stop_loss_price`: `entry_price * (1 - config.stop_loss_pct)`.
      5. `suggested_size_usd`: `min(config.base_position_usd * float(event.confidence), config.max_position_usd)`. Round to 2 decimal places.
      6. `expected_profit_pct`: `(target_exit - entry) / entry`.
      7. `max_hold_minutes`: from config.
      8. Return `EntrySignal(...)` with uuid.

- [x] T024 Add reversion monitoring to `signal_generator.py`:
  - `check_exit(self, update: PriceUpdate, entry: EntrySignal) -> ExitSignal | None`:
    1. Get current bid for the entry's side (`update.yes_bid` if side="yes", `update.no_bid` if side="no").
    2. **Target hit**: If `current_bid >= entry.target_exit_price` → return ExitSignal with `exit_reason=REVERSION`, `exit_price=current_bid`.
    3. **Stop-loss hit**: If `current_bid <= entry.stop_loss_price` → return ExitSignal with `exit_reason=STOP_LOSS`, `exit_price=current_bid`.
    4. **Timeout**: If `(update.timestamp - entry.created_at).total_seconds() / 60 >= entry.max_hold_minutes` → return ExitSignal with `exit_reason=TIMEOUT`, `exit_price=current_bid`.
    5. Otherwise return None.
    6. For all exits: `realized_pnl = exit_price - entry.entry_price`, `realized_pnl_pct = realized_pnl / entry.entry_price`, `hold_minutes = (update.timestamp - entry.created_at).total_seconds() / 60`.

- [x] T025 Add execution ticket generation to `signal_generator.py`:
  - `create_ticket(self, entry: EntrySignal, event: FlippeningEvent) -> ExecutionTicket`:
    - `leg_1`: `{"venue": "polymarket", "action": "buy", "side": entry.side, "price": str(entry.entry_price), "size_usd": str(entry.suggested_size_usd)}`.
    - `leg_2`: `{"venue": "polymarket", "action": "sell", "side": entry.side, "price": str(entry.target_exit_price), "size_usd": str(entry.suggested_size_usd), "note": "limit sell — place manually when entry filled"}`.
    - `expected_cost`: `entry.entry_price * entry.suggested_size_usd`.
    - `expected_profit`: `(entry.target_exit_price - entry.entry_price) * entry.suggested_size_usd`.
    - `ticket_type`: `"flippening"`.
    - `status`: `"pending"`.

- [x] T026 Create `tests/unit/test_signal_generator.py` (~12 tests):
  - Test create_entry side is "yes" when favorite drops
  - Test create_entry side is "no" when underdog rises
  - Test target_exit_price calculation: entry + (baseline - entry) * reversion_pct
  - Test stop_loss_price calculation: entry * (1 - stop_loss_pct)
  - Test suggested_size scales with confidence
  - Test suggested_size capped at max_position_usd
  - Test expected_profit_pct calculation
  - Test check_exit returns REVERSION when bid >= target
  - Test check_exit returns STOP_LOSS when bid <= stop_loss
  - Test check_exit returns TIMEOUT when max_hold exceeded
  - Test check_exit returns None when no exit condition met
  - Test create_ticket populates both legs with correct data
  - Test create_ticket sets ticket_type="flippening"
  - Test realized_pnl and hold_minutes calculated correctly on exit

**Quality gate**: All 5 gates. Existing tests must pass.

---

## Phase 6: Alerting + Orchestrator

- [x] T027 Create `src/arb_scanner/flippening/alert_formatter.py`:
  - `build_entry_slack_payload(event: FlippeningEvent, entry: EntrySignal) -> dict[str, Any]`: Slack Block Kit payload. Header: "Flippening Detected" with :rotating_light: emoji. Fields: Market (title), Sport, Baseline Odds (formatted), Current Odds, Spike (pts with direction), Confidence (%), Entry Price, Target Exit, Size ($), Expected Profit (%).
  - `build_entry_discord_payload(event: FlippeningEvent, entry: EntrySignal) -> dict[str, Any]`: Discord embed. Color: Orange (15105570). Same fields as Slack.
  - `build_exit_slack_payload(event: FlippeningEvent, entry: EntrySignal, exit_sig: ExitSignal) -> dict[str, Any]`: Header varies by reason: :moneybag: "Flippening Reverted — Profit" for REVERSION, :x: "Stop-Loss Hit" for STOP_LOSS, :hourglass: "Timed Out" for TIMEOUT. Fields: Market, Reason, Entry Price, Exit Price, P&L ($), P&L (%), Hold Time.
  - `build_exit_discord_payload(event: FlippeningEvent, entry: EntrySignal, exit_sig: ExitSignal) -> dict[str, Any]`: Discord embed. Colors: Green (3066993) for reversion, Red (15158332) for stop_loss, Gray (9807270) for timeout.
  - `async def dispatch_flip_alert(payload_slack: dict | None, payload_discord: dict | None, *, slack_url: str, discord_url: str, client: httpx.AsyncClient | None) -> None`: Fire-and-forget dispatch, same pattern as existing `dispatch_webhook()`. Uses `_post_webhook` from `notifications/webhook.py` or duplicates the safe-send pattern.

- [x] T028 Create `src/arb_scanner/flippening/orchestrator.py`:
  - `async def run_flip_watch(config: Settings, *, dry_run: bool = False, sport_filter: list[str] | None = None) -> None`: Main entry point for flip-watch command.
    1. Load FlippeningConfig from settings. If not enabled and not dry_run, log warning and return.
    2. Create PolymarketClient from config.
    3. Discovery: Call `PolymarketClient.fetch_markets()` → `classify_sports_markets()` → `list[SportsMarket]`. Apply sport_filter if provided.
    4. If no sports markets found: log warning (EC-005), enter periodic retry (every 5 min).
    5. Create SpikeDetector(config.flippening), SignalGenerator(config.flippening).
    6. Create GameManager(config.flippening, spike_detector, signal_generator). Initialize with sports_markets.
    7. Create PriceStream via `create_price_stream()`. Subscribe to all token_ids.
    8. If not dry_run: create FlippeningRepository from DB pool.
    9. Main loop: `async for update in price_stream`:
      - `event, exit_sig = game_manager.process(update)`
      - If `event` and no open signal for that game:
        - `entry = signal_generator.create_entry(event, update ask price, baseline)`
        - `ticket = signal_generator.create_ticket(entry, event)`
        - Set `game_state.active_signal = entry`
        - If not dry_run: persist event, entry signal, baseline, ticket. Dispatch entry alert.
        - Log entry signal.
      - If `exit_sig`:
        - Clear `game_state.active_signal`
        - If not dry_run: persist exit signal. Dispatch exit alert.
        - Log exit signal with P&L.
    10. Periodic (every 5 min): re-discover sports markets, add new games to GameManager, remove completed games.
    11. Handle KeyboardInterrupt: close PriceStream, log shutdown.
  - `_periodic_discovery(...)`: Refresh sports market list, add new games, prune completed.

- [x] T029 Create `tests/unit/test_alert_formatter.py` (~6 tests):
  - Test entry Slack payload has correct header and emoji
  - Test entry Discord payload has Orange color (15105570)
  - Test exit Slack payload uses :moneybag: for reversion
  - Test exit Slack payload uses :x: for stop_loss
  - Test exit Discord payload uses correct color per reason
  - Test dispatch_flip_alert calls _post_webhook for configured URLs

- [x] T030 Create `tests/unit/test_flip_orchestrator.py` (~8 tests):
  - Test run_flip_watch exits early when flippening not enabled
  - Test discovery finds sports markets and initializes GameManager (mock PolymarketClient)
  - Test main loop processes price updates through GameManager (mock PriceStream)
  - Test entry signal triggers alert dispatch and persistence (mock repo + webhook)
  - Test exit signal triggers alert dispatch and persistence
  - Test dry_run skips persistence and alerts
  - Test no sports markets logs warning and retries (EC-005)
  - Test periodic discovery refreshes market list

**Quality gate**: All 5 gates. Existing tests must pass.

---

## Phase 7: CLI + API + Dashboard

- [x] T031 Create `src/arb_scanner/cli/flippening_commands.py` with `register(app: typer.Typer)` function:
  - `flip_watch` command: Options `--sports` (str, comma-separated sport filter), `--min-confidence` (float, override), `--dry-run` (bool). Loads config, overrides min_confidence if provided, calls `run_flip_watch()`.
  - `flip_history` command: Options `--last` (int, default 20), `--sport` (str | None), `--outcome` (str | None, filter by exit_reason), `--since` (str | None, ISO8601), `--format` (str, "table" or "json", default "table"). Reads from FlippeningRepository. Renders table or JSON.
  - `flip_stats` command: Options `--sport` (str | None), `--since` (str | None, ISO8601). Reads aggregated stats from FlippeningRepository. Renders summary: total signals, win rate, avg profit, avg hold time, breakdown by sport.

- [x] T032 Modify `src/arb_scanner/cli/app.py`: Import `flippening_commands` and call `flippening_commands.register(app)` following the same pattern as analytics_commands and alert_commands.

- [x] T033 Create `src/arb_scanner/api/routes_flippening.py`:
  - `router = APIRouter()`
  - `GET /api/flippenings/active`: Query param `limit` (int, default 50). Returns `await repo.get_active_signals(limit)`.
  - `GET /api/flippenings/history`: Query params `limit` (int, default 50), `sport` (str | None). Returns `await repo.get_history(limit, sport)`.
  - `GET /api/flippenings/stats`: Query params `sport` (str | None), `since` (str | None, parsed as datetime). Returns `await repo.get_stats(sport, since)`.
  - Use `Depends(get_flip_repo)` for FlippeningRepository dependency injection. `get_flip_repo` constructs from the app's DB pool (same pattern as existing routes).

- [x] T034 Modify `src/arb_scanner/api/app.py`: Import `routes_flippening.router` and call `app.include_router(router)`. Add `get_flip_repo` dependency that creates `FlippeningRepository(pool)`.

- [x] T035 Modify `src/arb_scanner/api/static/index.html`: Add "Flippenings" tab to the tab bar (after Tickets tab). Add corresponding content div with:
  - Active flippenings table placeholder
  - History table placeholder
  - Stats summary cards placeholder

- [x] T036 Modify `src/arb_scanner/api/static/app.js`: Add flippening tab logic:
  - `loadFlippeningsActive()`: Fetch `/api/flippenings/active`, render table with columns: Market, Sport, Side, Entry Price, Target Exit, Stop Loss, Size, Confidence, Time Open.
  - `loadFlippeningsHistory()`: Fetch `/api/flippenings/history?limit=20`, render table with columns: Market, Sport, Outcome, Entry, Exit, P&L ($), P&L (%), Hold Time.
  - `loadFlippeningsStats()`: Fetch `/api/flippenings/stats`, render summary cards: Total Signals, Win Rate, Avg Profit, Avg Hold Time, and per-sport breakdown.
  - Wire tab switching to call load functions. Include in auto-refresh cycle.

- [x] T037 Create `tests/unit/test_flippening_commands.py` (~6 tests):
  - Test flip_watch invokes run_flip_watch with correct config
  - Test flip_watch --dry-run passes dry_run=True
  - Test flip_watch --sports filters sports list
  - Test flip_history renders table output (mock repo)
  - Test flip_history --format json renders JSON
  - Test flip_stats renders summary (mock repo)

**Quality gate**: All 5 gates. Existing tests must pass.

---

## Phase 8: Edge Cases + Integration Tests + Polish

- [x] T038 Implement EC-001 (Late Join): In `GameManager.initialize()`, when a market's game_start_time is in the past, capture baseline with `late_join=True`. In `SpikeDetector._score_confidence()`, apply `config.late_join_penalty` multiplier when `baseline.late_join is True`. Already designed into T011/T020 — verify tests cover this path.

- [x] T039 Implement EC-002 (Multiple Flippenings): In `GameManager.process()`, skip spike detection when `state.active_signal is not None`. On ExitSignal, clear `state.active_signal = None` to allow the next flippening in the same game. Already designed into T011 — verify tests cover sequential flippenings in same game.

- [x] T040 Implement EC-003 (Game Resolves During Monitoring): In `GameManager.process()`, when market resolves (detected from price update hitting exactly 1.0 or 0.0, or from lifecycle transition to COMPLETED), if `state.active_signal` exists, emit ExitSignal with `exit_reason=RESOLUTION` and `exit_price = Decimal("1.00")` or `Decimal("0.00")` depending on resolution side.

- [x] T041 Implement EC-004 (WebSocket Disconnection): In `WebSocketPriceStream`, on sustained disconnect exceeding `max_hold_minutes` for any game with active signal, emit synthetic PriceUpdate with last known prices. In `GameManager`, add `close_all_open_signals(reason: ExitReason, last_prices: dict[str, PriceUpdate]) -> list[ExitSignal]` method. Called by orchestrator when stream disconnect timeout exceeded.

- [x] T042 Implement EC-005 (No Sports Markets): Already handled in T028 — verify orchestrator logs warning and retries discovery every 5 minutes when no markets found.

- [x] T043 Implement EC-006 (Baseline Drift): In `GameManager._update_drift()`, track price changes in `drift_accumulator` (list of (timestamp, price) tuples). If `abs(price_delta_per_minute) < Decimal("0.02")` sustained for > 5 minutes of elapsed data, update `state.baseline` to current prices. Clear drift_accumulator on baseline update. If a sharp spike is detected, do NOT update baseline. Already designed into T011 — verify tests cover gradual drift vs sharp spike.

- [x] T044 Create `tests/integration/test_flippening_pipeline.py` (~10 tests):
  - Test full pipeline: mock WS emits baseline → spike → reversion sequence. Verify: baseline captured, FlippeningEvent detected, EntrySignal generated, ExitSignal(REVERSION) emitted, correct P&L.
  - Test full pipeline with stop_loss: mock WS emits baseline → spike → further drop. Verify ExitSignal(STOP_LOSS).
  - Test full pipeline with timeout: mock WS emits baseline → spike → no reversion within max_hold. Verify ExitSignal(TIMEOUT).
  - Test late_join pipeline: connect to already-live game, verify late_join baseline + confidence penalty.
  - Test multiple games: two concurrent games, one spikes, verify only spiked game generates signals.
  - Test game resolution during active signal: market resolves to 1.0, verify ExitSignal(RESOLUTION).
  - Test no spike on gradual drift: slow price movement doesn't trigger flippening.
  - Test EC-002: second spike in same game blocked while first signal active.
  - Test EC-002: second spike allowed after first signal exits.
  - Test persistence: verify FlippeningRepository receives correct insert calls (mock asyncpg).

- [x] T045 Run full quality gate suite. Fix any ruff, mypy, or test failures. Verify coverage >= 70%.

- [x] T046 Update `CLAUDE.md`:
  - Add flippening engine section: note `flippening/` subpackage, FlippeningConfig, new CLI commands (`flip-watch`, `flip-history`, `flip-stats`), new API endpoints, WebSocket dependency.
  - Add to Recent Changes: `008-flippening-engine` summary.
  - Add `flip-watch`, `flip-history`, `flip-stats` to Commands section.
  - Note `websockets` in Active Technologies.

- [x] T047 Update `src/arb_scanner/flippening/__init__.py` with final public re-exports: `SpikeDetector`, `SignalGenerator`, `GameManager`, `run_flip_watch`, `FlippeningRepository`, all model types.

**Quality gate**: All 5 gates green. Final verification. All existing tests pass. Coverage >= 70%.

---

## Total: 47 tasks across 8 phases
