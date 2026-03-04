# 021 — Flippening Exit Execution: Implementation Plan

## Architecture Summary

Three concerns wire together in a simple chain:

```
ExitSignal fires
  → _feed_exit_pipeline()          [new: flippening/_orch_exit.py]
  → AutoExecutionPipeline.process_exit()  [extend: execution/auto_pipeline.py]
  → FlipExitExecutor.execute_exit()       [new: execution/flip_exit_executor.py]
  → PolymarketExecutor.place_order()      [existing, no changes]
  → FlipPositionRepo.close_position()     [new: execution/flip_position_repo.py]
  → dispatch_auto_exec_alert()            [existing, no changes]
```

Entry registration:
```
AutoExecutionPipeline._execute_pipeline() → (flippening ticket only)
  → FlipPositionRepo.insert_position()    [new]
```

## Phase 1: Database

### 1.1 Migration

**File**: `src/arb_scanner/storage/migrations/018_flippening_auto_positions.sql`

```sql
CREATE TABLE IF NOT EXISTS flippening_auto_positions (
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

CREATE UNIQUE INDEX IF NOT EXISTS flippening_auto_positions_market_open
    ON flippening_auto_positions (market_id)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS flippening_auto_positions_status
    ON flippening_auto_positions (status);
```

Run via: `uv run arb-scanner migrate`

## Phase 2: Repository Layer

### 2.1 `FlipPositionRepo`

**File**: `src/arb_scanner/execution/flip_position_repo.py`

```python
class FlipPositionRepo:
    """CRUD for flippening_auto_positions."""
    def __init__(self, pool: asyncpg.Pool) -> None: ...

    async def insert_position(
        self, arb_id: str, market_id: str, token_id: str,
        side: str, size_contracts: int, entry_price: Decimal,
        venue_order_id: str | None,
    ) -> str:  # returns position id
        """Insert open position after successful entry order."""

    async def get_open_position(self, market_id: str) -> dict[str, Any] | None:
        """Return open position for market, or None."""

    async def close_position(
        self, market_id: str, exit_order_id: str,
        exit_price: Decimal, realized_pnl: Decimal, exit_reason: str,
    ) -> None:
        """Mark position closed with exit details."""

    async def mark_exit_failed(self, market_id: str) -> None:
        """Mark position exit_failed after sell order rejection."""

    async def get_orphaned_positions(self) -> list[dict[str, Any]]:
        """Return all open positions (for startup orphan check)."""
```

SQL queries live in `storage/_flip_position_queries.py` (keeping the pattern used by
`_ticket_queries.py` and `_flippening_queries.py`).

## Phase 3: Exit Executor

### 3.1 `FlipExitExecutor`

**File**: `src/arb_scanner/execution/flip_exit_executor.py`

```python
class FlipExitExecutor:
    """Places Polymarket sell orders for open flippening positions."""

    def __init__(
        self,
        poly: PolymarketExecutor,
        exec_repo: ExecutionRepository,
        position_repo: FlipPositionRepo,
        stop_loss_aggression_pct: float = 0.02,
    ) -> None: ...

    async def execute_exit(
        self,
        exit_sig: ExitSignal,
        entry_sig: EntrySignal,
        event: FlippeningEvent,
    ) -> str | None:
        """
        Place sell order for the open position on event.market_id.
        Returns execution order_id on success, None if no open position.
        """
```

Internal helpers (all < 50 lines):
- `_build_sell_request(position, exit_sig, aggression_pct) -> OrderRequest`
- `_compute_realized_pnl(entry_price, fill_price, size_contracts) -> Decimal`
- `_dispatch_exit_alert(entry, exit_status, order_id, config)`

### 3.2 Sell Order Construction

```python
def _build_sell_request(
    position: dict[str, Any],
    exit_sig: ExitSignal,
    aggression_pct: float,
) -> OrderRequest:
    price = Decimal(str(exit_sig.exit_price))
    if exit_sig.exit_reason == ExitReason.STOP_LOSS:
        price = price * (1 - Decimal(str(aggression_pct)))
    side: OrderSide = f"sell_{position['side']}"  # sell_yes or sell_no
    return OrderRequest(
        venue="polymarket",
        side=side,
        price=price,
        size_usd=Decimal("0"),        # not used for sells
        size_contracts=position["size_contracts"],
        token_id=position["token_id"],
    )
```

## Phase 4: Orchestrator Wiring

### 4.1 `_feed_exit_pipeline()` — new file

**File**: `src/arb_scanner/flippening/_orch_exit.py`

```python
async def _feed_exit_pipeline(
    event: FlippeningEvent,
    entry: EntrySignal,
    exit_sig: ExitSignal,
    config: Settings,
) -> None:
    """Feed exit signal to AutoExecutionPipeline.process_exit() if mode=auto."""
    try:
        from arb_scanner.execution.auto_pipeline import AutoExecutionPipeline
        pipeline: AutoExecutionPipeline | None = getattr(config, "_auto_pipeline", None)
        if pipeline is None or pipeline.mode != "auto":
            return
        await pipeline.process_exit(exit_sig, entry, event)
    except Exception:
        logger.warning("auto_pipeline_exit_feed_failed")
```

### 4.2 Extend `handle_exit()` in `_orch_processing.py`

Add one line after the existing alert buffer append:

```python
await _feed_exit_pipeline(event, entry, exit_sig, config)
```

Import `_feed_exit_pipeline` from the new `_orch_exit.py`.

