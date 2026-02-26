# Implementation Plan: Backtesting & Historical Replay

**Feature**: `012-backtesting-replay` | **Date**: 2026-02-26 | **Status**: Draft
**Spec**: `specs/012-backtesting-replay/spec.md`

## Architecture Overview

The backtesting system adds a **capture layer** in the live orchestrator and a **replay engine** that runs offline against stored ticks. Tick capture is non-blocking (buffered batch inserts). The replay engine reuses existing `SpikeDetector` and `SignalGenerator` directly — no mock or wrapper — just feeding stored ticks instead of live ones.

```
LIVE PATH (capture)                        OFFLINE PATH (replay)
─────────────────                          ──────────────────────
WebSocket / Polling                        CLI: flip-replay / flip-evaluate / flip-sweep
        │                                          │
        ▼                                          ▼
  PriceUpdate                              ReplayEngine
        │                                    ├─ load ticks from DB (cursor streaming)
        ├─► TickBuffer.append()              ├─ load baseline from flippening_baselines
        │     └─ flush to DB (batch)         ├─ load drifts from flippening_baseline_drifts
        │                                    ├─ reconstruct GameState
        ▼                                    ├─ feed ticks → SpikeDetector.check_spike()
  SpikeDetector                              ├─ feed ticks → SignalGenerator.check_exit()
  SignalGenerator                            └─ collect ReplaySignal[]
  GameManager                                        │
        │                                            ▼
        ├─► drift fires                      evaluate_replay()
        │     └─ DriftCapture → DB             ├─ win_rate, avg_pnl, profit_factor
        │                                      └─ max_drawdown, avg_hold
        ▼
  existing persist path                      sweep_parameter()
                                               └─ iterate config values → evaluate_replay()
```

## File Change Map

### New Files

| File | Purpose | FRs |
|------|---------|-----|
| `src/arb_scanner/flippening/tick_buffer.py` | `TickBuffer` class — non-blocking batch insert buffer for `PriceUpdate` | FR-001, FR-002, FR-003 |
| `src/arb_scanner/flippening/replay_engine.py` | `ReplayEngine` class — loads ticks, reconstructs state, replays through spike/signal | FR-005–FR-009 |
| `src/arb_scanner/flippening/replay_evaluator.py` | `evaluate_replay()` and `sweep_parameter()` functions | FR-010, FR-011 |
| `src/arb_scanner/models/replay.py` | `ReplaySignal` and `ReplayEvaluation` Pydantic models | FR-008, FR-010 |
| `src/arb_scanner/cli/_replay_helpers.py` | Async helpers and table renderers for replay CLI commands | FR-012–FR-014 |
| `src/arb_scanner/storage/migrations/016_create_tick_tables.sql` | `flippening_price_ticks` + `flippening_baseline_drifts` tables | FR-001, FR-004 |
| `src/arb_scanner/storage/_tick_queries.py` | SQL for tick insert, drift insert, tick select (cursor), drift select, tick prune | FR-001, FR-004, FR-005, FR-015 |
| `src/arb_scanner/storage/tick_repository.py` | `TickRepository` — batch insert ticks, insert drifts, stream ticks, prune | FR-001, FR-004, FR-005, FR-015 |
| `tests/unit/test_tick_buffer.py` | Buffer append, flush, overflow, error handling | FR-001–FR-003 |
| `tests/unit/test_replay_engine.py` | Replay with known ticks, config overrides, drift application | FR-005–FR-009 |
| `tests/unit/test_replay_evaluator.py` | Evaluation metrics, sweep parameter, edge cases | FR-010, FR-011 |
| `tests/unit/test_replay_cli.py` | CLI command integration tests | FR-012–FR-015 |

### Modified Files

| File | Changes | FRs |
|------|---------|-----|
| `src/arb_scanner/models/config.py` | Add `capture_ticks: bool = True`, `tick_retention_days: int = 90`, `tick_buffer_size: int = 100`, `tick_flush_interval_seconds: float = 5.0` to `FlippeningConfig` | FR-003, FR-015 |
| `src/arb_scanner/flippening/orchestrator.py` | Create `TickBuffer` on startup, call `buffer.append(update)` per tick, call `buffer.flush()` on interval | FR-001, FR-002 |
| `src/arb_scanner/flippening/game_manager.py` | Return drift info from `_update_drift()` so orchestrator can persist it | FR-004 |
| `src/arb_scanner/storage/_flippening_queries.py` | No changes — tick queries go in separate `_tick_queries.py` | — |
| `src/arb_scanner/cli/flippening_commands.py` | Register `flip-replay`, `flip-evaluate`, `flip-sweep`, `flip-tick-prune` commands | FR-012–FR-015 |
| `config.example.yaml` | Add `capture_ticks`, `tick_retention_days`, `tick_buffer_size`, `tick_flush_interval_seconds` | FR-003, FR-015 |

