# Quickstart: Split Execution Paths

## Overview

This feature splits the monolithic `AutoExecutionPipeline` into two independent pipelines:
- `ArbAutoExecutionPipeline` — two-leg cross-venue arbitrage execution
- `FlipAutoExecutionPipeline` — single-leg Polymarket mean reversion execution

Both share: `CapitalManager`, mode control, daily loss limits.
Each owns: its own failure breaker, evaluator, critic, and execution path.

## Key Files to Create

| File | Purpose |
|------|---------|
| `src/arb_scanner/execution/arb_pipeline.py` | Arb-specific auto-execution pipeline |
| `src/arb_scanner/execution/flip_pipeline.py` | Flip-specific auto-execution pipeline |
| `src/arb_scanner/execution/arb_evaluator.py` | Arb-specific criteria evaluation |
| `src/arb_scanner/execution/flip_evaluator.py` | Flip-specific criteria evaluation |
| `src/arb_scanner/execution/arb_critic.py` | Arb-specific LLM trade critic |
| `src/arb_scanner/execution/flip_critic.py` | Flip-specific LLM trade critic |

## Key Files to Modify

| File | Change |
|------|--------|
| `src/arb_scanner/api/app.py` | Create two pipeline instances, wire both to app.state |
| `src/arb_scanner/api/routes_auto_execution.py` | Add per-pipeline breaker status |
| `src/arb_scanner/api/static/app.js` | Use `pipeline_type` field instead of field-sniffing |
| `src/arb_scanner/flippening/_orch_processing.py` | Feed `FlipAutoExecutionPipeline` instead of generic pipeline |
| `src/arb_scanner/cli/orchestrator.py` | Feed `ArbAutoExecutionPipeline` instead of generic pipeline |

## Key Files to Delete/Deprecate

| File | Action |
|------|--------|
| `src/arb_scanner/execution/auto_pipeline.py` | Delete after split complete |
| `src/arb_scanner/execution/auto_evaluator.py` | Delete (replaced by arb_evaluator + flip_evaluator) |

## Verification

After implementation, verify:
1. `grep -r "ticket_type" src/arb_scanner/execution/` returns zero matches
2. `uv run pytest tests/ -x` — all tests pass
3. Start dashboard, trigger both arb and flip opportunities, confirm independent breaker behavior
