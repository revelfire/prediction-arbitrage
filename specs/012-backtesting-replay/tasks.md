# Tasks: 012 — Backtesting & Historical Replay

**Feature**: `012-backtesting-replay` | **Date**: 2026-02-26
**Spec**: `specs/012-backtesting-replay/spec.md`
**Plan**: `specs/012-backtesting-replay/plan.md`

---

## Phase 1: Tick Capture Config (FR-003, FR-015)

- [ ] 1.1 Add `capture_ticks: bool = True` to `FlippeningConfig` in `models/config.py`
- [ ] 1.2 Add `tick_retention_days: int = 90` to `FlippeningConfig`
- [ ] 1.3 Add `tick_buffer_size: int = 100` to `FlippeningConfig`
- [ ] 1.4 Add `tick_flush_interval_seconds: float = 5.0` to `FlippeningConfig`
- [ ] 1.5 Add commented `capture_ticks`, `tick_retention_days`, `tick_buffer_size`, `tick_flush_interval_seconds` fields to `config.example.yaml` under `flippening:`
- [ ] 1.6 Verify existing tests pass with new config defaults (`pytest tests/ -x --tb=short`)

## Phase 2: Database Schema — Migration 016 (FR-001, FR-004)

- [ ] 2.1 Create `src/arb_scanner/storage/migrations/016_create_tick_tables.sql` with `flippening_price_ticks` table: `id BIGSERIAL PRIMARY KEY`, `market_id TEXT NOT NULL`, `token_id TEXT NOT NULL`, `yes_bid NUMERIC(10,6)`, `yes_ask NUMERIC(10,6)`, `no_bid NUMERIC(10,6)`, `no_ask NUMERIC(10,6)`, `timestamp TIMESTAMPTZ NOT NULL`, `synthetic_spread BOOLEAN DEFAULT FALSE`, `book_depth_bids INT DEFAULT 0`, `book_depth_asks INT DEFAULT 0`
- [ ] 2.2 Add composite index `idx_ticks_market_ts ON flippening_price_ticks (market_id, timestamp)` in same migration
- [ ] 2.3 Add timestamp index `idx_ticks_ts ON flippening_price_ticks (timestamp)` for retention pruning
- [ ] 2.4 Add `flippening_baseline_drifts` table in same migration: `id BIGSERIAL PRIMARY KEY`, `market_id TEXT NOT NULL`, `old_yes NUMERIC(10,6) NOT NULL`, `new_yes NUMERIC(10,6) NOT NULL`, `drift_reason TEXT NOT NULL DEFAULT 'gradual'`, `drifted_at TIMESTAMPTZ NOT NULL`
- [ ] 2.5 Add index `idx_drifts_market_ts ON flippening_baseline_drifts (market_id, drifted_at)`

## Phase 3: Tick Repository + SQL Queries (FR-001, FR-004, FR-015)

- [ ] 3.1 Create `src/arb_scanner/storage/_tick_queries.py` with `INSERT_TICKS_BATCH` SQL using `executemany`-compatible parameterized insert for `flippening_price_ticks`
- [ ] 3.2 Add `INSERT_DRIFT` SQL constant — single row insert for `flippening_baseline_drifts`
- [ ] 3.3 Add `SELECT_TICKS_BY_MARKET` SQL — `WHERE market_id = $1 AND timestamp >= $2 AND timestamp <= $3 ORDER BY timestamp`
- [ ] 3.4 Add `SELECT_DRIFTS_BY_MARKET` SQL — drifts for a market in a time range, ordered by `drifted_at`
- [ ] 3.5 Add `SELECT_DISTINCT_MARKETS` SQL — distinct `market_id` from `flippening_price_ticks` joined with `flippening_baselines` on sport filter + time range
- [ ] 3.6 Add `DELETE_OLD_TICKS` SQL — `DELETE FROM flippening_price_ticks WHERE timestamp < $1`
- [ ] 3.7 Create `src/arb_scanner/storage/tick_repository.py` with `TickRepository.__init__(self, pool: asyncpg.pool.Pool)`
- [ ] 3.8 Implement `TickRepository.insert_ticks_batch(ticks: list[tuple])` — uses `executemany` with `INSERT_TICKS_BATCH`
- [ ] 3.9 Implement `TickRepository.insert_drift(market_id, old_yes, new_yes, drifted_at, drift_reason='gradual')`
- [ ] 3.10 Implement `TickRepository.stream_ticks(market_id, since, until) -> AsyncIterator[asyncpg.Record]` — asyncpg cursor-based streaming within a transaction (EC-007)
- [ ] 3.11 Implement `TickRepository.get_drifts(market_id, since, until) -> list[asyncpg.Record]`
- [ ] 3.12 Implement `TickRepository.get_market_ids(sport, since, until) -> list[str]` — for batch replay across a sport
- [ ] 3.13 Implement `TickRepository.prune_ticks(before: datetime) -> int` — returns count of deleted rows
- [ ] 3.14 Add `TickRepository.get_baseline(market_id) -> asyncpg.Record | None` — fetch most recent baseline for a market from `flippening_baselines`
- [ ] 3.15 Run quality gates: `ruff check`, `ruff format`, `mypy src/ --strict`