## Implementation Phases

### Phase 1: Tick Capture Models + Config (FR-003)

Add 4 new fields to `FlippeningConfig` in `models/config.py`:

```python
capture_ticks: bool = True
tick_retention_days: int = 90
tick_buffer_size: int = 100
tick_flush_interval_seconds: float = 5.0
```

All default-valued so existing configs remain valid. Update `config.example.yaml` with commented examples.

### Phase 2: Database Schema — Migration 016 (FR-001, FR-004)

Create `016_create_tick_tables.sql`:

```sql
CREATE TABLE flippening_price_ticks (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    yes_bid NUMERIC(10,6) NOT NULL,
    yes_ask NUMERIC(10,6) NOT NULL,
    no_bid NUMERIC(10,6) NOT NULL,
    no_ask NUMERIC(10,6) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    synthetic_spread BOOLEAN NOT NULL DEFAULT FALSE,
    book_depth_bids INT NOT NULL DEFAULT 0,
    book_depth_asks INT NOT NULL DEFAULT 0
);

CREATE INDEX idx_ticks_market_ts ON flippening_price_ticks (market_id, timestamp);
CREATE INDEX idx_ticks_ts ON flippening_price_ticks (timestamp);

CREATE TABLE flippening_baseline_drifts (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT NOT NULL,
    old_yes NUMERIC(10,6) NOT NULL,
    new_yes NUMERIC(10,6) NOT NULL,
    drift_reason TEXT NOT NULL DEFAULT 'gradual',
    drifted_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_drifts_market_ts ON flippening_baseline_drifts (market_id, drifted_at);
```

Design notes:
- `BIGSERIAL` primary key — ticks accumulate fast (millions/day at scale).
- Composite index `(market_id, timestamp)` is the primary query pattern for replay.
- `timestamp` index for retention pruning (`DELETE WHERE timestamp < $1`).
- `NUMERIC(10,6)` matches existing price precision across the codebase.

### Phase 3: Tick Repository + SQL (FR-001, FR-004, FR-015)

Create `storage/_tick_queries.py` with:
- `INSERT_TICKS_BATCH` — uses `UNNEST` for batch inserts (asyncpg `executemany` alternative for high throughput).
- `INSERT_DRIFT` — single row insert for baseline drift events.
- `SELECT_TICKS_BY_MARKET` — `WHERE market_id = $1 AND timestamp BETWEEN $2 AND $3 ORDER BY timestamp`. No `LIMIT` — cursor-based streaming handles large result sets.
- `SELECT_DRIFTS_BY_MARKET` — drifts for a market in a time range.
- `SELECT_DISTINCT_MARKETS` — distinct market_ids within a sport + time range (for evaluate/sweep).
- `DELETE_OLD_TICKS` — `DELETE FROM flippening_price_ticks WHERE timestamp < $1`.

Create `storage/tick_repository.py` with `TickRepository`:
- `insert_ticks_batch(ticks: list[tuple])` — batch insert using `executemany` or `COPY`.
- `insert_drift(market_id, old_yes, new_yes, drifted_at)`.
- `stream_ticks(market_id, since, until) -> AsyncIterator[Record]` — uses asyncpg cursor for EC-007 (streaming, not loading all into memory).
- `get_drifts(market_id, since, until) -> list[Record]`.
- `get_market_ids(sport, since, until) -> list[str]` — for batch replay across a sport.
- `prune_ticks(before: datetime) -> int` — returns count deleted.

Streaming approach for EC-007:
```python
async def stream_ticks(self, market_id, since, until):
    async with self._pool.acquire() as conn:
        async with conn.transaction():
            async for record in conn.cursor(Q.SELECT_TICKS, market_id, since, until):
                yield record
```

### Phase 4: TickBuffer — Non-Blocking Batch Writer (FR-001, FR-002, FR-003)

