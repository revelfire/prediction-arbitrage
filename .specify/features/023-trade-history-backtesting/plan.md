# 023 — Trade History & Backtesting: Implementation Plan

## Current State Audit

| File | Lines | Status |
|------|------:|--------|
| `models/config.py` | 368 | Over limit — no changes needed |
| `models/replay.py` | 56 | OK |
| `models/flippening.py` | 215 | OK |
| `storage/repository.py` | 391 | Over limit — no changes needed |
| `storage/flippening_repository.py` | 332 | Over limit — no changes needed |
| `storage/analytics_repository.py` | 324 | Over limit — no changes needed |
| `api/routes_flippening.py` | 198 | OK — reference pattern for new routes |
| `api/app.py` | 399 | Over limit — only add router include |
| `api/static/app.js` | 2054 | Over limit — add backtest tab JS |
| `api/static/index.html` | 716 | Over limit — add backtest tab HTML |
| `cli/app.py` | existing | Add command registrations |
| Last migration | `025_position_market_title.sql` | Next = 026 |

## Architecture

```
New files:
  models/backtesting.py          # ~120 lines: ImportedTrade, Position, PortfolioSummary, SignalAlignment
  storage/backtesting_repository.py  # ~250 lines: import, position, P&L queries
  storage/_backtesting_queries.py    # ~100 lines: raw SQL constants
  storage/migrations/026_trade_history.sql  # Schema for imported_trades, trade_positions
  backtesting/                   # New package
    csv_importer.py              # ~80 lines: CSV parsing + validation
    position_engine.py           # ~120 lines: FIFO cost basis reconstruction
    portfolio_calculator.py      # ~100 lines: P&L, ROI, fee adjustment
    signal_comparator.py         # ~80 lines: cross-reference trades vs. signals
  api/routes_backtesting.py      # ~150 lines: REST endpoints
  cli/backtesting_commands.py    # ~100 lines: import-trades, portfolio, backtest-report

Modified files:
  api/app.py                     # +2 lines: include backtesting router
  api/static/index.html          # +~80 lines: Backtest tab HTML
  api/static/app.js              # +~200 lines: Backtest tab JS
  cli/app.py                     # +3 lines: register backtesting commands
  api/deps.py                    # +~10 lines: backtesting repo dependency
```

## Phase 1: Data Models (~120 lines)

**New file: `models/backtesting.py`**

```python
class TradeAction(str, Enum):
    BUY = "Buy"
    SELL = "Sell"
    DEPOSIT = "Deposit"
    WITHDRAW = "Withdraw"

class ImportedTrade(BaseModel):
    """A single trade row from Polymarket CSV export."""
    id: int | None = None
    market_name: str
    action: TradeAction
    usdc_amount: Decimal
    token_amount: Decimal
    token_name: str  # "Yes", "No", or custom outcome name
    timestamp: datetime
    tx_hash: str
    condition_id: str | None = None  # Resolved via Gamma API lookup
    imported_at: datetime | None = None

class PositionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"       # Fully offset by sells
    RESOLVED = "resolved"   # Market resolved

class TradePosition(BaseModel):
    """Reconstructed position from FIFO cost basis."""
    id: int | None = None
    market_name: str
    token_name: str
    cost_basis: Decimal      # Total USDC spent (buys)
    tokens_held: Decimal     # Net token balance
    avg_entry_price: Decimal
    realized_pnl: Decimal    # From sells and resolutions
    unrealized_pnl: Decimal  # Mark-to-market
    status: PositionStatus
    fee_paid: Decimal        # Polymarket fee on winnings
    first_trade_at: datetime
    last_trade_at: datetime

class SignalAlignment(str, Enum):
    ALIGNED = "aligned"
    CONTRARY = "contrary"
    NO_SIGNAL = "no_signal"

class PortfolioSummary(BaseModel):
    """Aggregate portfolio metrics."""
    total_realized_pnl: Decimal
    total_unrealized_pnl: Decimal
    total_fees: Decimal
    net_pnl: Decimal
    win_count: int
    loss_count: int
    win_rate: float
    total_capital_deployed: Decimal
    roi: float
    trade_count: int
    avg_trade_size: Decimal
    positions: list[TradePosition]
```

## Phase 2: Database Migration

**New file: `storage/migrations/026_trade_history.sql`**

