# Implementation Plan: Split Execution Paths

**Branch**: `022-split-execution-paths` | **Date**: 2026-03-04 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/022-split-execution-paths/spec.md`

## Summary

Split the monolithic `AutoExecutionPipeline` into two independent pipelines (`ArbAutoExecutionPipeline` and `FlipAutoExecutionPipeline`) to eliminate 8 `ticket_type` conditional branches across 4 modules. Each pipeline owns its evaluator, critic, and failure breaker while sharing the capital manager, loss limits, and mode control. The dashboard remains unified with explicit `pipeline_type` labels replacing field-presence heuristics.

Root cause fix: flippening trades no longer route through `ExecutionOrchestrator.execute()` (which requires arb tickets), eliminating the silent failure → breaker trip cascade.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: httpx, pydantic v2, anthropic SDK, asyncpg, structlog, FastAPI, typer
**Storage**: PostgreSQL + pgvector (asyncpg, no ORM). Tables: `auto_execution_log`, `auto_execution_positions`, `flippening_auto_positions`, `execution_orders`
**Testing**: pytest + pytest-asyncio, mocked asyncpg pools
**Target Platform**: Linux server (Docker) + macOS local dev
**Project Type**: CLI + web dashboard (FastAPI serving vanilla JS)
**Performance Goals**: Pipeline processing < 5s per opportunity
**Constraints**: <300 lines/module, <50 lines/function, ≥70% coverage
**Scale/Scope**: ~760 source files, 1248 tests, 2 concurrent pipelines

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Human-in-the-Loop | PASS | Pipelines still produce execution actions under operator control (mode: off/manual/auto). No change to human oversight model. |
| II. Pydantic at Every Boundary | PASS | `AutoExecLogEntry`, `CriticVerdict`, `OrderRequest`, `OrderResponse` remain at all boundaries. New pipeline classes accept typed config, return typed results. |
| III. Async-First I/O | PASS | All execution paths use async/await. No sync HTTP introduced. |
| IV. Structured Logging | PASS | structlog JSON logging maintained. Pipeline name added to log context (FR-013). |
| V. Two-Pass Matching | N/A | Matching pipeline not affected by this refactor. |
| VI. Configuration Over Code | PASS | Pipeline thresholds remain in YAML config via `AutoExecutionConfig`. Per-pipeline overrides added to config model. |

**Quality Gates**:
- ruff check: enforced
- ruff format: enforced
- mypy --strict: enforced
- pytest all pass: enforced
- coverage ≥70%: enforced

**No violations. No complexity tracking needed.**

## Project Structure

### Documentation (this feature)

```text
specs/022-split-execution-paths/
├── plan.md              # This file
├── research.md          # Phase 0 output (8 research decisions)
├── data-model.md        # Phase 1 output (entity + relationship diagrams)
├── quickstart.md        # Phase 1 output (key files + verification)
├── contracts/
│   └── pipeline-interface.md  # Pipeline control + dashboard API contracts
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
src/arb_scanner/
├── execution/
│   ├── arb_pipeline.py          # NEW: ArbAutoExecutionPipeline
│   ├── flip_pipeline.py         # NEW: FlipAutoExecutionPipeline
│   ├── arb_evaluator.py         # NEW: Arb-specific criteria evaluation
│   ├── flip_evaluator.py        # NEW: Flip-specific criteria evaluation
│   ├── arb_critic.py            # NEW: Arb-specific trade critic
│   ├── flip_critic.py           # NEW: Flip-specific trade critic
│   ├── auto_pipeline.py         # DELETE after migration
│   ├── auto_evaluator.py        # DELETE after migration
│   ├── trade_critic.py          # DELETE after migration (shared logic extracted)
│   ├── _critic_prompts.py       # MODIFY: Remove ticket_type branching, keep prompt constants
│   ├── _auto_slippage.py        # UNCHANGED (already venue-agnostic)
│   ├── circuit_breaker.py       # UNCHANGED (two instances created at startup)
│   ├── capital_manager.py       # UNCHANGED (single shared instance)
│   ├── orchestrator.py          # UNCHANGED (arb pipeline continues using it)
│   ├── flip_exit_executor.py    # UNCHANGED (flip pipeline uses it for exits)
│   ├── flip_position_repo.py    # UNCHANGED
│   ├── activity_feed.py         # MODIFY: Add pipeline field to events
│   └── auto_sizing.py           # UNCHANGED
├── api/
│   ├── app.py                   # MODIFY: Create two pipeline instances
│   ├── routes_auto_execution.py # MODIFY: Per-pipeline breaker status
│   ├── routes_execution.py      # UNCHANGED
│   └── static/
│       ├── app.js               # MODIFY: Use pipeline_type, fix closePosition
│       ├── index.html           # MODIFY: Add Type column, per-pipeline breakers
│       └── style.css            # UNCHANGED
├── flippening/
│   ├── _orch_processing.py      # MODIFY: Feed FlipAutoExecutionPipeline
│   └── _orch_exit.py            # MODIFY: Call flip pipeline's process_exit
├── cli/
│   └── orchestrator.py          # MODIFY: Feed ArbAutoExecutionPipeline
├── models/
│   └── _auto_exec_config.py     # MODIFY: Add per-pipeline override dicts
├── notifications/
│   └── auto_exec_webhook.py     # MODIFY: Add pipeline label to alerts
└── storage/
    └── _execution_queries.py    # UNCHANGED

