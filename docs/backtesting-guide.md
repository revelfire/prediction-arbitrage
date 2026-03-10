# Backtesting & Trade History Guide

This guide covers the backtesting dashboard tab and CLI commands for importing Polymarket trade history, analyzing portfolio performance, and comparing trades against flippening signals.

---

## Prerequisites

- Migration 026 applied (`uv run arb-scanner migrate`)
- Dashboard running (`uv run arb-scanner serve` or `docker compose up -d dashboard`)
- A Polymarket trade history CSV export (see [Exporting Trades](#exporting-trades-from-polymarket))

---

## Exporting Trades from Polymarket

1. Go to [polymarket.com](https://polymarket.com) and log in.
2. Navigate to **Portfolio** > **History**.
3. Click **Export CSV** (top-right of the trade history table).
4. Save the file locally.

The CSV must have these columns: `marketName`, `action`, `usdcAmount`, `tokenAmount`, `tokenName`, `timestamp`, `hash`. The `action` column accepts: `Buy`, `Sell`, `Deposit`, `Withdraw`.

---

## Dashboard Tab

Open the dashboard and click the **Backtesting** tab (under the "Analysis" group in the tab bar).

### Portfolio Summary Cards

Six cards across the top:

| Card | Description |
|------|-------------|
| **Net P&L** | Realized + unrealized P&L minus fees. Green if positive, red if negative. |
| **Win Rate** | Percentage of closed positions that were profitable. |
| **ROI** | Net P&L divided by total capital deployed. |
| **Capital Deployed** | Sum of all position cost bases. |
| **Trades** | Total number of reconstructed positions. |
| **Total Fees** | Cumulative fees paid across all trades. |

### Daily P&L Chart

A dual-axis line chart showing:
- **Daily P&L** (left axis, cyan) — realized P&L per day.
- **Cumulative P&L** (right axis, green dashed) — running total over time.

### Signal Alignment

A doughnut chart breaking down how your trades relate to flippening signals:
- **Aligned** (green) — trade direction matched the signal.
- **Contrary** (red) — trade went against the signal.
- **No signal** (gray) — no matching flippening signal found.

This requires category performance data to be computed (via `backtest-report` CLI or the `portfolio` command).

### Category Performance Table

Per-category breakdown of trading results:

| Column | Description |
|--------|-------------|
| Category | Market category (nba, nfl, btc_threshold, etc.) |
| Win Rate | Percentage of winning positions in this category |
| Avg P&L | Average P&L per position |
| Trades | Number of positions in this category |
| Total P&L | Sum of realized P&L |
| Profit Factor | Gross profit / gross loss (higher is better) |

### Trade History Table

Scrollable table of imported trades showing date, market name, action (Buy/Sell/Deposit/Withdraw), USDC amount, token amount, and token name.

### CSV Import

Click **Upload Polymarket CSV** to import trades directly from the dashboard. The status message will show how many trades were imported and how many were duplicates (by transaction hash).

---

## CLI Commands

All backtesting commands work without the dashboard running — they connect directly to PostgreSQL.

### Import Trades

```bash
# Dry run (validate only, no DB writes)
uv run arb-scanner import-trades ~/Downloads/polymarket-history.csv --dry-run

# Import to database
uv run arb-scanner import-trades ~/Downloads/polymarket-history.csv
```

Duplicate trades (same `hash`) are automatically skipped.

### Portfolio Analysis

```bash
# Show all positions and portfolio summary
uv run arb-scanner portfolio

# Filter by status
uv run arb-scanner portfolio --status closed

# Filter by category
uv run arb-scanner portfolio --category nba

# JSON output
uv run arb-scanner portfolio --format json
```

The `portfolio` command reconstructs positions from imported trades using FIFO cost-basis accounting, computes per-category performance metrics, and persists the results for the dashboard.

### Backtest Report

```bash
# Full report: signal comparison + portfolio + category performance
uv run arb-scanner backtest-report

# Filter by category
uv run arb-scanner backtest-report --category nba

# Filter by date range
uv run arb-scanner backtest-report --since 2026-01-01 --until 2026-03-01
```

### Parameter Sweeps (with persistence)

```bash
# Sweep a parameter and persist the best result
uv run arb-scanner flip-sweep \
  --param spike_threshold_pct \
  --min 0.05 --max 0.20 --step 0.01 \
  --category nba \
  --persist
```

The `--persist` flag saves the optimal parameter value to the `optimal_params` table, viewable via the dashboard's optimal params API endpoint (`GET /api/backtesting/optimal-params`).

---

## API Endpoints

All endpoints are under `/api/backtesting/`:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/import` | Upload CSV file (multipart/form-data) |
| `GET` | `/trades` | Trade history (`?market_name=`, `?action=`, `?limit=`) |
| `GET` | `/positions` | Positions (`?status=open\|closed\|resolved`) |
| `GET` | `/portfolio` | Aggregate portfolio metrics |
| `GET` | `/daily-pnl` | Daily P&L array (`?since=ISO_DATE`) |
| `GET` | `/signal-comparison` | Signal alignment counts |
| `GET` | `/category-performance` | Per-category metrics |
| `GET` | `/optimal-params` | Optimal sweep params (`?category=`) |

---

## Workflow

A typical backtesting workflow:

1. **Export** your trade history CSV from Polymarket.
2. **Import** via the dashboard upload button or `import-trades` CLI.
3. **Run** `uv run arb-scanner portfolio` to reconstruct positions and compute category performance.
4. **View** the Backtesting tab for charts and summaries.
5. **Compare** against flippening signals with `backtest-report`.
6. **Tune** parameters with `flip-sweep --persist` and check the optimal params endpoint.
7. **Re-import** periodically as you make new trades — duplicates are skipped automatically.
