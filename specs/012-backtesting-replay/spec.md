# Spec: 012 — Backtesting & Historical Replay

**Feature**: `012-backtesting-replay` | **Date**: 2026-02-26

## Problem Statement

The flippening engine generates entry/exit signals for live sports markets, but there is no way to evaluate whether the configured thresholds and confidence weights are optimal. The spike detector, signal generator, and confidence scorer are tuned by hand with no empirical feedback loop. Raw price ticks are not persisted, so historical replay is impossible — once a game ends, the tick-level data is lost forever.

## Goals

1. **Capture**: Persist every `PriceUpdate` that flows through the engine so historical replay becomes possible.
2. **Replay**: Re-run the spike detector and signal generator against stored ticks with arbitrary config overrides, producing hypothetical signals.
3. **Evaluate**: Compute per-sport win rates, average P&L, optimal thresholds, and confidence weight sensitivity from replay results.
4. **CLI**: Expose replay and evaluation as CLI commands for iterative tuning.

## Non-Goals

- Kelly Criterion position sizing (deferred to post-012, depends on stable win rate data).
- Live A/B testing of config variants during production runs.
- UI dashboard for replay results (CLI + JSON output sufficient for now).
- Replaying arb scanner data (this is flippening-only).

---

## Functional Requirements

### Tick Capture

**FR-001**: The system MUST persist every `PriceUpdate` processed by the orchestrator to a `flippening_price_ticks` table. Fields: `market_id`, `token_id`, `yes_bid`, `yes_ask`, `no_bid`, `no_ask`, `timestamp`, `synthetic_spread`, `book_depth_bids`, `book_depth_asks`.

**FR-002**: Tick persistence MUST be non-blocking — failures MUST NOT disrupt the live engine. Use batch inserts (buffer up to N ticks, flush on interval or buffer full).

**FR-003**: Tick persistence MUST be skippable via `dry_run` mode and a new `capture_ticks: bool` config flag (default `true`).

**FR-004**: The system MUST persist baseline drift updates (when `GameManager._update_drift()` fires) to a `flippening_baseline_drifts` table: `market_id`, `old_yes`, `new_yes`, `drift_reason`, `drifted_at`.

### Replay Engine

**FR-005**: The system MUST provide a `ReplayEngine` class that accepts a market_id + time range, loads ticks from the DB, and replays them through `SpikeDetector` and `SignalGenerator` with a given `FlippeningConfig`.

**FR-006**: `ReplayEngine` MUST reconstruct `GameState` from the stored baseline row, then feed ticks in timestamp order through `SpikeDetector.check_spike()` and `SignalGenerator.check_exit()`.

**FR-007**: `ReplayEngine` MUST apply baseline drift records from `flippening_baseline_drifts` at the correct timestamps during replay.

**FR-008**: Replay results MUST be returned as a list of `ReplaySignal` objects containing: entry price, exit price, exit reason, P&L, hold minutes, confidence at entry, config used.

**FR-009**: `ReplayEngine` MUST support config overrides — the caller provides a partial `FlippeningConfig` that overrides specific fields (e.g. `spike_threshold_pct=0.12`) while inheriting defaults from the stored config.

### Evaluation & Reporting

**FR-010**: The system MUST provide an `evaluate_replay()` function that takes replay results and computes: total signals, win count (reversion exits), win rate, avg P&L, avg hold minutes, max drawdown, profit factor (gross wins / gross losses).

**FR-011**: The system MUST provide a `sweep_parameter()` function that runs replays across a range of values for a single config parameter (e.g. `spike_threshold_pct` from 0.08 to 0.25 in 0.01 steps), returning evaluation metrics for each value.

### CLI Commands

**FR-012**: `flip-replay` CLI command: `--market-id`, `--sport`, `--since`/`--until` (ISO 8601), `--override` (key=value pairs), `--format table|json`. Runs replay and prints signal list.

**FR-013**: `flip-evaluate` CLI command: `--sport`, `--since`/`--until`, `--format table|json`. Aggregates replay results across all markets for a sport.

**FR-014**: `flip-sweep` CLI command: `--param` (config field name), `--min`, `--max`, `--step`, `--sport`, `--since`/`--until`, `--format table|json`. Runs parameter sweep and prints evaluation grid.

### Data Retention

**FR-015**: Tick data MUST support configurable retention via `tick_retention_days: int` config field (default 90). A `flip-tick-prune` CLI command deletes ticks older than the retention window.

---

## Success Criteria

**SC-001**: After 24 hours of live `flip-watch`, the `flippening_price_ticks` table contains ticks for every market that produced a `PriceUpdate`, with < 0.1% drop rate vs telemetry `cum_parsed_ok`.

**SC-002**: `flip-replay --market-id X` reproduces the same entry/exit signals as the live run (same side, same exit reason) when using the same config. Minor price differences (< 0.005) acceptable due to timestamp precision.

**SC-003**: `flip-sweep --param spike_threshold_pct --min 0.08 --max 0.25 --step 0.01 --sport nba` completes and returns a grid of win_rate / avg_pnl per threshold value.

**SC-004**: Tick capture adds < 5ms p95 latency to the per-update processing path (batch insert, not per-tick).

**SC-005**: `flip-tick-prune` deletes ticks older than retention window without locking the table for > 1 second.

---

## Edge Cases

**EC-001**: Market with zero ticks in the requested time range — replay returns empty result, no error.

**EC-002**: Baseline row missing for a market — replay logs a warning and skips that market.

**EC-003**: Drift records out of order or with timestamps before baseline capture — sort by timestamp, skip any before baseline.

**EC-004**: Config override with invalid values (e.g. negative threshold) — validate via `FlippeningConfig` model, raise clear error.

**EC-005**: Tick buffer flush fails (DB unavailable) — log warning, drop buffer, continue live processing. Do NOT retry or block.

**EC-006**: Sweep with zero ticks for a sport — return empty grid with a note, no crash.

**EC-007**: Very large tick datasets (millions of rows) — replay must stream ticks via cursor, not load all into memory.