tests/
├── unit/
│   ├── test_arb_pipeline.py     # NEW
│   ├── test_flip_pipeline.py    # NEW
│   ├── test_arb_evaluator.py    # NEW
│   ├── test_flip_evaluator.py   # NEW
│   ├── test_arb_critic.py       # NEW
│   ├── test_flip_critic.py      # NEW
│   ├── test_auto_pipeline.py    # DELETE (replaced by above)
│   ├── test_auto_evaluator.py   # DELETE (replaced by above)
│   └── test_trade_critic.py     # DELETE (replaced by above)
└── integration/
    └── test_pipeline_isolation.py  # NEW: Verify independent breaker behavior
```

**Structure Decision**: Follows existing `src/arb_scanner/execution/` module layout. New files are siblings to existing execution modules. Each new module stays under 300 lines. Test files mirror source structure 1:1.

## Design Decisions

### D-001: No Base Class

Both pipelines share dependencies (injected via constructor) but not behavior. A base class would contain almost no logic — just constructor boilerplate. Using composition (injected deps) over inheritance keeps each pipeline self-contained and independently testable.

### D-002: Flip Pipeline Direct Execution

The flip pipeline calls `PolymarketExecutor.place_order()` directly instead of going through `ExecutionOrchestrator.execute()`. This bypasses the ticket lookup that caused the original bug. The `FlipExitExecutor` already follows this pattern successfully.

### D-003: Shared Log Table

Both pipelines write to the same `auto_execution_log` table with a `source` field distinguishing them. No schema migration needed — the `source` column already exists and is populated with `"arb_watch"` or `"flippening"`.

### D-004: Two CircuitBreakerManager Instances

Same class, two instances. Each pipeline's constructor receives its own `CircuitBreakerManager`. The loss breaker's `check_loss()` reads `CapitalManager.daily_pnl` which is global — both instances see the same daily P&L.

### D-005: Prompt Constants Stay in _critic_prompts.py

The `CRITIC_SYSTEM_PROMPT` and `FLIPPENING_CRITIC_SYSTEM_PROMPT` constants and `build_critic_prompt()` function stay in `_critic_prompts.py`. The branching is removed — each critic module imports only its relevant prompt. `build_critic_prompt()` is split into `build_arb_critic_prompt()` and `build_flip_critic_prompt()`.

### D-006: Mode Control Shared

Both pipelines share a single mode state. When operator sets mode to "auto", both pipelines activate. When "off", both stop. This matches current behavior. If per-pipeline mode is desired later, it's a config extension not a structural change.

## Migration Strategy

### Phase 1: Create New Modules (additive, no breaking changes)

1. Create `arb_pipeline.py`, `flip_pipeline.py` with full pipeline logic extracted from `auto_pipeline.py`
2. Create `arb_evaluator.py`, `flip_evaluator.py` from `auto_evaluator.py`
3. Create `arb_critic.py`, `flip_critic.py` from `trade_critic.py`
4. Update `_critic_prompts.py` to export separate builder functions
5. Write all new test files

### Phase 2: Wire New Pipelines (switchover)

6. Update `app.py` to create both pipeline instances, store on `app.state`
7. Update `_orch_processing.py` to feed `FlipAutoExecutionPipeline`
8. Update `_orch_exit.py` to call flip pipeline's `process_exit()`
9. Update `cli/orchestrator.py` to feed `ArbAutoExecutionPipeline`
10. Update dashboard API routes for per-pipeline breaker status

### Phase 3: Dashboard + Cleanup

11. Update `app.js` and `index.html` for explicit `pipeline_type` and per-pipeline breakers
12. Fix `closePosition()` frontend bug
13. Add `pipeline` field to activity feed events
14. Delete old modules (`auto_pipeline.py`, `auto_evaluator.py`, `trade_critic.py`)
15. Delete old test files
16. Verify: `grep -r "ticket_type" src/arb_scanner/execution/` returns zero matches