### 4.3 `AutoExecutionPipeline.process_exit()`

Add to `auto_pipeline.py`:

```python
async def process_exit(
    self,
    exit_sig: ExitSignal,
    entry_sig: EntrySignal,
    event: FlippeningEvent,
) -> None:
    """Place sell order for an open flippening position."""
    if self._mode != "auto" or self._killed:
        return
    await self._exit_executor.execute_exit(exit_sig, entry_sig, event)
```

`self._exit_executor: FlipExitExecutor` injected at construction time.

### 4.4 Register Position After Entry

In `_execute_pipeline()`, after `result = await self._orchestrator.execute(arb_id, size)`,
for flippening tickets:

```python
if (
    opportunity.get("ticket_type") == "flippening"
    and result.status in ("complete", "partial")
    and self._position_repo is not None
):
    await self._register_flip_position(result, opportunity)
```

`_register_flip_position()` extracts `market_id`, `token_id`, `side`, `size_contracts`,
and `venue_order_id` from the execution result and ticket data, then calls
`FlipPositionRepo.insert_position()`.

## Phase 5: Startup Orphan Check

In `FlippeningOrchestrator.__init__()` or its `run()` startup block:

```python
orphans = await self._position_repo.get_orphaned_positions()
if orphans:
    logger.warning("orphaned_flip_positions", count=len(orphans))
    await _dispatch_orphan_alert(orphans, config, http_client)
```

`_dispatch_orphan_alert()` builds a Slack/Discord message listing each orphan with
market, side, size, entry price, and the arb_id for cross-reference.

## Phase 6: API + Dashboard

### 6.1 REST Endpoint

**File**: Extend `src/arb_scanner/api/routes_execution.py`

```
POST /api/execution/flip-exit/{arb_id}
```

- Fetch open position for the ticket's market_id.
- Build a sell request at current market price (fetch best bid, use as limit).
- Call `FlipExitExecutor.execute_exit()` directly (bypasses auto-pipeline mode check).
- Return execution order dict.

### 6.2 Dashboard

In `app.js`, extend `openTicketDetail()` for flippening tickets:
- If position is open: show "Contracts held", "Entry price", estimated P&L.
- Show "Exit Now" button (calls `POST /api/execution/flip-exit/{arb_id}`).
- If position closed: show realized P&L, exit price, exit reason.

## Phase 7: Tests

### Unit Tests

| File | Coverage |
|---|---|
| `tests/unit/test_flip_position_repo.py` | All CRUD methods, unique constraint behavior |
| `tests/unit/test_flip_exit_executor.py` | Happy path, no-position skip, stop-loss aggression, failure marking |
| `tests/unit/test_orch_exit.py` | Feed function mode gate, exception swallowing |
| `tests/unit/test_auto_pipeline_exit.py` | process_exit() mode gate, delegation to executor |

All use `asyncpg` pool mocking (same pattern as `test_flippening_repository.py`).

### Integration Touch

Extend `tests/unit/test_execution_orchestrator.py` to verify position registration
is called for flippening tickets after successful execute().

## Dependency Injection

`FlipPositionRepo` and `FlipExitExecutor` are constructed in `app.py`
`_init_auto_execution()` (same location as the existing pipeline construction),
alongside the pool and existing repos.

```python
position_repo = FlipPositionRepo(pool)
exit_executor = FlipExitExecutor(poly, exec_repo, position_repo)
pipeline = AutoExecutionPipeline(
    ...,
    exit_executor=exit_executor,
    position_repo=position_repo,
)
```

## File Change Summary

| File | Change |
|---|---|
| `storage/migrations/018_flippening_auto_positions.sql` | **NEW** |
| `execution/flip_position_repo.py` | **NEW** |
| `storage/_flip_position_queries.py` | **NEW** |
| `execution/flip_exit_executor.py` | **NEW** |
| `flippening/_orch_exit.py` | **NEW** |
| `execution/auto_pipeline.py` | Add `process_exit()`, `_register_flip_position()`, inject `exit_executor` + `position_repo` |
| `flippening/_orch_processing.py` | Call `_feed_exit_pipeline()` in `handle_exit()` |
| `models/_auto_exec_config.py` | Add `stop_loss_aggression_pct: float = 0.02` |
| `api/routes_execution.py` | Add `POST /api/execution/flip-exit/{arb_id}` |
| `api/static/app.js` | Position display + "Exit Now" button in ticket detail |
| `api/app.py` | Inject `FlipPositionRepo`, `FlipExitExecutor` into pipeline |
| `tests/unit/test_flip_position_repo.py` | **NEW** |
| `tests/unit/test_flip_exit_executor.py` | **NEW** |
| `tests/unit/test_orch_exit.py` | **NEW** |
| `tests/unit/test_auto_pipeline_exit.py` | **NEW** |

Total new modules: 6. Files touched: 6. New test files: 4.

## Risk Assessment

| Risk | Likelihood | Mitigation |
|---|---|---|
| Polymarket rejects sell on resolved market | High (markets resolve daily) | EC-005 handling: `exit_failed` + operator alert |
| Entry fill not confirmed before exit fires | Medium | EC-002: place sell anyway at limit; unfilled sell times out harmlessly |
| DB unique constraint conflict on concurrent signals | Low | UNIQUE INDEX on `(market_id) WHERE status='open'` makes insert fail; caller catches and skips |
| `size_contracts` mismatch if entry partially filled | Medium (future) | Tracked in EC-008; deferred to a follow-on; over-sell is bounded by contracts held |
