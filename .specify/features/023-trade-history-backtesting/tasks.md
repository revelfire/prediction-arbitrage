# 023 — Trade History & Backtesting: Tasks

## Phase 1: Data Models

- [ ] 1.1 Create `models/backtesting.py` with `TradeAction`, `ImportedTrade`, `PositionStatus`, `TradePosition`, `SignalAlignment`, `PortfolioSummary` models (~120 lines)
- [ ] 1.2 Add `ImportResult` model (inserted count, duplicate count, error count)
- [ ] 1.3 Export new models from `models/__init__.py`
- [ ] 1.4 Run quality gates (ruff, mypy)

## Phase 2: Database Migration

- [ ] 2.1 Create `storage/migrations/026_trade_history.sql` with `imported_trades` table (UNIQUE on tx_hash, indexes on market_name, timestamp, action)
- [ ] 2.2 Add `trade_positions` table (UNIQUE on market_name + token_name, status CHECK constraint)
- [ ] 2.3 Test migration against local PostgreSQL

## Phase 3: CSV Importer

- [ ] 3.1 Create `backtesting/__init__.py`
- [ ] 3.2 Create `backtesting/csv_importer.py` with `parse_csv(path: Path) -> list[ImportedTrade]` function
- [ ] 3.3 Handle Unix epoch → UTC datetime conversion
- [ ] 3.4 Validate all rows via Pydantic, collect errors
- [ ] 3.5 Add `dry_run` mode returning validation stats without DB writes
- [ ] 3.6 Write `tests/unit/test_csv_importer.py` — valid CSV, deposits, malformed rows, empty file
- [ ] 3.7 Run quality gates

## Phase 4: Storage Layer

- [ ] 4.1 Create `storage/_backtesting_queries.py` with SQL constants (INSERT trades ON CONFLICT DO NOTHING, SELECT trades, UPSERT positions, aggregate portfolio)
- [ ] 4.2 Create `storage/backtesting_repository.py` with `BacktestingRepository` class
- [ ] 4.3 Implement `import_trades()` — batch insert with dedup, return `ImportResult`
- [ ] 4.4 Implement `get_trades()` — filtered query with market_name, since, until, action params
- [ ] 4.5 Implement `get_positions()` — filtered by status
- [ ] 4.6 Implement `upsert_position()` — INSERT ON CONFLICT UPDATE for position materialization
- [ ] 4.7 Implement `get_portfolio_summary()` — aggregate P&L, win rate, capital deployed
- [ ] 4.8 Implement `get_daily_pnl()` — daily realized P&L for chart
- [ ] 4.9 Implement `get_capital_flows()` — deposits and withdrawals
- [ ] 4.10 Add `get_backtest_repo` dependency in `api/deps.py`
- [ ] 4.11 Run quality gates

## Phase 5: Position Engine

- [ ] 5.1 Create `backtesting/position_engine.py` with `reconstruct_positions(trades) -> list[TradePosition]`
- [ ] 5.2 Implement FIFO cost-basis queue: buy lots as `deque[(price, quantity)]`
- [ ] 5.3 Handle Buy (push lot), Sell (pop from front, compute realized P&L), partial lot consumption
- [ ] 5.4 Handle Sell-without-Buy edge case (unknown cost basis)
- [ ] 5.5 Handle non-standard token names (arbitrary strings, not just Yes/No)
- [ ] 5.6 Write `tests/unit/test_position_engine.py` — simple buy/sell, partial fills, multi-market, sell-without-buy, FIFO ordering
- [ ] 5.7 Run quality gates

## Phase 6: Portfolio Calculator

- [ ] 6.1 Create `backtesting/portfolio_calculator.py` with `calculate_portfolio(positions, fee_rate) -> PortfolioSummary`
- [ ] 6.2 Implement fee adjustment: deduct `fee_rate * realized_pnl` for winning positions only
- [ ] 6.3 Compute win/loss counts, win rate, ROI, avg trade size, capital deployed
- [ ] 6.4 Write `tests/unit/test_portfolio_calculator.py` — fee adjustment, all-wins, all-losses, zero capital, mixed
- [ ] 6.5 Run quality gates

## Phase 7: Signal Comparator

- [ ] 7.1 Create `backtesting/signal_comparator.py` with `compare_trades_to_signals(trades, signals) -> list[tuple]`
- [ ] 7.2 Implement time-window matching (±30 min) and market name matching
- [ ] 7.3 Classify: aligned, contrary, no_signal
- [ ] 7.4 Aggregate P&L by alignment category
- [ ] 7.5 Write `tests/unit/test_signal_comparator.py` — aligned, contrary, no-signal, edge cases
- [ ] 7.6 Run quality gates

## Phase 8: Performance Metrics Persistence (Feedback Data Contract)

