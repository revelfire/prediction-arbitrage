# Data Model: Split Execution Paths

**Feature**: 022-split-execution-paths
**Date**: 2026-03-04

## Entities

### ArbAutoExecutionPipeline

Self-contained pipeline for arbitrage trade execution.

**Dependencies (injected)**:
- `AutoExecutionConfig` — criteria thresholds, sizing, breaker config
- `ExecutionOrchestrator` — two-leg ticket execution (existing, unchanged)
- `ArbTradeCritic` — arb-specific LLM risk evaluation
- `CircuitBreakerManager` — arb-specific failure/anomaly breakers
- `CapitalManager` — shared global capital/loss tracking
- `AutoExecRepository` — arb execution log + position persistence
- `PolymarketExecutor` — venue executor (for slippage checks)
- `KalshiExecutor` — venue executor (for slippage checks)

**State**:
- `mode`: off | manual | auto
- `killed`: bool (emergency stop)
- `cooldowns`: dict of recently rejected arb_ids with TTL

**Behavior**:
- `process_opportunity(opportunity, source)` → `AutoExecLogEntry | None`
- Evaluates arb criteria (spread bounds, confidence, liquidity)
- Calls `ArbTradeCritic.evaluate()` with two-venue context
- Checks slippage via `check_slippage()` (both venues)
- Executes via `ExecutionOrchestrator.execute()` (two-leg atomic)
- Records to `auto_execution_log`, updates `auto_execution_positions`

### FlipAutoExecutionPipeline

Self-contained pipeline for flippening (mean reversion) trade execution.

**Dependencies (injected)**:
- `AutoExecutionConfig` — criteria thresholds, sizing, breaker config
- `PolymarketExecutor` — single-leg order placement (direct, no orchestrator)
- `FlipTradeCritic` — flip-specific LLM risk evaluation
- `CircuitBreakerManager` — flip-specific failure/anomaly breakers
- `CapitalManager` — shared global capital/loss tracking
- `FlipPositionRepo` — flip position tracking (flippening_auto_positions)
- `AutoExecRepository` — execution log persistence (shared log table)
- `ExecutionRepository` — order audit trail (execution_orders)

**State**:
- `mode`: off | manual | auto
- `killed`: bool (emergency stop)
- `cooldowns`: dict of recently rejected arb_ids with TTL
- `exit_executor`: FlipExitExecutor (for exit signal handling)

**Behavior**:
- `process_opportunity(opportunity, source)` → `AutoExecLogEntry | None`
- Evaluates flip criteria (confidence, category, daily loss — NOT spread bounds)
- Calls `FlipTradeCritic.evaluate()` with single-venue context
- Places single-leg order via `PolymarketExecutor.place_order()` directly
- Registers position in `flippening_auto_positions`
- `process_exit(exit_sig, entry_sig, event)` → handles exit signals
- Records to `auto_execution_log`

### ArbTradeCritic

Arbitrage-specific LLM risk evaluator.

**Fields**:
- `config`: CriticConfig
- `client`: Anthropic API client
- `timeout_count`: consecutive timeout tracker

**Behavior**:
- `evaluate(ticket, legs, context)` → `CriticVerdict`
- Mechanical flags: stale price, anomalous spread, low poly/kalshi depth, price symmetry, title risk terms
- System prompt: `CRITIC_SYSTEM_PROMPT` (two-venue arbitrage focus)
- User prompt: Arb template (spread, poly price, kalshi price, book depths)

### FlipTradeCritic

Flippening-specific LLM risk evaluator.

**Fields**:
- `config`: CriticConfig
- `client`: Anthropic API client
- `timeout_count`: consecutive timeout tracker

**Behavior**:
- `evaluate(ticket, context)` → `CriticVerdict`
- Mechanical flags: stale price, anomalous deviation, price symmetry, title risk terms (NO venue depth checks)
- System prompt: `FLIPPENING_CRITIC_SYSTEM_PROMPT` (single-venue mean reversion focus)
- User prompt: Flip template (entry price, side, baseline deviation, market ID)

### CircuitBreakerManager (existing, per-pipeline instances)

No changes to class internals. Two instances created at startup.

**Instance 1**: Arb breakers (arb failure count, arb anomaly state)
**Instance 2**: Flip breakers (flip failure count, flip anomaly state)

**Shared concern**: Loss breaker uses `CapitalManager.daily_pnl` which is global. Both pipeline instances call `check_loss()` against the same daily P&L.

### CapitalManager (existing, single shared instance)

No changes. Both pipelines inject the same instance for:
- `suggest_size()`, `check_venue_reserve()`, `check_exposure()`
- `check_daily_pnl()`, `check_cooldown()`, `check_concentration()`
- `record_fill()`, `close_position()`

## Relationships

```
                    ┌─────────────────────┐
                    │   CapitalManager    │ (shared singleton)
                    │   - daily_pnl       │
                    │   - open_positions   │
                    │   - venue_balances   │
                    └──────┬──────┬───────┘
                           │      │
              ┌────────────┘      └────────────┐
              ▼                                 ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│  ArbAutoExecutionPipeline│    │ FlipAutoExecutionPipeline│
│  - ArbTradeCritic        │    │ - FlipTradeCritic        │
│  - CircuitBreakerManager │    │ - CircuitBreakerManager  │
│    (arb instance)        │    │   (flip instance)        │
│  - ExecutionOrchestrator │    │ - PolymarketExecutor     │
│  - AutoExecRepository    │    │ - FlipPositionRepo       │
└──────────┬───────────────┘    │ - FlipExitExecutor       │
           │                    └──────────┬───────────────┘
           │                               │
           ▼                               ▼
┌─────────────────┐            ┌────────────────────┐
│execution_tickets│            │flippening_auto_    │
│auto_exec_log    │            │  positions         │
│auto_exec_       │            │auto_exec_log       │
│  positions      │            │                    │
└─────────────────┘            └────────────────────┘
```

## State Transitions

### Pipeline Mode (shared across both)
```
off → manual → auto → off
         ↗         ↘
     (operator sets mode via dashboard)
```

### Arb Trade Lifecycle
```
opportunity → evaluate → critic → slippage → execute (2-leg) → complete/failed
                                                                    ↓
                                                              log + position
```

### Flip Trade Lifecycle
```
opportunity → evaluate → critic → execute (1-leg) → register position
                                                          ↓
                                                    [hold period]
                                                          ↓
                                               exit signal → sell → close position
```
