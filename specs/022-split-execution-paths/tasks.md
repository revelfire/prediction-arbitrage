# Tasks: Split Execution Paths

**Input**: Design documents from `/specs/022-split-execution-paths/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Split shared modules into separate exports so both pipelines can import cleanly

- [X] T001 Split `build_critic_prompt()` in `src/arb_scanner/execution/_critic_prompts.py` into two functions: `build_arb_critic_prompt()` (uses poly_yes_price, kalshi_yes_price, poly_depth, kalshi_depth, spread) and `build_flip_critic_prompt()` (uses entry_price, side, baseline_deviation_pct, market_id). Remove the `if ticket_type == "flippening"` branch. Keep both system prompt constants (`CRITIC_SYSTEM_PROMPT`, `FLIPPENING_CRITIC_SYSTEM_PROMPT`) as-is. Existing `build_critic_prompt()` should delegate to the appropriate new function for backward compatibility until old modules are deleted.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Config model extension needed before pipelines can be created

**CRITICAL**: No user story work can begin until this phase is complete

- [X] T002 Add optional `arb_overrides: dict[str, Any]` and `flip_overrides: dict[str, Any]` fields (default empty dict) to `AutoExecutionConfig` in `src/arb_scanner/models/_auto_exec_config.py`. Add a helper method `effective_config(pipeline_type: str) -> AutoExecutionConfig` that returns a copy with overrides applied. This allows per-pipeline thresholds (e.g., flip may allow higher max_spread_pct) while maintaining backward compatibility. Update `tests/unit/test_auto_exec_config.py` with tests for the new method.

**Checkpoint**: Foundation ready — user story implementation can now begin in parallel

---

## Phase 3: User Story 1 — Independent Flippening Execution (Priority: P1) MVP

**Goal**: Flippening trades execute end-to-end through a dedicated pipeline without touching execution_tickets table or arb failure breaker.

**Independent Test**: Trigger a flippening signal with auto-execution enabled → trade executes via PolymarketExecutor.place_order() directly → position registered in flippening_auto_positions → arb breaker state unchanged.

### Implementation for User Story 1

- [X] T003 [P] [US1] Create `src/arb_scanner/execution/flip_evaluator.py` with function `evaluate_flip_criteria(opportunity, config, capital, breakers, open_positions)` that returns `(passed: bool, reasons: list[str])`. Extract flip-specific logic from `auto_evaluator.py`: confidence minimum, category filtering, daily loss limit, max open positions, duplicate detection. Do NOT include spread bounds (min/max_spread_pct) — large deviation IS the signal for flippening. Include structlog logging with `pipeline="flip"` context.

- [X] T004 [P] [US1] Create `src/arb_scanner/execution/flip_critic.py` with class `FlipTradeCritic` extracted from `trade_critic.py`. Constructor takes `CriticConfig`. Method `evaluate(ticket, context)` returns `CriticVerdict`. Mechanical flags: stale price, anomalous deviation, price symmetry, title risk terms. Skip venue depth checks entirely (no poly_depth/kalshi_depth). Use `FLIPPENING_CRITIC_SYSTEM_PROMPT` constant directly (no conditional). Call `build_flip_critic_prompt()` from `_critic_prompts.py`. Include timeout tracking and Claude API call logic (extract shared `_call_claude()` helper or duplicate the ~30 lines of API call + JSON parse).

- [X] T005 [US1] Create `src/arb_scanner/execution/flip_pipeline.py` with class `FlipAutoExecutionPipeline`. Constructor takes: `config: Settings`, `auto_config: AutoExecutionConfig`, `critic: FlipTradeCritic`, `breakers: CircuitBreakerManager`, `capital: CapitalManager`, `poly: PolymarketExecutor`, `position_repo: FlipPositionRepo`, `auto_repo: AutoExecRepository`, `exec_repo: ExecutionRepository`, `exit_executor: FlipExitExecutor | None`. Methods: `process_opportunity(opportunity, source)` → evaluates via `evaluate_flip_criteria()`, calls critic, places single-leg order via `self._poly.place_order()` directly (NOT through ExecutionOrchestrator), registers position via `FlipPositionRepo.insert_position()`, records to `auto_execution_log`. `process_exit(exit_sig, entry_sig, event)` → delegates to `FlipExitExecutor.execute_exit()`. `set_mode()`, `kill()`, `mode` property for control. Build flip-specific market context inline (entry_price, side, baseline_deviation_pct, market_id — no poly/kalshi depth fields). Use `push_activity()` with `pipeline="flip"` field. Module must be <300 lines.

- [X] T006 [P] [US1] Write `tests/unit/test_flip_evaluator.py` with tests: passes when all criteria met, rejects on low confidence, rejects on blocked category, rejects on daily loss exceeded, rejects on max open positions, does NOT reject on large spread (deviation is the signal). Mock `CapitalManager` and `CircuitBreakerManager`.

- [X] T007 [P] [US1] Write `tests/unit/test_flip_critic.py` with tests: returns clean verdict when no flags, raises stale price flag, raises anomalous deviation flag, does NOT check poly_depth or kalshi_depth, uses `FLIPPENING_CRITIC_SYSTEM_PROMPT`, calls `build_flip_critic_prompt()`. Mock Anthropic API client.

- [X] T008 [US1] Write `tests/unit/test_flip_pipeline.py` with tests: process_opportunity happy path (evaluate → critic → place_order → register_position → log), rejects when evaluator fails, rejects when critic kills, records failure on execution error (breakers.record_failure()), records success on execution complete (breakers.record_success()), process_exit delegates to exit_executor, mode control (off/manual skip processing), kill switch prevents all trades. Mock all dependencies (poly executor, critic, evaluator, repos, breakers, capital).

**Checkpoint**: FlipAutoExecutionPipeline is fully tested in isolation with mocked deps. Ready for wiring.

---

## Phase 4: User Story 2 — Independent Arbitrage Execution (Priority: P1)

**Goal**: Arb trades execute through a dedicated pipeline with zero `ticket_type` conditionals. Uses ExecutionOrchestrator for two-leg execution as before.

**Independent Test**: Trigger an arb ticket → evaluator checks spread bounds + depth → critic uses arb system prompt → orchestrator executes two-leg → no flippening code paths touched.

### Implementation for User Story 2

- [X] T009 [P] [US2] Create `src/arb_scanner/execution/arb_evaluator.py` with function `evaluate_arb_criteria(opportunity, config, capital, breakers, open_positions)` that returns `(passed: bool, reasons: list[str])`. Extract arb-specific logic from `auto_evaluator.py`: spread bounds (min_spread_pct, max_spread_pct), confidence minimum, category filtering, daily loss limit, max open positions, duplicate detection. Include structlog logging with `pipeline="arb"` context.

- [X] T010 [P] [US2] Create `src/arb_scanner/execution/arb_critic.py` with class `ArbTradeCritic` extracted from `trade_critic.py`. Constructor takes `CriticConfig`. Method `evaluate(ticket, legs, context)` returns `CriticVerdict`. Mechanical flags: stale price, anomalous spread, low poly_depth, low kalshi_depth, price symmetry, title risk terms. Use `CRITIC_SYSTEM_PROMPT` constant directly (no conditional). Call `build_arb_critic_prompt()` from `_critic_prompts.py`. Include timeout tracking and Claude API call logic.

- [X] T011 [US2] Create `src/arb_scanner/execution/arb_pipeline.py` with class `ArbAutoExecutionPipeline`. Constructor takes: `config: Settings`, `auto_config: AutoExecutionConfig`, `orchestrator: ExecutionOrchestrator`, `critic: ArbTradeCritic`, `breakers: CircuitBreakerManager`, `capital: CapitalManager`, `poly: PolymarketExecutor`, `kalshi: KalshiExecutor`, `auto_repo: AutoExecRepository`. Methods: `process_opportunity(opportunity, source)` → evaluates via `evaluate_arb_criteria()`, calls critic, checks slippage via `check_slippage()` (both venues), executes via `orchestrator.execute()` (two-leg atomic), records to `auto_execution_log`. `set_mode()`, `kill()`, `mode` property for control. Build arb-specific market context inline (poly_yes_price, kalshi_yes_price, poly_depth, kalshi_depth — no entry_price/baseline_deviation fields). Use `push_activity()` with `pipeline="arb"` field. Module must be <300 lines.

- [X] T012 [P] [US2] Write `tests/unit/test_arb_evaluator.py` with tests: passes when all criteria met, rejects on spread below min, rejects on spread above max, rejects on low confidence, rejects on blocked category, rejects on daily loss exceeded. Mock `CapitalManager` and `CircuitBreakerManager`.

- [X] T013 [P] [US2] Write `tests/unit/test_arb_critic.py` with tests: returns clean verdict when no flags, raises stale price flag, raises low poly_depth flag, raises low kalshi_depth flag, raises anomalous spread flag, uses `CRITIC_SYSTEM_PROMPT`, calls `build_arb_critic_prompt()`. Mock Anthropic API client.

- [X] T014 [US2] Write `tests/unit/test_arb_pipeline.py` with tests: process_opportunity happy path (evaluate → critic → slippage → orchestrator.execute → log), rejects when evaluator fails, rejects when critic kills, rejects on slippage exceeded, records failure on execution error, records success on execution complete, mode control, kill switch. Mock all dependencies.

**Checkpoint**: ArbAutoExecutionPipeline is fully tested in isolation. Ready for wiring.

---

## Phase 5: User Story 3 — Shared Safety Layer (Priority: P2)

**Goal**: Both pipelines share capital manager, loss limits, and mode control while maintaining independent failure breakers. Wiring phase — connects new pipelines to the live system.

**Independent Test**: Trigger 3 consecutive flip failures → flip breaker trips → arb breaker remains healthy and can still process opportunities. Capital manager daily budget applies across both pipelines.

### Implementation for User Story 3

- [X] T015 [US3] Update `src/arb_scanner/api/app.py` `_init_auto_execution()` to create two `CircuitBreakerManager` instances (arb_breakers, flip_breakers), one `ArbAutoExecutionPipeline` and one `FlipAutoExecutionPipeline`. Both receive the same `CapitalManager` instance. Store on `app.state`: `arb_pipeline`, `flip_pipeline`, `arb_breakers`, `flip_breakers`. Keep `app.state.auto_pipeline` as a reference to `arb_pipeline` for backward compatibility during transition. Wire shared mode control: both pipelines get mode set/killed together. Also store `config._arb_pipeline` and `config._flip_pipeline` sidecar references for CLI and flippening orchestrator access.

- [X] T016 [US3] Update `src/arb_scanner/flippening/_orch_processing.py` `_feed_auto_pipeline()` to access `config._flip_pipeline` instead of `config._auto_pipeline`. The function should call `flip_pipeline.process_opportunity(opp, source="flippening")`. No changes to the opportunity dict structure — it already contains all flip-specific fields.

- [X] T017 [US3] Update `src/arb_scanner/flippening/_orch_exit.py` `_feed_exit_pipeline()` to access `config._flip_pipeline` instead of `config._auto_pipeline` and call `flip_pipeline.process_exit(exit_sig, entry_sig, event)`.

- [X] T018 [US3] Update `src/arb_scanner/cli/orchestrator.py` to access `config._arb_pipeline` instead of `config._auto_pipeline` when feeding arb opportunities. The arb pipeline's `process_opportunity()` accepts the same dict shape as before.

- [X] T019 [US3] Write `tests/integration/test_pipeline_isolation.py` with tests: (1) 3 consecutive flip failures do not trip arb breaker, (2) 3 consecutive arb failures do not trip flip breaker, (3) capital manager daily budget is shared — arb trade consumes budget visible to flip pipeline, (4) mode control propagates to both pipelines, (5) kill switch stops both pipelines. Use mocked executors and repos but real CircuitBreakerManager and CapitalManager instances.

- [X] T020 [US3] Update existing tests that reference `auto_pipeline` or `config._auto_pipeline`: search for all usages in `tests/unit/test_auto_pipeline.py`, `tests/unit/test_auto_pipeline_exit.py`, `tests/unit/test_orch_exit.py`, `tests/unit/test_cli_app.py`, `tests/integration/test_auto_exec_pipeline.py` and update to use the appropriate pipeline (`_arb_pipeline` or `_flip_pipeline`). Ensure all existing tests still pass.

**Checkpoint**: Both pipelines wired into live system with independent breakers and shared capital. Core bug (flip failures tripping arb breaker) is fixed.

---

## Phase 6: User Story 4 — Unified Dashboard View (Priority: P2)

**Goal**: Dashboard shows single consolidated view with explicit pipeline type labels and per-pipeline circuit breaker status.

**Independent Test**: With both pipeline types active, dashboard shows all positions in one table with "Arb"/"Flip" labels, and circuit breakers display independently per pipeline.

### Implementation for User Story 4

- [X] T021 [P] [US4] Update `src/arb_scanner/api/routes_auto_execution.py`: (1) `/status` endpoint returns per-pipeline breaker state — access `request.app.state.arb_breakers` and `request.app.state.flip_breakers` and return separate `arb_breakers` and `flip_breakers` arrays in response. (2) `/positions` endpoint adds `"pipeline_type": "arb"` to arb position dicts and `"pipeline_type": "flip"` to flip position dicts. (3) `/enable` and `/disable` endpoints set mode on both pipelines.

- [X] T022 [P] [US4] Update `src/arb_scanner/execution/activity_feed.py` `push_activity()` to accept and include a `pipeline: str` field in event dicts. Default to `"unknown"` for backward compatibility. Update all `push_activity()` call sites in both pipeline modules to pass `pipeline="arb"` or `pipeline="flip"`.

- [X] T023 [P] [US4] Update `src/arb_scanner/notifications/auto_exec_webhook.py` `dispatch_auto_exec_alert()` to include pipeline label in notification message. Read `entry.source` field to determine pipeline type — "flippening" source maps to "Flip" label, all others map to "Arb". Add pipeline prefix to notification title.

- [X] T024 [US4] Update `src/arb_scanner/api/static/index.html`: (1) Add "Type" column to Open Positions table header (between Market and Side). (2) Update circuit breaker status section to show separate Arb and Flip failure breaker indicators instead of a single failure breaker.

- [X] T025 [US4] Update `src/arb_scanner/api/static/app.js`: (1) `refreshOpenPositions()` — use `p.pipeline_type` field instead of field-presence heuristics (`p.market_id ? 'flip' : 'arb'`). Add Type cell to table row. (2) `refreshAutoExecStatus()` — display arb and flip breakers separately. (3) `closePosition()` — fix bug: arb positions should NOT call `/api/execution/flip-exit/`. For now, disable close button for arb positions (arb positions are atomic and don't have manual close). Only flip positions get Close button. (4) Update SSE event handler to show pipeline label in activity feed.

- [X] T026 [US4] Update `tests/unit/test_execution_routes.py` to verify `/status` returns per-pipeline breaker state and `/positions` returns `pipeline_type` field on each position dict.

**Checkpoint**: Dashboard displays unified view with explicit pipeline labels and independent breaker status.

---

## Phase 7: User Story 5 — Pipeline-Specific Evaluation Criteria (Priority: P3)

**Goal**: Verify that each pipeline's evaluator and critic use only their type-specific fields and prompts, with no cross-contamination.

**Independent Test**: Flip evaluator never checks spread bounds. Arb critic never skips depth checks. No `ticket_type` string appears anywhere in execution modules.

### Implementation for User Story 5

- [X] T027 [US5] Add verification tests to `tests/unit/test_flip_evaluator.py`: confirm that passing `spread_pct` values outside arb bounds (e.g., spread=0.90) does NOT cause rejection — flip evaluator has no spread bounds at all. Add test that `ticket_type` string literal does not appear in `src/arb_scanner/execution/flip_evaluator.py` source.

- [X] T028 [US5] Add verification tests to `tests/unit/test_arb_evaluator.py`: confirm that spread bounds ARE enforced. Add test that `ticket_type` string literal does not appear in `src/arb_scanner/execution/arb_evaluator.py` source.

- [X] T029 [US5] Add verification tests to `tests/unit/test_flip_critic.py`: confirm mechanical flags do NOT include `low_depth_poly_depth` or `low_depth_kalshi_depth`. Confirm system prompt is `FLIPPENING_CRITIC_SYSTEM_PROMPT`. Add source scan test.

- [X] T030 [US5] Add verification tests to `tests/unit/test_arb_critic.py`: confirm mechanical flags DO include depth checks. Confirm system prompt is `CRITIC_SYSTEM_PROMPT`. Add source scan test.

**Checkpoint**: Pipeline-specific evaluation verified. No cross-contamination between arb and flip criteria.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Remove old modules, verify zero conditional branches, run quality gates

- [X] T031 Delete `src/arb_scanner/execution/auto_pipeline.py` and remove all imports referencing `AutoExecutionPipeline` across the codebase. Search: `grep -r "auto_pipeline" src/` and `grep -r "AutoExecutionPipeline" src/` — update or remove every hit.
- [X] T032 Delete `src/arb_scanner/execution/auto_evaluator.py` and remove all imports referencing `evaluate_criteria`. Search: `grep -r "auto_evaluator" src/` and `grep -r "evaluate_criteria" src/` — update or remove every hit.
- [X] T033 Delete `src/arb_scanner/execution/trade_critic.py` and remove all imports referencing `TradeCritic`. Search: `grep -r "trade_critic" src/` and `grep -r "TradeCritic" src/` — update or remove every hit.
- [X] T034 [P] Delete old test files: `tests/unit/test_auto_pipeline.py`, `tests/unit/test_auto_evaluator.py`, `tests/unit/test_trade_critic.py`. Verify no other test files import from deleted modules.
- [X] T035 Verify zero `ticket_type` conditionals: run `grep -r "ticket_type" src/arb_scanner/execution/` and confirm zero matches. If any remain, refactor them out. The string `ticket_type` may appear in dict keys passed through (e.g., opportunity dicts) but must NOT appear in conditional logic (`if.*ticket_type`).
- [X] T036 Run all quality gates: `uv run ruff check src/ tests/` (zero errors), `uv run ruff format --check src/ tests/` (clean), `uv run mypy src/ --strict` (zero errors), `uv run pytest tests/ -x --tb=short` (all pass), `uv run pytest tests/ --cov=src/arb_scanner --cov-fail-under=70` (≥70% coverage). Fix any failures.
- [X] T037 Verify module size constraints: no new module exceeds 300 lines, no function exceeds 50 lines. Run: `wc -l src/arb_scanner/execution/arb_pipeline.py src/arb_scanner/execution/flip_pipeline.py src/arb_scanner/execution/arb_evaluator.py src/arb_scanner/execution/flip_evaluator.py src/arb_scanner/execution/arb_critic.py src/arb_scanner/execution/flip_critic.py`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion
- **US1 Flip Pipeline (Phase 3)**: Depends on Phase 1 + Phase 2
- **US2 Arb Pipeline (Phase 4)**: Depends on Phase 1 + Phase 2
- **US3 Shared Safety (Phase 5)**: Depends on Phase 3 AND Phase 4 (needs both pipelines)
- **US4 Dashboard (Phase 6)**: Depends on Phase 5 (needs wired pipelines)
- **US5 Evaluation Criteria (Phase 7)**: Depends on Phase 3 AND Phase 4
- **Polish (Phase 8)**: Depends on ALL previous phases

### User Story Dependencies

- **US1 (P1)** + **US2 (P1)**: Independent — can run in parallel after Foundational
- **US3 (P2)**: Depends on US1 + US2 (wiring phase needs both pipelines created)
- **US4 (P2)**: Depends on US3 (dashboard shows per-pipeline breakers from wired system)
- **US5 (P3)**: Depends on US1 + US2 (verifies evaluator/critic isolation)

### Within Each User Story

- Evaluator + Critic [P] can be created in parallel
- Pipeline depends on evaluator + critic
- Tests [P] for evaluator + critic can run in parallel
- Pipeline tests depend on pipeline creation

### Parallel Opportunities

```
Phase 1 (Setup)
    ↓
