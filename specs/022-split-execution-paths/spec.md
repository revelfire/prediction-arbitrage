# Feature Specification: Split Execution Paths

**Feature Branch**: `022-split-execution-paths`
**Created**: 2026-03-04
**Status**: Draft
**Input**: User description: "Separate flippening and arbitrage execution paths into distinct, clean code paths while maintaining a unified dashboard UI."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Independent Flippening Execution (Priority: P1)

A flippening trade signal is detected and executed end-to-end through its own dedicated pipeline without touching any arbitrage-specific code paths. The flippening pipeline evaluates the opportunity using flippening-specific criteria (spike magnitude, reversion probability, category config), applies its own circuit breakers, and places single-leg orders on the appropriate venue—all without requiring an execution ticket in the arb ticket table.

**Why this priority**: This directly fixes the root-cause bug where flippening trades trip the arb failure breaker because they lack execution tickets. Every flippening trade currently fails silently in the shared orchestrator, then cascades into breaker trips that block all execution.

**Independent Test**: Can be fully tested by triggering a flippening signal with auto-execution enabled and verifying the trade executes successfully without writing to or reading from the execution_tickets table, and without affecting the arb failure breaker state.

**Acceptance Scenarios**:

1. **Given** a flippening spike signal with auto-execution enabled, **When** the flippening pipeline processes the signal, **Then** the trade executes through the flippening-specific path without querying the execution_tickets table.
2. **Given** 3 consecutive flippening trade failures, **When** the arb pipeline later detects an arbitrage opportunity, **Then** the arb failure breaker is unaffected and the arb trade proceeds normally.
3. **Given** a flippening trade that fails due to insufficient liquidity, **When** the failure is recorded, **Then** only the flippening failure breaker increments—not the shared/arb breaker.

---

### User Story 2 - Independent Arbitrage Execution (Priority: P1)

An arbitrage opportunity is detected and executed through its own dedicated pipeline. The arb pipeline evaluates two-leg ticket structures (Polymarket + Kalshi), applies arb-specific critic prompts and evaluation criteria, and manages its own circuit breakers—completely independent of flippening state.

**Why this priority**: Equal to P1 because both pipelines must be independent for the system to function correctly. The arb pipeline is the original path and must continue working with its ticket-based flow without flippening conditionals polluting the logic.

**Independent Test**: Can be fully tested by triggering an arb ticket with auto-execution enabled and verifying the two-leg execution completes without any `ticket_type == "flippening"` conditional branches in the execution path.

**Acceptance Scenarios**:

1. **Given** an arbitrage ticket with auto-execution enabled, **When** the arb pipeline processes the ticket, **Then** the execution uses arb-specific evaluation criteria and critic prompts with no flippening conditional branches.
2. **Given** 3 consecutive arb trade failures, **When** a flippening signal arrives, **Then** the flippening pipeline is unaffected and processes normally.

---

### User Story 3 - Shared Safety Layer (Priority: P2)

Both pipelines share common safety controls: capital management (per-trade and daily limits), loss breaker (cumulative loss threshold), and mode control (enabled/paused/disabled). These shared controls ensure global risk management regardless of which pipeline originated the trade.

**Why this priority**: Safety controls must be consistent across both pipelines to prevent over-exposure, but the refactoring of individual pipelines (P1) is prerequisite.

**Independent Test**: Can be tested by verifying that a trade from either pipeline correctly checks and updates the shared capital manager and loss breaker state.

**Acceptance Scenarios**:

1. **Given** the daily capital limit is 90% consumed by arb trades, **When** a flippening signal requests the remaining 15% of daily capital, **Then** the capital manager rejects the flippening trade due to insufficient daily budget.
2. **Given** cumulative losses from flippening trades exceed the loss threshold, **When** the loss breaker trips, **Then** both arb and flippening pipelines are halted.
3. **Given** an operator pauses auto-execution via the dashboard, **When** either pipeline receives a new signal, **Then** both pipelines respect the paused state.

---

### User Story 4 - Unified Dashboard View (Priority: P2)

The dashboard displays a single consolidated view of all execution activity regardless of pipeline origin. Open positions, recent trades, circuit breaker status, and performance stats merge arb and flippening data into unified tables and charts. The user can distinguish trade types via a visible label but does not need separate tabs or views.

**Why this priority**: The operator needs a single pane of glass to monitor all execution activity. This is a UI concern that doesn't affect pipeline correctness.

**Independent Test**: Can be tested by having both arb and flippening positions open simultaneously and verifying the dashboard shows all positions in a single table with type indicators.

**Acceptance Scenarios**:

1. **Given** 2 open arb positions and 3 open flippening positions, **When** the dashboard loads, **Then** all 5 positions appear in the Open Positions table with a "Type" indicator distinguishing them.
2. **Given** the arb failure breaker is tripped but the flippening breaker is healthy, **When** the dashboard displays circuit breaker status, **Then** each pipeline's breaker status is shown independently.
3. **Given** recent trades from both pipelines, **When** the activity feed loads, **Then** trades are shown in chronological order with pipeline origin labels.

---

### User Story 5 - Pipeline-Specific Evaluation Criteria (Priority: P3)

Each pipeline uses evaluation criteria tailored to its trade type. Flippening trades are evaluated on spike magnitude, reversion probability, and category-specific thresholds. Arbitrage trades are evaluated on spread size, fee-adjusted profit, and cross-venue execution risk. The trade critic uses pipeline-specific prompts rather than branching on ticket_type.

