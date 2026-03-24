# 023 — Trade History & Backtesting

## Overview

Import actual Polymarket trade history (CSV export), calculate portfolio-level P&L, and backtest scanner signals against historical price data. Provides both CLI commands and a dashboard tab for visualizing trade performance, comparing actual trades against what the scanner recommended, and running "what-if" backtests on historical arb and flippening opportunities. This bridges the gap between the existing flippening-only replay system and full portfolio analysis.

## Motivation

The existing backtesting tools (`flip-replay`, `flip-evaluate`, `flip-sweep`) only replay stored price ticks through the flippening spike detector. They answer "how would my spike detection parameters have performed?" but cannot answer:

- **"How am I actually doing?"** — No way to import real trade data and compute realized P&L.
- **"Should I have taken that signal?"** — No comparison between scanner recommendations and actual trade outcomes.
- **"What would have happened if...?"** — No way to backtest arb scanner signals against historical cross-venue price data.
- **"Where's the edge?"** — No fee-adjusted return analysis showing which market types and strategies are actually profitable after Polymarket's fee structure.

The operator currently exports their Polymarket history as CSV (market name, action, USDC amount, token amount, token name, timestamp, tx hash) and has no way to feed it back into the system for analysis. This feature closes the loop between trading and analysis.

## Functional Requirements

### FR-001: Polymarket CSV Trade Import

The system MUST import Polymarket's trade history CSV format with columns: `marketName`, `action` (Buy/Sell/Deposit/Withdraw), `usdcAmount`, `tokenAmount`, `tokenName` (Yes/No/outcome name), `timestamp` (Unix epoch), `hash` (transaction hash).

- Deposits and Withdrawals MUST be tracked for capital flow analysis but excluded from P&L calculations.
- Duplicate imports (same `hash`) MUST be idempotent — re-importing a CSV with overlapping transactions MUST NOT create duplicates.
- Import MUST parse the CSV, validate each row, and persist to PostgreSQL.
- Import MUST resolve market names to Polymarket condition IDs where possible (via Gamma API lookup or cached match).

### FR-002: Position Reconstruction

The system MUST reconstruct positions from imported trades using FIFO cost-basis accounting:

- **Open positions**: Net token holdings per market/outcome after all buys and sells.
- **Closed positions**: Fully offset buy/sell pairs with realized P&L.
- **Resolved positions**: Markets that have resolved — tokens worth $1.00 (winning outcome) or $0.00 (losing outcome). Realized P&L computed from cost basis vs. resolution value.
- Position state MUST update when new trades are imported or when market resolution status changes.

### FR-003: Portfolio P&L Dashboard

The system MUST provide portfolio-level metrics:

- **Total realized P&L**: Sum of all closed/resolved position profits and losses.
- **Total unrealized P&L**: Mark-to-market of open positions using current best bid/ask.
- **Win rate**: Percentage of closed/resolved positions with positive P&L.
- **Average trade size**: Mean USDC per trade.
- **Capital deployed**: Total USDC used for buys (excluding deposits).
- **ROI**: Total P&L / Total capital deployed.
- **P&L by market category**: Breakdown by market type (BTC threshold, sports, politics, etc.) using keyword classification.
- **P&L timeline**: Daily/weekly cumulative P&L chart.

### FR-004: Fee-Adjusted Returns

All P&L calculations MUST account for Polymarket's fee structure:

- Polymarket charges fees on **net winnings only** (not on losing trades).
- The fee schedule (currently 2% on winnings) MUST be read from `config.yaml` fee configuration.
- Display both gross and net (fee-adjusted) P&L.

### FR-005: Signal Comparison

The system MUST compare imported trades against scanner signals that were active at the time of each trade:

- For each imported trade, query `flippening_signals` and `arb_opportunities` tables for signals that overlapped the trade's timestamp and market.
- Classify trades as: **Signal-aligned** (trade direction matches scanner signal), **Signal-contrary** (trade opposes scanner signal), **No signal** (no scanner signal existed for that market/time).
- Compute P&L breakdown by alignment category to answer: "Am I better off when I follow the scanner?"

### FR-006: Historical Arb Backtest

The system MUST backtest cross-venue arbitrage detection against historical price snapshots:

- Use stored `scan_snapshots` (existing arb scanner price captures) to replay the matching + arb detection pipeline.
- For each historical snapshot, compute: what arb opportunities would have been detected, their theoretical spread, and fee-adjusted net profit.
- Aggregate: total theoretical opportunities, average spread, estimated annual yield if all were executed.

### FR-007: CLI Commands

New commands under the `arb-scanner` CLI:

- `import-trades <csv-path>` — Import a Polymarket CSV. Options: `--dry-run` (validate without persisting), `--format table|json`.
- `portfolio` — Display current portfolio summary (open positions, P&L). Options: `--category <type>`, `--format table|json`.
- `backtest-report` — Generate backtest analysis comparing actual trades vs. signals. Options: `--since`, `--until`, `--format table|json`.

### FR-008: Dashboard Backtest Tab

The web dashboard MUST include a **Backtest** tab with:

- Portfolio summary cards (total P&L, win rate, ROI, capital deployed).
- P&L timeline chart (Chart.js line chart, daily granularity).
- Trade history table (sortable, filterable by market, action, outcome).
- Signal comparison breakdown (pie chart: aligned / contrary / no signal).
- Import button triggering CSV upload via API endpoint.