## Phase 4: TickBuffer — Non-Blocking Batch Writer (FR-001, FR-002, FR-003)

- [ ] 4.1 Create `src/arb_scanner/flippening/tick_buffer.py` with `TickBuffer` class
- [ ] 4.2 Implement `TickBuffer.__init__(repo: TickRepository | None, config: FlippeningConfig)` — set `_enabled = config.capture_ticks and repo is not None`, `_max_size = config.tick_buffer_size`, `_buffer: list[tuple] = []`
- [ ] 4.3 Implement `TickBuffer._to_row(update: PriceUpdate) -> tuple` — extract `(market_id, token_id, yes_bid, yes_ask, no_bid, no_ask, timestamp, synthetic_spread, book_depth_bids, book_depth_asks)` from PriceUpdate
- [ ] 4.4 Implement `TickBuffer.append(update: PriceUpdate) -> None` — return early if `not _enabled`, append `_to_row(update)` to buffer
- [ ] 4.5 Implement `TickBuffer.flush() -> None` — async method: swap buffer (`batch, self._buffer = self._buffer[:], []`), call `repo.insert_ticks_batch(batch)`, wrap in try/except logging warning on failure (EC-005: drop buffer, never retry)
- [ ] 4.6 Add auto-flush trigger in `append()` when `len(self._buffer) >= self._max_size` — schedule `flush()` via `asyncio.ensure_future`
- [ ] 4.7 Add `TickBuffer.pending` property returning `len(self._buffer)` for diagnostics
- [ ] 4.8 Create `tests/unit/test_tick_buffer.py` — test append fills buffer
- [ ] 4.9 Test auto-flush when buffer reaches `_max_size`
- [ ] 4.10 Test manual `flush()` drains buffer and calls `repo.insert_ticks_batch()`
- [ ] 4.11 Test flush failure logs warning and drops buffer without raising (EC-005)
- [ ] 4.12 Test disabled buffer (`capture_ticks=False`) — `append()` is silent no-op
- [ ] 4.13 Test dry-run buffer (`repo=None`) — `append()` is silent no-op
- [ ] 4.14 Run quality gates

## Phase 5: Drift Capture in GameManager (FR-004)

- [ ] 5.1 Modify `GameManager._update_drift()` return type from `None` to `tuple[Decimal, Decimal, datetime] | None` — return `(old_yes, new_yes, drifted_at)` when drift fires, None otherwise
- [ ] 5.2 Capture old baseline `yes_price` before mutation in `_update_drift()`, return it with new value and timestamp when drift occurs
- [ ] 5.3 Modify `GameManager.process()` to capture `_update_drift()` return value and expose it — change return type to `tuple[FlippeningEvent | None, ExitSignal | None, tuple[...] | None]` (3-tuple)
- [ ] 5.4 Update all callers of `game_mgr.process()` in `orchestrator.py` to unpack the 3-tuple: `event, exit_sig, drift_info = game_mgr.process(update)`
- [ ] 5.5 Update existing tests for `GameManager.process()` to unpack 3-tuple
- [ ] 5.6 Run quality gates