Create `flippening/tick_buffer.py` with `TickBuffer`:

```python
class TickBuffer:
    def __init__(self, repo: TickRepository | None, config: FlippeningConfig):
        self._repo = repo
        self._buffer: list[tuple] = []
        self._max_size = config.tick_buffer_size
        self._enabled = config.capture_ticks and repo is not None

    def append(self, update: PriceUpdate) -> None:
        """Add a tick to the buffer. Non-blocking."""
        if not self._enabled:
            return
        self._buffer.append(self._to_row(update))
        if len(self._buffer) >= self._max_size:
            asyncio.get_event_loop().call_soon(
                lambda: asyncio.ensure_future(self.flush())
            )

    async def flush(self) -> None:
        """Flush buffer to DB. Swallows exceptions (EC-005)."""
        if not self._buffer or not self._repo:
            return
        batch, self._buffer = self._buffer[:], []
        try:
            await self._repo.insert_ticks_batch(batch)
        except Exception:
            logger.warning("tick_flush_failed", dropped=len(batch))
```

Design notes:
- **Non-blocking (SC-004)**: `append()` is synchronous. Flush is async but fire-and-forget on buffer-full. The orchestrator also calls `flush()` on a timer.
- **EC-005**: On DB failure, drop buffer and log. Never retry, never block the live engine.
- **FR-003**: `_enabled` flag checks both `capture_ticks` config and repo availability (None in dry_run).
- Buffer swap (`self._buffer[:], []`) prevents data races if flush overlaps with append.

### Phase 5: Drift Capture in GameManager (FR-004)

Modify `GameManager._update_drift()` to return drift info when a drift occurs:

Currently `_update_drift()` returns `None` implicitly. Change it to return an optional tuple `(old_yes, new_yes, drifted_at)` when a drift fires. The `process()` method already calls `_update_drift()` — capture its return value.

In the orchestrator, after `game_mgr.process(update)`, check if a drift was returned and persist via `TickRepository.insert_drift()`.

This is a small change: `_update_drift()` currently sets `state.baseline` directly and logs. We add a return value without changing the existing mutation pattern.

### Phase 6: Orchestrator Integration — Tick Capture (FR-001, FR-002)

In `orchestrator.py`, modify `run_flip_watch()`:

1. Create `TickRepository` from the DB pool (alongside existing `FlippeningRepository`).
2. Create `TickBuffer(tick_repo, config.flippening)`.
3. In the `async for update in stream:` loop, call `tick_buffer.append(enriched)` before `_process_update()`.
4. On the timer loop (alongside telemetry persist), call `await tick_buffer.flush()` every `tick_flush_interval_seconds`.
5. In the `finally` block, call `await tick_buffer.flush()` to drain remaining ticks.
6. Wire drift persistence: capture drift return from `_process_update()` path.

The tick buffer creation is conditional on `config.flippening.capture_ticks and not dry_run`.

### Phase 7: Replay Models (FR-008, FR-010)

Create `models/replay.py`:

```python
class ReplaySignal(BaseModel):
    """A hypothetical signal produced during replay."""
    market_id: str
    entry_price: Decimal
    exit_price: Decimal
    exit_reason: ExitReason
    realized_pnl: Decimal
    hold_minutes: Decimal
    confidence: Decimal
    side: str
    entry_at: datetime
    exit_at: datetime

class ReplayEvaluation(BaseModel):
    """Aggregate metrics from a set of replay signals."""
    total_signals: int
    win_count: int
    win_rate: float
    avg_pnl: float
    avg_hold_minutes: float
    max_drawdown: float
    profit_factor: float  # gross wins / gross losses
    config_overrides: dict[str, Any] = {}

class SweepResult(BaseModel):
    """Result of a parameter sweep — one eval per parameter value."""
    param_name: str
    results: list[tuple[float, ReplayEvaluation]]
```

### Phase 8: ReplayEngine (FR-005, FR-006, FR-007, FR-009)

Create `flippening/replay_engine.py`:

```python
class ReplayEngine:
    def __init__(
        self,
        tick_repo: TickRepository,
        flip_repo: FlippeningRepository,
        base_config: FlippeningConfig,
    ):
        self._tick_repo = tick_repo
        self._flip_repo = flip_repo
        self._base_config = base_config

    async def replay_market(
        self,
        market_id: str,
        since: datetime,
        until: datetime,
        overrides: dict[str, Any] | None = None,
    ) -> list[ReplaySignal]:
        """Replay a single market's ticks through spike/signal pipeline."""

    async def replay_sport(
        self,
        sport: str,
        since: datetime,
        until: datetime,
        overrides: dict[str, Any] | None = None,
    ) -> list[ReplaySignal]:
        """Replay all markets for a sport in the time range."""
```