### FR-009: Performance Metrics Persistence (Feedback Data Contract)

The system MUST persist structured performance metrics that downstream features (e.g., adaptive signal tuning) can query programmatically. This is the data contract between 023 (analysis) and future features that close the feedback loop.

The system MUST compute and store to a `category_performance` table on each portfolio recalculation:

- **category**: Market category slug (e.g., `"btc_threshold"`, `"nba"`, `"oscars"`). Derived from market name keyword classification (same heuristic as flippening discovery).
- **win_rate**: Float 0.0–1.0 for closed/resolved positions in that category.
- **avg_pnl**: Average fee-adjusted P&L per trade.
- **trade_count**: Number of closed/resolved trades.
- **total_pnl**: Sum of fee-adjusted realized P&L.
- **profit_factor**: Gross wins / gross losses (capped at 999.99).
- **avg_hold_minutes**: Average time between entry and exit.
- **signal_alignment_rate**: Fraction of trades that were signal-aligned.
- **aligned_win_rate**: Win rate of signal-aligned trades only.
- **contrary_win_rate**: Win rate of signal-contrary trades only.
- **computed_at**: Timestamp of last computation.

Additionally, the system MUST persist per-category **optimal parameter snapshots** from `flip-sweep` results when available:

- `optimal_params` table: `(category, param_name, optimal_value, win_rate_at_optimal, sweep_date)`.
- Populated by a new `--persist` flag on `flip-sweep` and `flip-evaluate` commands.
- These tables form the read interface for feature 024 (adaptive signal tuning).

The system MUST expose these via:
- `GET /api/backtest/category-performance` — category performance breakdown.
- `GET /api/backtest/optimal-params` — latest optimal parameter snapshots.
- CLI: `backtest-report --category <slug>` includes category performance metrics.

## Non-Functional Requirements

### NFR-001: Import Performance

CSV import MUST process 1,000 trades in under 5 seconds. Deduplication check via `hash` column MUST use a database unique index, not application-level scanning.

### NFR-002: Backward Compatibility

Existing flippening replay commands (`flip-replay`, `flip-evaluate`, `flip-sweep`) MUST continue to work unchanged. The new backtesting system is additive.

### NFR-003: Observability

All import operations MUST log via structlog with: file path, row count, duplicate count, error count. P&L calculations MUST log input parameters and result summaries.

### NFR-004: Data Integrity

Trade imports MUST be transactional — if any row fails validation, the entire import MUST roll back. The `hash` column MUST have a UNIQUE constraint to prevent duplicates at the database level.

## Edge Cases

### EC-001: CSV With Only Deposits

If the CSV contains only Deposit/Withdraw rows and no trades, the import MUST succeed but the portfolio report MUST show zero positions and zero P&L.

### EC-002: Unknown Market Names

If a market name from CSV cannot be resolved to a Polymarket condition ID, the trade MUST still be imported with `condition_id = NULL`. Portfolio display MUST show the raw market name.

### EC-003: Partially Resolved Markets

Markets with multiple outcomes where some outcomes have resolved and others haven't MUST show partially-realized P&L. Example: a multi-outcome market where "Yes" resolved to $0.00 but "No" hasn't settled yet.

### EC-004: Re-import After Market Resolution

If trades were imported when a market was open, and later the market resolves, running `portfolio` MUST pick up the resolution and compute realized P&L. Resolution status SHOULD be refreshable via Gamma API.

### EC-005: Sell Without Prior Buy

If a Sell appears for a market/outcome with no prior Buy in the imported data (e.g., position opened before the CSV export window), create an "unknown cost basis" position. Report P&L as "incomplete — missing entry data" rather than computing a misleading number.

### EC-006: Non-Standard Token Names

Some Polymarket markets use custom outcome names (e.g., "TCU Horned Frogs" instead of "Yes"/"No"). The system MUST handle arbitrary `tokenName` values, not just "Yes" and "No".

## Success Criteria

- SC-001: Import the provided Polymarket CSV (`Polymarket-History-2026-03-06.csv`) with zero errors, correct deduplication on re-import.
- SC-002: Portfolio command shows correct realized P&L for resolved BTC threshold markets and correct open positions for unresolved ones.
- SC-003: Fee-adjusted P&L differs from gross P&L by the correct Polymarket fee amount.
- SC-004: Signal comparison correctly classifies trades as aligned/contrary/no-signal against stored flippening signals.
- SC-005: Dashboard Backtest tab renders portfolio summary, P&L chart, and trade table.
- SC-006: `import-trades --dry-run` validates CSV without persisting any data.
- SC-007: All quality gates pass (ruff, mypy, pytest, coverage >= 70%).

## Dependencies

- PostgreSQL (existing) — new tables for imported trades and positions.
- Polymarket Gamma API (existing client) — market resolution lookup.
- Chart.js (existing CDN in dashboard) — P&L timeline chart.
- Existing fee configuration in `config.yaml`.
- Existing `flippening_signals` and `arb_opportunities` tables for signal comparison.

## Out of Scope

- Kalshi trade import (Kalshi doesn't offer CSV export in the same format; can be added later).
- Automated trade import from on-chain data (would require blockchain indexing; CSV is sufficient for v1).
- Tax reporting or cost-basis methods beyond FIFO (LIFO, specific lot — future feature).
- Real-time P&L streaming (portfolio updates on refresh, not live push).
- Multi-user portfolio isolation (single operator assumption per constitution).