## Phase 6: Orchestrator Integration — Tick Capture (FR-001, FR-002)

- [ ] 6.1 Import `TickBuffer` and `TickRepository` in `orchestrator.py`
- [ ] 6.2 In `run_flip_watch()`, after creating `FlippeningRepository`, also create `TickRepository(db.pool)` if `not dry_run`
- [ ] 6.3 Create `TickBuffer(tick_repo, config.flippening)` — passes `None` repo if dry_run
- [ ] 6.4 In the `async for update in stream:` loop, call `tick_buffer.append(enriched)` before `_process_update()`
- [ ] 6.5 Add tick flush to the timer check: `if now - last_tick_flush > config.flippening.tick_flush_interval_seconds: await tick_buffer.flush(); last_tick_flush = now`
- [ ] 6.6 In the `finally` block (stream cleanup), call `await tick_buffer.flush()` to drain remaining ticks
- [ ] 6.7 Wire drift persistence: after `_process_update()`, if `drift_info` is not None, call `await tick_repo.insert_drift(drift_info...)` wrapped in try/except (non-blocking)
- [ ] 6.8 Update `_process_update()` signature to accept and return drift info from `game_mgr.process()`
- [ ] 6.9 Run quality gates

## Phase 7: Replay Models (FR-008, FR-010)

- [ ] 7.1 Create `src/arb_scanner/models/replay.py` with `ReplaySignal` Pydantic model: `market_id: str`, `entry_price: Decimal`, `exit_price: Decimal`, `exit_reason: ExitReason`, `realized_pnl: Decimal`, `hold_minutes: Decimal`, `confidence: Decimal`, `side: str`, `entry_at: datetime`, `exit_at: datetime`
- [ ] 7.2 Add `ReplayEvaluation` Pydantic model: `total_signals: int`, `win_count: int`, `win_rate: float`, `avg_pnl: float`, `avg_hold_minutes: float`, `max_drawdown: float`, `profit_factor: float`, `config_overrides: dict[str, Any] = {}`
- [ ] 7.3 Add `SweepResult` Pydantic model: `param_name: str`, `results: list[tuple[float, ReplayEvaluation]]`
- [ ] 7.4 Add field validators: `side` must be "yes" or "no", prices in [0, 1] range
- [ ] 7.5 Run quality gates (`mypy` will verify model correctness)

## Phase 8: ReplayEngine (FR-005, FR-006, FR-007, FR-009)

- [ ] 8.1 Create `src/arb_scanner/flippening/replay_engine.py` with `ReplayEngine.__init__(tick_repo, flip_repo, base_config)`
- [ ] 8.2 Implement `_apply_overrides(overrides: dict | None) -> FlippeningConfig` — uses `base_config.model_copy(update=overrides)`, catches `ValidationError` for EC-004
- [ ] 8.3 Implement `_load_baseline(market_id) -> Baseline | None` — fetches from `flippening_baselines` via `tick_repo.get_baseline()`, returns None if missing (EC-002)
- [ ] 8.4 Implement `_load_drifts(market_id, since, until, baseline_captured_at) -> list` — fetches drifts, filters out any with `drifted_at < baseline_captured_at` (EC-003), sorts by timestamp
- [ ] 8.5 Implement `_record_to_price_update(record: asyncpg.Record) -> PriceUpdate` — converts DB row to PriceUpdate model
- [ ] 8.6 Implement `replay_market(market_id, since, until, overrides) -> list[ReplaySignal]` — the core replay loop: apply overrides, load baseline (skip if None), load drifts, create SpikeDetector + SignalGenerator, stream ticks, process each tick through spike/signal pipeline, collect ReplaySignals
- [ ] 8.7 In replay loop: maintain `price_history: deque[PriceUpdate]` (maxlen 200), `active_signal: EntrySignal | None`, `drift_index: int` for walking through sorted drift records
- [ ] 8.8 In replay loop: apply drift when `drift_records[drift_index].drifted_at <= tick.timestamp` — update baseline yes/no prices (FR-007)
- [ ] 8.9 In replay loop: when spike detected and no active signal, call `signal_gen.create_entry()` to get EntrySignal, record as active
- [ ] 8.10 In replay loop: when active signal exists, call `signal_gen.check_exit()`, on exit create `ReplaySignal` from entry + exit data
- [ ] 8.11 Implement `replay_sport(sport, since, until, overrides) -> list[ReplaySignal]` — get market_ids via `tick_repo.get_market_ids()`, call `replay_market()` for each, concatenate results
- [ ] 8.12 Handle EC-001 (zero ticks): `replay_market()` returns empty list when tick stream yields nothing
- [ ] 8.13 Handle EC-002 (missing baseline): log warning, return empty list
- [ ] 8.14 Create `tests/unit/test_replay_engine.py` — test replay with known tick sequence produces expected entry/exit signals
- [ ] 8.15 Test config override changes spike threshold, producing different signals than default
- [ ] 8.16 Test drift record application updates baseline mid-replay, altering spike detection
- [ ] 8.17 Test missing baseline returns empty result with warning log (EC-002)
- [ ] 8.18 Test empty tick range returns empty result (EC-001)
- [ ] 8.19 Test invalid config override (negative threshold) raises ValidationError (EC-004)
- [ ] 8.20 Test drift records before baseline are skipped (EC-003)
- [ ] 8.21 Run quality gates