- [ ] 8.1 Add `CategoryPerformance` and `OptimalParamSnapshot` models to `models/backtesting.py`
- [ ] 8.2 Add `category_performance` and `optimal_params` tables to migration `026_trade_history.sql`
- [ ] 8.3 Create `backtesting/performance_tracker.py` with `compute_category_performance(positions, signal_comparisons) -> list[CategoryPerformance]`
- [ ] 8.4 Implement market name → category classification (keyword matching, reuse flippening category_keywords patterns)
- [ ] 8.5 Add `upsert_category_performance()` and `get_category_performance()` to `backtesting_repository.py`
- [ ] 8.6 Add `upsert_optimal_params()` and `get_optimal_params()` to `backtesting_repository.py`
- [ ] 8.7 Add `--persist` flag to `flip-sweep` in `cli/replay_commands.py` — persist best param to `optimal_params`
- [ ] 8.8 Wire performance tracker into portfolio recalculation (auto-compute on `portfolio` command / API call)
- [ ] 8.9 Write `tests/unit/test_performance_tracker.py` — category classification, metric aggregation, edge cases
- [ ] 8.10 Run quality gates

## Phase 9: CLI Commands

- [ ] 9.1 Create `cli/backtesting_commands.py` with `import_trades`, `portfolio`, `backtest_report` commands
- [ ] 9.2 Implement `import-trades <csv-path>` — parse CSV, import to DB, show summary table
- [ ] 9.3 Implement `portfolio` — reconstruct positions, calculate portfolio, persist category performance, display
- [ ] 9.4 Implement `backtest-report` — signal comparison + portfolio + category performance combined view
- [ ] 9.5 Add `--dry-run`, `--format`, `--category`, `--status`, `--since`, `--until` options
- [ ] 9.6 Register commands in `cli/app.py`
- [ ] 9.7 Write `tests/unit/test_backtesting_cli.py` — command invocation with mock data
- [ ] 9.8 Run quality gates

## Phase 10: API Routes

- [ ] 10.1 Create `api/routes_backtesting.py` with `APIRouter`
- [ ] 10.2 Implement `POST /api/backtest/import` — multipart CSV upload, parse, import
- [ ] 10.3 Implement `GET /api/backtest/trades` — filtered trade history
- [ ] 10.4 Implement `GET /api/backtest/positions` — position list with P&L
- [ ] 10.5 Implement `GET /api/backtest/portfolio` — PortfolioSummary JSON
- [ ] 10.6 Implement `GET /api/backtest/daily-pnl` — daily P&L for chart
- [ ] 10.7 Implement `GET /api/backtest/signal-comparison` — alignment breakdown
- [ ] 10.8 Implement `GET /api/backtest/category-performance` — per-category metrics (FR-009)
- [ ] 10.9 Implement `GET /api/backtest/optimal-params` — latest sweep results (FR-009)
- [ ] 10.10 Include router in `api/app.py`
- [ ] 10.11 Write `tests/unit/test_backtesting_routes.py` — endpoint tests with mock repo
- [ ] 10.12 Run quality gates

## Phase 11: Dashboard Tab

- [ ] 11.1 Add "Backtest" tab button to `index.html` tab bar
- [ ] 11.2 Add Backtest tab content div: summary cards (P&L, Win Rate, ROI, Capital), chart canvas, trade table, signal pie chart, category performance table, CSV upload area
- [ ] 11.3 Add `loadBacktestTab()` in `app.js` — fetch portfolio, daily-pnl, signal-comparison, category-performance endpoints
- [ ] 11.4 Render summary cards from portfolio data
- [ ] 11.5 Render P&L timeline Chart.js line chart (daily granularity)
- [ ] 11.6 Render trade history table (sortable, market name, action, amount, P&L, date)
- [ ] 11.7 Render signal alignment doughnut chart (aligned/contrary/no-signal)
- [ ] 11.8 Render category performance table (category, win rate, avg P&L, trade count, aligned win rate — sortable)
- [ ] 11.9 Implement CSV file upload handler → POST to import endpoint → refresh tab
- [ ] 11.10 Manual test: upload provided CSV, verify dashboard renders

## Phase 12: Integration & Quality

- [ ] 12.1 Run full test suite: `uv run pytest tests/ -x --tb=short`
- [ ] 12.2 Verify coverage: `uv run pytest tests/ --cov=src/arb_scanner --cov-fail-under=70`
- [ ] 12.3 Run `uv run ruff check src/ tests/` — zero errors
- [ ] 12.4 Run `uv run ruff format --check src/ tests/` — clean
- [ ] 12.5 Run `uv run mypy src/ --strict` — zero errors
- [ ] 12.6 End-to-end test: import `Polymarket-History-2026-03-06.csv`, run `portfolio`, verify P&L and category performance
- [ ] 12.7 End-to-end test: start dashboard, navigate to Backtest tab, upload CSV, verify rendering
- [ ] 12.8 End-to-end test: run `flip-sweep --persist`, verify `optimal_params` table populated
