# 021 — Flippening Exit Execution: Tasks

## T-001: DB migration — `flippening_auto_positions`
**Depends on**: none
**Files**: `src/arb_scanner/storage/migrations/018_flippening_auto_positions.sql`

Create `flippening_auto_positions` table with `status` check constraint, unique partial
index on open positions, and status index. Follow existing migration file naming convention.

Acceptance: `uv run arb-scanner migrate` applies cleanly on a fresh DB and a DB that
already has migrations 001–017.

---

## T-002: `_flip_position_queries.py` — SQL query constants
**Depends on**: T-001
**Files**: `src/arb_scanner/storage/_flip_position_queries.py`

Constants (no logic):
- `INSERT_FLIP_POSITION` — insert returning id
- `GET_OPEN_POSITION` — SELECT WHERE market_id AND status='open'
- `CLOSE_POSITION` — UPDATE status='closed', set exit_order_id, exit_price, realized_pnl, exit_reason, closed_at
- `MARK_EXIT_FAILED` — UPDATE status='exit_failed'
- `GET_ORPHANED_POSITIONS` — SELECT WHERE status='open' ORDER BY opened_at

---

## T-003: `FlipPositionRepo` — repository class
**Depends on**: T-002
**Files**: `src/arb_scanner/execution/flip_position_repo.py`

Implement five async methods wrapping T-002 queries. Follow pattern from
`flippening_repository.py` (asyncpg pool, structlog). All public methods have docstrings.

---

## T-004: `tests/unit/test_flip_position_repo.py`
**Depends on**: T-003
**Files**: `tests/unit/test_flip_position_repo.py`

Mock `asyncpg` pool (same fixture as `test_flippening_repository.py`). Test:
- `insert_position` returns a UUID string
- `get_open_position` returns dict when found, None when missing
- `close_position` succeeds
- `mark_exit_failed` succeeds
- `get_orphaned_positions` returns list

---

## T-005: `FlipExitExecutor` — exit order placement
**Depends on**: T-003
**Files**: `src/arb_scanner/execution/flip_exit_executor.py`

Implement `execute_exit()`, `_build_sell_request()`, `_compute_realized_pnl()`.
Key behaviours:
- No open position → log + return None, no order placed
- STOP_LOSS exit → apply `stop_loss_aggression_pct` to limit price
- Successful sell → call `position_repo.close_position()`, return order_id
- Failed sell → call `position_repo.mark_exit_failed()`, re-raise for circuit breaker to count

Inject: `PolymarketExecutor`, `ExecutionRepository`, `FlipPositionRepo`, `float`.

---

## T-006: `tests/unit/test_flip_exit_executor.py`
**Depends on**: T-005
**Files**: `tests/unit/test_flip_exit_executor.py`

Mock all three injected dependencies. Test:
- Happy path: sell submitted → `close_position` called with correct args
- No open position → returns None, no executor call
- `STOP_LOSS` exit: sell price is reduced by `stop_loss_aggression_pct`
- Executor raises → `mark_exit_failed` called, exception propagates

---

## T-007: `_orch_exit.py` — exit pipeline feed function
**Depends on**: T-005
**Files**: `src/arb_scanner/flippening/_orch_exit.py`

Implement `_feed_exit_pipeline(event, entry, exit_sig, config)`. Mirror exactly the
structure of `_feed_auto_pipeline()` in `_orch_processing.py`:
- Guard: pipeline exists and mode == "auto"
- Call `pipeline.process_exit(exit_sig, entry, event)`
- Wrap in try/except, log warning on failure

---

## T-008: `tests/unit/test_orch_exit.py`
**Depends on**: T-007
**Files**: `tests/unit/test_orch_exit.py`

Test:
- Auto mode: `pipeline.process_exit()` is called
- Non-auto mode: `process_exit()` is NOT called
- Exception in pipeline: swallowed, no re-raise
- `_auto_pipeline` attr missing on config: no call, no exception

---

## T-009: Add `process_exit()` to `AutoExecutionPipeline`
**Depends on**: T-005, T-007
**Files**: `src/arb_scanner/execution/auto_pipeline.py`

- Add `exit_executor: FlipExitExecutor | None` and `position_repo: FlipPositionRepo | None`
  to `__init__()` (both optional with default `None` for backward compat).
- Add `process_exit(exit_sig, entry_sig, event)` method: mode gate → delegate to executor.
- Add `_register_flip_position(result, opportunity)` helper: extracts fields from
  `ExecutionResult` + opportunity dict, calls `position_repo.insert_position()`.
  Called from `_execute_pipeline()` after successful execute when `ticket_type="flippening"`.

---

## T-010: `tests/unit/test_auto_pipeline_exit.py`
**Depends on**: T-009
**Files**: `tests/unit/test_auto_pipeline_exit.py`