```sql
-- Imported trade history from Polymarket CSV
CREATE TABLE IF NOT EXISTS imported_trades (
    id BIGSERIAL PRIMARY KEY,
    market_name TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('Buy', 'Sell', 'Deposit', 'Withdraw')),
    usdc_amount NUMERIC(18,6) NOT NULL,
    token_amount NUMERIC(18,6) NOT NULL,
    token_name TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    tx_hash TEXT NOT NULL UNIQUE,
    condition_id TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_imported_trades_market ON imported_trades(market_name);
CREATE INDEX IF NOT EXISTS idx_imported_trades_timestamp ON imported_trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_imported_trades_action ON imported_trades(action);

-- Reconstructed positions (materialized from trades)
CREATE TABLE IF NOT EXISTS trade_positions (
    id BIGSERIAL PRIMARY KEY,
    market_name TEXT NOT NULL,
    token_name TEXT NOT NULL,
    cost_basis NUMERIC(18,6) NOT NULL DEFAULT 0,
    tokens_held NUMERIC(18,6) NOT NULL DEFAULT 0,
    avg_entry_price NUMERIC(18,6) NOT NULL DEFAULT 0,
    realized_pnl NUMERIC(18,6) NOT NULL DEFAULT 0,
    unrealized_pnl NUMERIC(18,6) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'resolved')),
    fee_paid NUMERIC(18,6) NOT NULL DEFAULT 0,
    first_trade_at TIMESTAMPTZ NOT NULL,
    last_trade_at TIMESTAMPTZ NOT NULL,
    UNIQUE(market_name, token_name)
);
```

## Phase 3: CSV Importer (~80 lines)

**New file: `backtesting/csv_importer.py`**