`replay_market()` implementation:
1. Apply config overrides via `base_config.model_copy(update=overrides)` (FR-009).
2. Validate the merged config — Pydantic catches invalid values (EC-004).
3. Load baseline from `flippening_baselines` for this market_id — skip if missing (EC-002).
4. Load drifts from `flippening_baseline_drifts` for this market + time range, sorted by timestamp (EC-003: skip any before baseline.captured_at).
5. Create fresh `SpikeDetector(config)`, `SignalGenerator(config)`.
6. Create a minimal `GameState` with the loaded baseline.
7. Stream ticks via `tick_repo.stream_ticks()` (EC-007: cursor, not bulk load).
8. For each tick:
   - Convert DB record to `PriceUpdate`.
   - Append to `price_history` deque.
   - Check if a drift record applies at this timestamp — update baseline if so (FR-007).
   - If no active signal: call `spike_detector.check_spike()`.
   - If spike detected: call `signal_gen.create_entry()`, record as active.
   - If active signal: call `signal_gen.check_exit()`.
   - If exit: create `ReplaySignal`, clear active, append to results.
9. Return `list[ReplaySignal]`.

`replay_sport()`:
1. Call `tick_repo.get_market_ids(sport, since, until)` to find markets.
2. For each market, call `replay_market()`.
3. Concatenate results (EC-001: market with zero ticks returns empty list).

### Phase 9: Evaluation + Sweep (FR-010, FR-011)

Create `flippening/replay_evaluator.py`:

```python
def evaluate_replay(signals: list[ReplaySignal]) -> ReplayEvaluation:
    """Compute aggregate metrics from replay signals."""
```

Implementation:
- `win_count` = signals where `exit_reason == ExitReason.REVERSION`.
- `win_rate` = win_count / total (or 0 if total == 0).
- `avg_pnl` = mean of `realized_pnl`.
- `avg_hold_minutes` = mean of `hold_minutes`.
- `max_drawdown` = largest peak-to-trough decline in cumulative P&L sequence.
- `profit_factor` = sum(positive pnl) / abs(sum(negative pnl)), or 0 if no losses.

```python
async def sweep_parameter(
    engine: ReplayEngine,
    sport: str,
    since: datetime,
    until: datetime,
    param_name: str,
    min_val: float,
    max_val: float,
    step: float,
) -> SweepResult:
    """Run replays across a range of values for a single parameter."""
```

Implementation:
- Generate values from `min_val` to `max_val` (inclusive) in `step` increments.
- For each value, call `engine.replay_sport(sport, since, until, {param_name: value})`.
- Run `evaluate_replay()` on results.
- Return `SweepResult` with all (value, evaluation) pairs.
- EC-006: If no ticks exist for the sport, each replay returns empty → evaluation has 0 signals → grid still returned.

### Phase 10: CLI Commands (FR-012, FR-013, FR-014, FR-015)

Register 4 new commands in `flippening_commands.py`:

**`flip-replay`** (FR-012):
- Options: `--market-id`, `--sport`, `--since`, `--until`, `--override` (repeatable, format `key=value`), `--format table|json`.
- Requires either `--market-id` or `--sport`.
- Parses `--override` into dict, delegates to `ReplayEngine.replay_market()` or `replay_sport()`.
- Renders signal list as table or JSON.

**`flip-evaluate`** (FR-013):
- Options: `--sport` (required), `--since`, `--until`, `--format table|json`.
- Replays all markets for sport, runs `evaluate_replay()`, prints summary.

**`flip-sweep`** (FR-014):
- Options: `--param` (required), `--min`, `--max`, `--step`, `--sport` (required), `--since`, `--until`, `--format table|json`.
- Runs `sweep_parameter()`, prints grid of param_value → metrics.

**`flip-tick-prune`** (FR-015):
- Options: `--days` (override retention, default from config), `--dry-run` (show count without deleting).
- Calls `tick_repo.prune_ticks()`, prints count deleted.