Test:
- `process_exit()` in auto mode delegates to `exit_executor.execute_exit()`
- `process_exit()` in off/manual mode returns immediately without calling executor
- `_register_flip_position()` called after successful flippening execute
- `_register_flip_position()` NOT called for arbitrage tickets
- `_register_flip_position()` NOT called if execute result is "failed"

---

## T-011: Wire `handle_exit()` → `_feed_exit_pipeline()`
**Depends on**: T-007
**Files**: `src/arb_scanner/flippening/_orch_processing.py`

In `handle_exit()`, after `alert_buffer.append_exit(...)`, add:

```python
await _feed_exit_pipeline(event, entry, exit_sig, config)
```

Import `_feed_exit_pipeline` from `._orch_exit`. This is a 2-line change.

---

## T-012: Add `stop_loss_aggression_pct` to config
**Depends on**: none
**Files**: `src/arb_scanner/models/_auto_exec_config.py`, `config.example.yaml`

Add `stop_loss_aggression_pct: float = 0.02` to `AutoExecutionConfig`. Add commented
entry to `config.example.yaml` under `auto_execution:`.

---

## T-013: Startup orphan check
**Depends on**: T-003
**Files**: `src/arb_scanner/flippening/orchestrator.py` (or `_orch_processing.py`)

In the orchestrator startup path (before the main poll loop), call:
```python
orphans = await position_repo.get_orphaned_positions()
```
If any exist, log a warning and dispatch a Slack/Discord alert listing each orphan's
`market_id`, `side`, `size_contracts`, and `entry_price`. Reuse `dispatch_flip_alert()`.

---

## T-014: REST endpoint `POST /api/execution/flip-exit/{arb_id}`
**Depends on**: T-005, T-003
**Files**: `src/arb_scanner/api/routes_execution.py`

New route:
1. Fetch ticket by `arb_id` → extract `market_id`.
2. Fetch open position from `FlipPositionRepo`.
3. If no open position → 404.
4. Fetch current best bid from Polymarket (to use as limit sell price).
5. Build synthetic `ExitSignal` with `exit_reason=MANUAL`, `exit_price=best_bid`.
6. Call `FlipExitExecutor.execute_exit()`.
7. Return `{"order_id": ..., "status": ..., "price": ...}`.

Inject `FlipPositionRepo` and `FlipExitExecutor` via `app.state` (same as existing repos).

---

## T-015: Dashboard — position display + "Exit Now" button
**Depends on**: T-014
**Files**: `src/arb_scanner/api/static/app.js`, `src/arb_scanner/api/static/index.html`

Extend `openTicketDetail()` for flippening tickets (after fetching execution orders):
- Fetch `GET /api/execution/orders/{arb_id}` to get execution order with `action=buy`.
- If open position exists (check via a new `GET /api/execution/flip-position/{arb_id}`
  endpoint or derive from order status), show:
  - Contracts held, entry price, estimated current P&L (using last known spike price).
  - "Exit Now" button: calls `POST /api/execution/flip-exit/{arb_id}`, then reloads detail.
- If position closed: show realized P&L, exit price, exit reason.

Add `GET /api/execution/flip-position/{arb_id}` route that returns the open position dict
(or 404 if none).

---

## T-016: Inject new components in `app.py`
**Depends on**: T-003, T-005, T-009
**Files**: `src/arb_scanner/api/app.py`

In `_init_auto_execution()`:
```python
position_repo = FlipPositionRepo(pool)
exit_executor = FlipExitExecutor(
    poly_executor, exec_repo, position_repo,
    config.auto_execution.stop_loss_aggression_pct,
)
pipeline = AutoExecutionPipeline(
    ...,
    exit_executor=exit_executor,
    position_repo=position_repo,
)
app.state.flip_position_repo = position_repo
```

---

## T-017: Full quality gate pass
**Depends on**: all above
**Files**: all

Run:
```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/ --strict
uv run pytest tests/ -x --tb=short
uv run pytest tests/ --cov=src/arb_scanner --cov-fail-under=70
```

Fix any issues. Confirm all five gates pass.

---

## Dependency Order

```
T-001 (migration)
  └── T-002 (queries)
        └── T-003 (repo)
              ├── T-004 (repo tests)
              ├── T-005 (exit executor)
              │     ├── T-006 (executor tests)
              │     ├── T-007 (feed function)
              │     │     ├── T-008 (feed tests)
              │     │     └── T-011 (wire handle_exit)
              │     ├── T-009 (pipeline process_exit)
              │     │     └── T-010 (pipeline tests)
              │     ├── T-013 (orphan check)
              │     └── T-014 (REST endpoint)
              │           └── T-015 (dashboard)
T-012 (config field) — independent, can run anytime
T-016 (app.py injection) — depends on T-003, T-005, T-009
T-017 (quality gates) — final
```