## Phase 9: Evaluation + Parameter Sweep (FR-010, FR-011)

- [ ] 9.1 Create `src/arb_scanner/flippening/replay_evaluator.py` with `evaluate_replay(signals: list[ReplaySignal]) -> ReplayEvaluation`
- [ ] 9.2 Implement win count: signals where `exit_reason == ExitReason.REVERSION`
- [ ] 9.3 Implement win rate: `win_count / total_signals` (0.0 if total is 0)
- [ ] 9.4 Implement avg P&L: mean of `realized_pnl` across all signals
- [ ] 9.5 Implement avg hold minutes: mean of `hold_minutes`
- [ ] 9.6 Implement max drawdown: track cumulative P&L series, find largest peak-to-trough decline
- [ ] 9.7 Implement profit factor: `sum(positive pnl) / abs(sum(negative pnl))`, return 0.0 if no losses (or float('inf') capped to 999.99)
- [ ] 9.8 Handle empty signals: return `ReplayEvaluation` with all zeros
- [ ] 9.9 Implement `sweep_parameter(engine, sport, since, until, param_name, min_val, max_val, step) -> SweepResult`
- [ ] 9.10 Generate parameter values from `min_val` to `max_val` inclusive using `step` increments (handle float precision with `Decimal`)
- [ ] 9.11 For each value: call `engine.replay_sport(sport, since, until, {param_name: value})`, then `evaluate_replay()`, collect into `SweepResult`
- [ ] 9.12 Handle EC-006 (sweep with no ticks): each evaluation has 0 signals, grid still returned with empty evaluations
- [ ] 9.13 Create `tests/unit/test_replay_evaluator.py` — test all-win scenario (100% win rate)
- [ ] 9.14 Test all-loss scenario (0% win rate, profit_factor = 0)
- [ ] 9.15 Test mixed scenario with known P&Ls — verify win_rate, avg_pnl, profit_factor
- [ ] 9.16 Test empty signals list produces all-zero evaluation
- [ ] 9.17 Test max drawdown with known cumulative P&L sequence (e.g. [+5, -3, +2, -8, +1] → drawdown = 8)
- [ ] 9.18 Test sweep with multiple parameter values — result per value with correct override
- [ ] 9.19 Test sweep with zero ticks produces empty evaluations (EC-006)
- [ ] 9.20 Run quality gates

## Phase 10: CLI Commands (FR-012, FR-013, FR-014, FR-015)