Async helpers and renderers go in `cli/_replay_helpers.py` to keep `flippening_commands.py` under 300 lines.

### Phase 11: Tests

**`test_tick_buffer.py`**:
- Buffer append and auto-flush at capacity.
- Manual flush drains buffer.
- Flush failure drops buffer without raising (EC-005).
- Disabled buffer (capture_ticks=False) silently no-ops.
- Dry run (repo=None) silently no-ops.

**`test_replay_engine.py`**:
- Replay known tick sequence → expected entry/exit signals.
- Config override changes threshold → different signals.
- Drift record updates baseline mid-replay.
- Missing baseline → empty result with warning (EC-002).
- Empty tick range → empty result (EC-001).
- Invalid config override → validation error (EC-004).
- Drift before baseline → skipped (EC-003).

**`test_replay_evaluator.py`**:
- All wins → 100% win rate, profit factor = inf (or capped).
- All losses → 0% win rate.
- Mixed → correct win_rate, avg_pnl, profit_factor.
- Empty signals → all zeros.
- Max drawdown calculation with known P&L sequence.
- Sweep with multiple values → result per value.
- Sweep with zero ticks → empty evaluations (EC-006).

**`test_replay_cli.py`**:
- `flip-replay --market-id X --since ... --until ...` with mocked engine.
- `flip-evaluate --sport nba` with mocked engine.
- `flip-sweep --param spike_threshold_pct --min 0.08 --max 0.20 --step 0.04` with mocked engine.
- `flip-tick-prune --days 30` with mocked repo.
- `flip-tick-prune --dry-run` shows count without deletion.

## Edge Case Handling

| Edge Case | Handling | Phase |
|-----------|----------|-------|
| EC-001: Zero ticks in range | `replay_market()` returns `[]`, evaluate shows 0 signals | Phase 8 |
| EC-002: Missing baseline | Skip market, log warning | Phase 8 |
| EC-003: Drift before baseline | Sort drifts by timestamp, skip any with `drifted_at < baseline.captured_at` | Phase 8 |
| EC-004: Invalid config override | Pydantic validation on `model_copy(update=)`, raise `ValueError` | Phase 8 |
| EC-005: Tick flush DB failure | Log warning, drop buffer, continue live engine | Phase 4 |
| EC-006: Sweep with no ticks | Each evaluation has 0 signals, grid still rendered | Phase 9 |
| EC-007: Millions of ticks | asyncpg cursor streaming in `stream_ticks()`, never `fetchall()` | Phase 3 |

## Module Size Compliance

All new files stay under 300 lines:
- `tick_buffer.py`: ~60 lines (buffer logic is simple)
- `replay_engine.py`: ~150 lines (two public methods + tick processing loop)
- `replay_evaluator.py`: ~80 lines (pure math, no I/O)
- `models/replay.py`: ~40 lines (3 models)
- `_tick_queries.py`: ~50 lines (SQL constants)
- `tick_repository.py`: ~100 lines (6 methods)
- `_replay_helpers.py`: ~120 lines (CLI renderers)

`flippening_commands.py` currently ~308 lines — the 4 new commands will push it over. Solution: the command function bodies stay thin (parse args, delegate to `_replay_helpers.py` async functions), keeping the file around 380 lines. Alternatively, extract replay commands to `replay_commands.py` if needed. Pre-1.0, 380 lines is acceptable; split if it reaches 400.

`orchestrator.py` currently ~687 lines — already over 300. The tick buffer adds ~10 lines. This is existing tech debt; no new architectural changes needed.

## Performance Notes

- **SC-004 (< 5ms p95)**: `TickBuffer.append()` is a list append — O(1), sub-microsecond. Flush is async and non-blocking. The hot path never awaits DB.
- **SC-005 (prune without locking)**: `DELETE WHERE timestamp < $1` with a partial index on timestamp. For very large tables, batch delete in chunks (`DELETE ... LIMIT 10000` in a loop) to avoid lock escalation.
- **EC-007 (cursor streaming)**: asyncpg `conn.cursor()` returns records one-at-a-time. Memory usage stays constant regardless of tick count.

## Quality Gates

All must pass after each phase:
1. `ruff check` — zero errors
2. `ruff format --check` — clean
3. `mypy src/ --strict` — zero errors
4. `pytest tests/ -x` — all pass
5. `pytest --cov --cov-fail-under=70` — coverage maintained