Phase 2 (Foundational)
    ↓
┌─── Phase 3 (US1: Flip) ───┐
│  T003 [P] flip_evaluator   │
│  T004 [P] flip_critic      │    ← Parallel with Phase 4
│  T005     flip_pipeline     │
│  T006-T008 tests            │
└─────────────────────────────┘
                                ┌─── Phase 4 (US2: Arb) ───┐
                                │  T009 [P] arb_evaluator   │
                                │  T010 [P] arb_critic      │
                                │  T011     arb_pipeline     │
                                │  T012-T014 tests           │
                                └───────────────────────────┘
    ↓ (both complete)
Phase 5 (US3: Wiring + Safety)
    ↓
┌─── Phase 6 (US4: Dashboard) ──┐  ┌─── Phase 7 (US5: Verification) ──┐
│  T021 [P] routes               │  │  T027-T030 verification tests    │
│  T022 [P] activity_feed        │  └──────────────────────────────────┘
│  T023 [P] webhook              │
│  T024-T025 frontend            │
└────────────────────────────────┘
    ↓ (all complete)
Phase 8 (Polish & Cleanup)
```

---

## Parallel Example: User Story 1

```bash
# Launch evaluator + critic in parallel (different files):
Task: T003 "Create flip_evaluator.py"
Task: T004 "Create flip_critic.py"