**Why this priority**: Correct evaluation is important but the existing criteria largely work—the main issue is the conditional branching that selects them, not the criteria themselves.

**Independent Test**: Can be tested by verifying that a flippening evaluation never references arb-specific fields (spread, two-leg structure) and vice versa.

**Acceptance Scenarios**:

1. **Given** a flippening opportunity, **When** the evaluator scores it, **Then** the evaluation uses spike magnitude, reversion probability, and category config—not spread or fee-adjusted profit.
2. **Given** an arbitrage opportunity, **When** the critic reviews it, **Then** the critic prompt references two-leg execution risk and venue spread—not spike detection or reversion.

---

### Edge Cases

- What happens when both pipelines attempt to reserve capital simultaneously? The shared capital manager must handle concurrent requests safely.
- What happens when a flippening position exists on a market that also has an open arb ticket? Each pipeline manages its own positions independently.
- What happens when an operator manually closes a position from the dashboard? The close request must route to the correct pipeline's exit logic based on position type.
- What happens during the migration period when old-format positions exist in the database? The system must handle legacy positions that predate the pipeline split.
- What happens when the config specifies different failure breaker thresholds per pipeline? Each pipeline's breaker operates with its own configured thresholds.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a distinct execution pipeline for flippening trades that does not read from or write to the execution_tickets table.
- **FR-002**: System MUST provide a distinct execution pipeline for arbitrage trades that does not contain any `ticket_type` conditional branches.
- **FR-003**: Each pipeline MUST maintain its own failure breaker that tracks consecutive failures independently.
- **FR-004**: Both pipelines MUST share a single capital manager that enforces per-trade limits and daily budget across all trade types.
- **FR-005**: Both pipelines MUST share a single loss breaker that tracks cumulative losses across all trade types.
- **FR-006**: Both pipelines MUST respect the shared mode control (enabled/paused/disabled) state.
- **FR-007**: The flippening pipeline MUST use flippening-specific evaluation criteria (spike magnitude, reversion probability, category thresholds) without referencing arb-specific fields.
- **FR-008**: The arbitrage pipeline MUST use arb-specific evaluation criteria (spread size, fee-adjusted profit, cross-venue risk) without referencing flippening-specific fields.
- **FR-009**: The flippening pipeline MUST use flippening-specific critic prompts that do not branch on ticket_type.
- **FR-010**: The arbitrage pipeline MUST use arb-specific critic prompts that do not branch on ticket_type.
- **FR-011**: The dashboard MUST display a unified view of open positions from both pipelines in a single table with type indicators.
- **FR-012**: The dashboard MUST display independent circuit breaker status for each pipeline's failure breaker alongside the shared loss breaker.
- **FR-013**: The system MUST log pipeline-specific context (pipeline name, trade type) on every execution event for debugging and audit.

### Key Entities

- **Execution Pipeline**: A self-contained processing path that takes an opportunity (arb or flip), evaluates it, applies safety checks, executes the trade, and records the result. Each pipeline type has its own evaluator, critic, and failure breaker.
- **Safety Layer**: Shared components (capital manager, loss breaker, mode control) that apply global risk limits across all pipelines.
- **Failure Breaker**: Per-pipeline circuit breaker that trips after consecutive failures within that pipeline only. Does not affect other pipelines.
- **Position**: A record of an open or closed trade. Arb positions track two-leg spread entries. Flip positions track single-leg spike reversion entries. Both are visible in the unified dashboard.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Zero `ticket_type` conditional branches exist in any execution module (evaluator, critic, pipeline, orchestrator).
- **SC-002**: Flippening trade failures do not affect the arbitrage failure breaker state, and vice versa—verified by executing 3 consecutive failures in one pipeline and confirming the other pipeline's breaker remains healthy.
- **SC-003**: All existing unit tests continue to pass after the refactor, with no reduction in code coverage below the 70% threshold.
- **SC-004**: The dashboard displays positions from both pipelines in a single unified view within 1 refresh cycle (30 seconds).
- **SC-005**: End-to-end flippening trade execution completes successfully without querying the execution_tickets table—verified by integration test.
- **SC-006**: No single module exceeds 300 lines or single function exceeds 50 lines after the refactor (existing code constraints maintained).

## Assumptions

- The existing `FlipPositionRepo` and `flippening_auto_positions` table will continue to serve as the persistence layer for flippening positions (no schema changes needed beyond what migration 024 already added).
- The existing `execution_tickets` and `auto_execution_positions` tables will continue to serve arbitrage positions.
- The shared capital manager and loss breaker are already correctly implemented and only need to be injected into both pipelines rather than reimplemented.
- The `auto_pipeline.py` module will be replaced by two new pipeline modules rather than further extended with conditionals.
- Existing webhook/notification infrastructure (Slack/Discord) will be reused by both pipelines.

## Scope Boundaries

**In Scope**:
- Splitting `auto_pipeline.py` into two independent pipeline modules
- Splitting evaluator logic into pipeline-specific evaluators
- Splitting critic prompts into pipeline-specific prompt sets
- Creating per-pipeline failure breakers
- Updating dashboard API to show independent breaker status
- Updating dashboard UI to label trade types

**Out of Scope**:
- Changing the flippening signal detection engine (spike detector, signal generator)
- Changing the arbitrage matching or calculation engine
- Adding new trade types beyond arb and flip
- Changing the position database schema (use existing tables as-is)
- Modifying the capital manager or loss breaker internal logic
- Separate dashboard pages/tabs per pipeline (unified view only)