- Parse Polymarket CSV format (7 columns: marketName, action, usdcAmount, tokenAmount, tokenName, timestamp, hash).
- Validate each row into `ImportedTrade` Pydantic model.
- Convert Unix epoch timestamp to UTC datetime.
- Filter Deposit/Withdraw rows (flag, don't discard — needed for capital flow).
- Return `list[ImportedTrade]` for batch insert.
- `dry_run` mode: validate and return stats without touching DB.

## Phase 4: Storage Layer (~350 lines across 2 files)

**New file: `storage/_backtesting_queries.py` (~100 lines)**
- SQL constants: INSERT trades, SELECT trades by market/date, UPSERT positions, SELECT portfolio, dedup check.

**New file: `storage/backtesting_repository.py` (~250 lines)**
- `import_trades(trades: list[ImportedTrade]) -> ImportResult` — Batch insert with ON CONFLICT (tx_hash) DO NOTHING. Return counts (inserted, duplicates).
- `get_trades(market_name?, since?, until?, action?) -> list[ImportedTrade]` — Filtered trade history.
- `get_positions(status?) -> list[TradePosition]` — Current positions.
- `upsert_position(position: TradePosition)` — Create or update position after reconstruction.
- `get_portfolio_summary() -> PortfolioSummary` — Aggregate query.
- `get_capital_flows() -> list[ImportedTrade]` — Deposits and withdrawals only.
- `get_daily_pnl(since?, until?) -> list[dict]` — Daily realized P&L for chart.

## Phase 5: Position Engine (~120 lines)

**New file: `backtesting/position_engine.py`**

FIFO cost-basis reconstruction:
1. Group imported trades by `(market_name, token_name)`.
2. Sort by timestamp within each group.
3. For each group:
   - Maintain a FIFO queue of buy lots: `deque[(price, quantity)]`.
   - On Buy: push `(usdc/tokens, tokens)` onto queue.
   - On Sell: pop from front of queue, compute realized P&L = `(sell_price - buy_price) * quantity`.
   - Handle partial lot consumption.
4. Output: `TradePosition` per group with cost basis, tokens held, realized P&L.
5. Edge case: Sell without prior buy → create "unknown basis" position.

## Phase 6: Portfolio Calculator (~100 lines)

**New file: `backtesting/portfolio_calculator.py`**

- `calculate_portfolio(positions: list[TradePosition], fee_rate: Decimal) -> PortfolioSummary`
- Fee adjustment: For positions with positive realized P&L, deduct `fee_rate * realized_pnl`.
- Win/loss counting: position with `realized_pnl > 0` after fees = win.
- ROI = net P&L / total capital deployed.
- Capital deployed = sum of all buy trade USDC amounts.

## Phase 7: Signal Comparator (~80 lines)

**New file: `backtesting/signal_comparator.py`**

- `compare_trades_to_signals(trades, signals) -> list[tuple[ImportedTrade, SignalAlignment]]`
- For each Buy/Sell trade, query flippening_signals and arb_opportunities within ±30 minutes of trade timestamp and matching market.
- Classify: aligned if trade direction matches signal, contrary if opposite, no_signal if no match.
- Aggregate: P&L by alignment category.

## Phase 8: Performance Metrics Persistence (~150 lines)

**New file: `backtesting/performance_tracker.py` (~80 lines)**

- `compute_category_performance(positions, signal_comparisons) -> list[CategoryPerformance]`
- Group positions by category (keyword classification of market_name).
- Per category: win_rate, avg_pnl, profit_factor, avg_hold_minutes, signal alignment metrics.
- Called automatically after position reconstruction.

**New model in `models/backtesting.py` (+~30 lines)**

```python
class CategoryPerformance(BaseModel):
    """Per-category trading performance metrics. Consumed by 024 adaptive tuning."""
    category: str
    win_rate: float
    avg_pnl: Decimal
    trade_count: int
    total_pnl: Decimal
    profit_factor: float
    avg_hold_minutes: float
    signal_alignment_rate: float
    aligned_win_rate: float
    contrary_win_rate: float
    computed_at: datetime

class OptimalParamSnapshot(BaseModel):
    """Persisted sweep result for a category + parameter."""
    category: str
    param_name: str
    optimal_value: float
    win_rate_at_optimal: float
    sweep_date: datetime
```

**Migration addition in `026_trade_history.sql` (+~20 lines)**

```sql
CREATE TABLE IF NOT EXISTS category_performance (
    id BIGSERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    win_rate NUMERIC(6,4) NOT NULL DEFAULT 0,
    avg_pnl NUMERIC(18,6) NOT NULL DEFAULT 0,
    trade_count INT NOT NULL DEFAULT 0,
    total_pnl NUMERIC(18,6) NOT NULL DEFAULT 0,
    profit_factor NUMERIC(8,2) NOT NULL DEFAULT 0,
    avg_hold_minutes NUMERIC(10,2) NOT NULL DEFAULT 0,
    signal_alignment_rate NUMERIC(6,4) NOT NULL DEFAULT 0,
    aligned_win_rate NUMERIC(6,4) NOT NULL DEFAULT 0,
    contrary_win_rate NUMERIC(6,4) NOT NULL DEFAULT 0,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(category)
);

CREATE TABLE IF NOT EXISTS optimal_params (
    id BIGSERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    param_name TEXT NOT NULL,
    optimal_value NUMERIC(10,6) NOT NULL,
    win_rate_at_optimal NUMERIC(6,4) NOT NULL,
    sweep_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(category, param_name)
);
```

**Storage additions:**
- `storage/_backtesting_queries.py` (+~30 lines): UPSERT category_performance, UPSERT optimal_params, SELECT queries.
- `storage/backtesting_repository.py` (+~40 lines): `upsert_category_performance()`, `get_category_performance()`, `upsert_optimal_params()`, `get_optimal_params()`.

**Existing CLI modification:**
- Add `--persist` flag to `flip-sweep` command in `cli/replay_commands.py`. When set, persist the best-performing parameter value to `optimal_params` table.

## Phase 9: CLI Commands (~100 lines)

**New file: `cli/backtesting_commands.py`**

Three commands:

```
import-trades <csv-path>
  --dry-run       Validate only, no DB writes
  --format        table|json (default: table)

portfolio
  --category      Filter by market category keyword
  --status        open|closed|resolved|all (default: all)
  --format        table|json

backtest-report
  --since         ISO 8601 start date
  --until         ISO 8601 end date
  --format        table|json
```

Register in `cli/app.py` like existing `replay_commands`.

## Phase 10: API Routes (~150 lines)

**New file: `api/routes_backtesting.py`**

Endpoints following `routes_flippening.py` pattern:

- `POST /api/backtest/import` — Accept CSV file upload (multipart/form-data). Parse, validate, import.
- `GET /api/backtest/trades` — Trade history with filters (market, action, since/until, limit).
- `GET /api/backtest/positions` — Current positions with P&L.
- `GET /api/backtest/portfolio` — Portfolio summary (PortfolioSummary JSON).
- `GET /api/backtest/daily-pnl` — Daily P&L array for chart rendering.
- `GET /api/backtest/signal-comparison` — Signal alignment breakdown.
- `GET /api/backtest/category-performance` — Per-category performance metrics (FR-009).
- `GET /api/backtest/optimal-params` — Latest optimal parameter snapshots (FR-009).

Wire into `app.py` via `app.include_router(backtest_router)`.

## Phase 11: Dashboard Tab (~280 lines across HTML + JS)

**Modify: `api/static/index.html` (+~80 lines)**
- Add "Backtest" tab button alongside existing tabs.
- Add tab content div with: summary cards row, P&L chart canvas, trade table, signal comparison pie chart, category performance table, CSV import dropzone.

**Modify: `api/static/app.js` (+~200 lines)**
- `loadBacktestTab()` — Fetch `/api/backtest/portfolio`, `/api/backtest/daily-pnl`, `/api/backtest/signal-comparison`, `/api/backtest/category-performance`.
- Render summary cards (Total P&L, Win Rate, ROI, Capital Deployed).
- Render P&L timeline using Chart.js (line chart, daily granularity).
- Render trade history table (sortable columns).
- Render signal alignment pie chart (Chart.js doughnut).
- Render category performance table (category, win rate, avg P&L, aligned win rate — sortable).
- CSV upload handler → POST to `/api/backtest/import` → refresh tab.

## Phase 12: Tests

**New test files:**
- `tests/unit/test_csv_importer.py` — Parse valid CSV, handle deposits, reject malformed rows, dedup.
- `tests/unit/test_position_engine.py` — FIFO cost basis: simple buy/sell, partial fills, sell-without-buy, multi-market.
- `tests/unit/test_portfolio_calculator.py` — Fee adjustment, win rate, ROI edge cases (zero capital, all losses).
- `tests/unit/test_signal_comparator.py` — Aligned, contrary, no-signal classification.
- `tests/unit/test_performance_tracker.py` — Category classification, metric aggregation, edge cases.
- `tests/unit/test_backtesting_models.py` — Pydantic model validation, enums.
- `tests/unit/test_backtesting_routes.py` — API endpoint integration tests with mock repo.
- `tests/unit/test_backtesting_cli.py` — CLI command invocation with mock data.

## Implementation Order

| Phase | Depends On | Complexity | Est. New Lines |
|-------|-----------|------------|----------------|
| 1. Data Models | None | Low | ~150 |
| 2. Migration | None | Low | ~50 |
| 3. CSV Importer | Phase 1 | Low | ~80 |
| 4. Storage Layer | Phase 1, 2 | Medium | ~420 |
| 5. Position Engine | Phase 1 | Medium | ~120 |
| 6. Portfolio Calculator | Phase 1, 5 | Low | ~100 |
| 7. Signal Comparator | Phase 1 | Low | ~80 |
| 8. Perf Metrics | Phase 5, 6, 7 | Medium | ~150 |
| 9. CLI Commands | Phase 3, 4, 5, 6, 8 | Medium | ~110 |
| 10. API Routes | Phase 4, 5, 6, 7, 8 | Medium | ~180 |
| 11. Dashboard Tab | Phase 10 | Medium | ~300 |
| 12. Tests | All above | Medium | ~550 |
| **Total** | | | **~2,290** |

## Key Design Decisions

1. **Separate `backtesting/` package** (not inside `flippening/`): Trade history and portfolio analysis are scanner-wide, not flippening-specific. The existing `flippening/replay_*.py` modules stay untouched.

2. **FIFO in application layer** (not SQL): Cost basis reconstruction with partial lot consumption is complex in SQL. Python `deque` is clearer, testable, and handles edge cases (sell-without-buy) gracefully. Positions are materialized to DB after computation.

3. **No Gamma API resolution in v1**: Market name → condition_id mapping is nice-to-have but adds API dependency and rate limit concerns to import. Import stores raw market names; condition_id resolution can be backfilled later.

4. **CSV upload via multipart** (not file path): Dashboard import uses browser file upload. CLI uses local file path. Both feed into the same `csv_importer.parse_csv()` function.

5. **Materialized positions table**: Positions could be computed on-the-fly from trades, but materializing them (with upsert) enables fast dashboard queries and incremental updates on re-import.