# Then pipeline (depends on both):
Task: T005 "Create flip_pipeline.py"

# Launch all tests in parallel:
Task: T006 "Write test_flip_evaluator.py"
Task: T007 "Write test_flip_critic.py"
Task: T008 "Write test_flip_pipeline.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (_critic_prompts.py split)
2. Complete Phase 2: Foundational (config override support)
3. Complete Phase 3: User Story 1 (FlipAutoExecutionPipeline)
4. **STOP and VALIDATE**: All flip pipeline unit tests pass independently
5. This alone fixes the root-cause bug when wired in Phase 5

### Incremental Delivery

1. Setup + Foundational → prompt split + config ready
2. US1 (Flip Pipeline) → test independently → flip execution works in isolation
3. US2 (Arb Pipeline) → test independently → arb execution works in isolation
4. US3 (Wiring) → connect both pipelines → **root-cause bug fixed live**
5. US4 (Dashboard) → unified view with per-pipeline labels
6. US5 (Verification) → confirm no cross-contamination
7. Polish → delete old code, run quality gates

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- US1 and US2 are both P1 and can be developed in parallel
- US3 is the critical switchover — this is where the live bug gets fixed
- Old modules (auto_pipeline, auto_evaluator, trade_critic) stay alive until Phase 8 cleanup
- Each pipeline must be <300 lines; each function <50 lines
- Commit after each task or logical group
