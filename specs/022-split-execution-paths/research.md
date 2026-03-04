# Research: Split Execution Paths

**Feature**: 022-split-execution-paths
**Date**: 2026-03-04

## R-001: Current Conditional Branches (ticket_type) Inventory

**Decision**: All 8 `ticket_type` conditional branches across 4 modules must be eliminated via pipeline separation.

**Inventory**:

| Location | Line(s) | Conditional | Purpose |
|----------|---------|-------------|---------|
| `auto_pipeline.py` | 216 | `opportunity.get("ticket_type", "arbitrage")` | Type detection for logging |
| `auto_pipeline.py` | 396-400 | `ticket_type == "flippening"` | Position registration (flip-only) |
| `auto_pipeline.py` | 545-573 | `if ticket_type == "flippening"` | Market context building (entirely different dicts) |
| `auto_evaluator.py` | 40 | `ticket_type = opportunity.get("ticket_type", "arbitrage")` | Type extraction |
| `auto_evaluator.py` | 44-48 | `if ticket_type != "flippening"` | Spread bounds (arb-only) |
| `trade_critic.py` | 123-131 | `if ticket_type != "flippening"` | Book depth check (arb-only) |
| `trade_critic.py` | 173-177 | `if ticket_type == "flippening"` | System prompt selection |
| `_critic_prompts.py` | 87-109 | `if ticket_type == "flippening"` | User prompt building |

**Rationale**: Each conditional represents a fork where the two pipelines have fundamentally different logic. Splitting into separate modules eliminates all branches.

**Alternatives considered**:
- Strategy pattern (inject strategy object per type) — rejected because the split is so fundamental that separate modules are cleaner than a strategy interface
- Keep conditionals but add logging — rejected because it doesn't fix the architectural coupling

## R-002: Pipeline Architecture Decision

**Decision**: Two independent pipeline classes (`ArbAutoExecutionPipeline`, `FlipAutoExecutionPipeline`) sharing injected safety components, no base class.

**Rationale**: The two pipelines differ in:
1. **Execution model**: Arb = two-leg atomic via `ExecutionOrchestrator.execute()`; Flip = single-leg via `PolymarketExecutor.place_order()` directly
2. **Context structure**: Completely different dict shapes (arb has poly/kalshi prices+depth; flip has entry_price, side, baseline_deviation)
3. **Position tracking**: Different tables, different schemas, different lifecycle
4. **Exit handling**: Arb has no exit concept (atomic spread capture); Flip has time-based exit with `FlipExitExecutor`
5. **Evaluation criteria**: Arb checks spread bounds; Flip skips them (deviation IS the signal)

**Alternatives considered**:
- Base class with abstract methods — rejected because there's almost no shared logic to inherit. The two pipelines share _dependencies_ (capital, breakers) but not _behavior_.
- Single pipeline with dispatch table — rejected because it's essentially the current design with conditionals hidden behind method references.

## R-003: Circuit Breaker Separation

**Decision**: Per-pipeline failure breakers + shared loss breaker and anomaly breaker.

**Current state**: Single `CircuitBreakerManager` with 3 breakers (loss, failure, anomaly). The failure breaker's `record_failure()`/`record_success()` is called on every trade outcome.

**Approach**:
- Create two `CircuitBreakerManager` instances: one for arb, one for flip
- Each has its own failure breaker state (independent consecutive failure tracking)
- Loss breaker shared: Both pipelines call `check_loss(daily_pnl)` on the same `CapitalManager.daily_pnl` which is already global
- Anomaly breaker: per-pipeline (arb anomalous spread != flip anomalous deviation)

**Rationale**: The root-cause bug was flip failures tripping the shared failure breaker, blocking arb trades. Per-pipeline failure breakers directly fix this.

**Alternatives considered**:
- Single shared breaker with per-type counters — rejected because it still creates coupling (breaker state struct grows per type)
- Completely separate safety stacks — rejected because capital/loss limits must be global

## R-004: Orchestrator Routing

**Decision**: Flip pipeline bypasses `ExecutionOrchestrator.execute()` entirely. Uses `PolymarketExecutor.place_order()` directly.

**Root cause analysis**: `ExecutionOrchestrator.execute()` (line 200) calls `ticket_repo.get_ticket(arb_id)` — flippening events don't have tickets, so it returns `_failed_result()` silently. The orchestrator is fundamentally designed for two-leg arb tickets.

**Approach**:
- Arb pipeline continues using `ExecutionOrchestrator.execute()` (unchanged)
- Flip pipeline directly calls `PolymarketExecutor.place_order()` for entry, mirroring what `FlipExitExecutor` already does for exits
- Both record orders to `execution_orders` table for audit trail

**Rationale**: The flip entry path only needs to place a single Polymarket order. Routing through the two-leg orchestrator was always a misfit.

**Alternatives considered**:
- Add flip support to `ExecutionOrchestrator` — rejected because it would add more conditionals to an already complex module
- Create `FlipEntryExecutor` — considered viable but may be over-abstraction for a single `place_order()` call. Revisit if entry logic grows.

## R-005: Critic Module Separation

**Decision**: Split into `ArbTradeCritic` and `FlipTradeCritic`, each with hardcoded prompt selection.

**Current state**: `TradeCritic._call_critic()` selects system prompt via `if ticket_type == "flippening"`. `_check_mechanical_flags()` skips depth checks for flip. `build_critic_prompt()` builds entirely different user prompts.

**Approach**:
- `ArbTradeCritic`: Uses `CRITIC_SYSTEM_PROMPT`, checks poly/kalshi depth, builds arb-specific user prompt
- `FlipTradeCritic`: Uses `FLIPPENING_CRITIC_SYSTEM_PROMPT`, skips venue depth, builds flip-specific user prompt
- Shared: Claude API call logic, response parsing, timeout tracking, verdict model

**Rationale**: The two critics have different system prompts, different mechanical flags, and different user prompts. Separating eliminates all branching.

**Alternatives considered**:
- Keep single critic with configurable prompt set — viable but still requires conditional flag checking

## R-006: Dashboard Integration

**Decision**: Keep unified API endpoints that merge data from both pipelines. Add `pipeline_type` field to responses.

**Current state**: `/api/auto-execution/positions` already merges arb + flip positions. Frontend detects type by field presence (`market_id` → flip, `poly_market_id` → arb).

**Approach**:
- Add explicit `pipeline_type: "arb" | "flip"` to all position/log responses
- Add per-pipeline breaker status to `/api/auto-execution/status`
- Activity feed events get `pipeline` field
- Frontend uses `pipeline_type` instead of field-sniffing

**Rationale**: Explicit type labeling is more reliable than field-presence heuristics. No structural dashboard changes needed.

## R-007: Config Model Extension

**Decision**: Add optional per-pipeline config overrides to `AutoExecutionConfig`.

**Current state**: Single flat config applies to both pipelines. No way to express different thresholds per pipeline.

**Approach**:
- Add `arb_overrides` and `flip_overrides` optional dicts to `AutoExecutionConfig`
- Each pipeline reads base config, then overlays its overrides
- Keeps backward compatibility (overrides default to empty)

**Rationale**: Different trade types warrant different risk parameters (e.g., flip may allow higher max_spread_pct since deviation IS the signal).

## R-008: Existing Bug — closePosition() Frontend

**Decision**: Fix the frontend `closePosition()` function to use correct endpoints per type.

**Current state**: Both arb and flip positions call `/api/execution/flip-exit/{arbId}` — arb close will always fail because `flip_position_repo` won't find arb positions.

**Fix**: Route arb positions to a dedicated arb close endpoint (TBD, may need new endpoint or use existing execution cancel).