- [ ] 10.1 Create `src/arb_scanner/cli/_replay_helpers.py` with shared async helper: `create_replay_engine(config) -> tuple[ReplayEngine, Database]` — opens DB, creates repos, returns engine + db handle
- [ ] 10.2 Implement `run_replay(config, market_id, sport, since, until, overrides) -> list[dict]` — async helper that creates engine, runs replay, serializes results
- [ ] 10.3 Implement `run_evaluate(config, sport, since, until) -> dict` — async helper that replays + evaluates
- [ ] 10.4 Implement `run_sweep(config, sport, since, until, param, min_val, max_val, step) -> dict` — async helper that runs parameter sweep
- [ ] 10.5 Implement `run_prune(config, days, dry_run) -> dict` — async helper for tick pruning
- [ ] 10.6 Implement `render_replay_table(signals: list[dict])` — formatted text table: Market, Side, Entry, Exit, P&L, Hold, Confidence
- [ ] 10.7 Implement `render_evaluate_table(evaluation: dict)` — summary display with win rate, avg P&L, profit factor, drawdown
- [ ] 10.8 Implement `render_sweep_table(sweep: dict)` — grid display: Param Value | Signals | Win% | Avg P&L | Profit Factor
- [ ] 10.9 Implement `parse_overrides(override_strs: list[str]) -> dict` — parse `key=value` strings into dict, coerce numeric values
- [ ] 10.10 Register `flip-replay` command in `flippening_commands.py`: options `--market-id`, `--sport`, `--since`, `--until`, `--override` (list), `--format table|json`. Requires either `--market-id` or `--sport`. Delegates to `_replay_helpers.run_replay()`.
- [ ] 10.11 Register `flip-evaluate` command: options `--sport` (required), `--since`, `--until`, `--format table|json`. Delegates to `_replay_helpers.run_evaluate()`.
- [ ] 10.12 Register `flip-sweep` command: options `--param` (required), `--min`, `--max`, `--step`, `--sport` (required), `--since`, `--until`, `--format table|json`. Delegates to `_replay_helpers.run_sweep()`.
- [ ] 10.13 Register `flip-tick-prune` command: options `--days` (default from config), `--dry-run`. Delegates to `_replay_helpers.run_prune()`.
- [ ] 10.14 Add all 4 commands to `register()` function in `flippening_commands.py`
- [ ] 10.15 Create `tests/unit/test_replay_cli.py` — test `flip-replay --market-id X --since ... --until ...` with mocked engine returns signal table
- [ ] 10.16 Test `flip-replay --format json` returns valid JSON output
- [ ] 10.17 Test `flip-evaluate --sport nba` with mocked engine returns evaluation summary
- [ ] 10.18 Test `flip-sweep --param spike_threshold_pct --min 0.08 --max 0.20 --step 0.04 --sport nba` with mocked engine returns grid
- [ ] 10.19 Test `flip-tick-prune --days 30` with mocked repo returns deletion count
- [ ] 10.20 Test `flip-tick-prune --dry-run` shows count without deletion
- [ ] 10.21 Test `flip-replay` with neither `--market-id` nor `--sport` raises error
- [ ] 10.22 Run quality gates

## Phase 11: Integration + Final Verification

- [ ] 11.1 Run full test suite: `pytest tests/ -x --tb=short` — all tests pass
- [ ] 11.2 Run coverage check: `pytest tests/ --cov=src/arb_scanner --cov-fail-under=70`
- [ ] 11.3 Run `mypy src/ --strict` — zero errors
- [ ] 11.4 Run `ruff check src/ tests/` + `ruff format --check src/ tests/` — clean
- [ ] 11.5 Verify new files are under 300-line limit: `tick_buffer.py`, `replay_engine.py`, `replay_evaluator.py`, `models/replay.py`, `_tick_queries.py`, `tick_repository.py`, `_replay_helpers.py`
- [ ] 11.6 Verify `flippening_commands.py` stays under 400 lines; if over, extract replay commands to `replay_commands.py`
- [ ] 11.7 Update `CLAUDE.md` Recent Changes section with 012-backtesting-replay summary
- [ ] 11.8 Update `config.example.yaml` comments if any fields changed during implementation

---

**Total tasks**: 95
**Phases**: 11
