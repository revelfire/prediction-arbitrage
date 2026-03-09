"""CLI commands for trade history import and backtesting analysis."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import structlog
import typer

from arb_scanner.config.loader import load_config

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="cli.backtesting",
)


def register(app: typer.Typer) -> None:
    """Register backtesting commands on the main Typer app.

    Args:
        app: The main CLI Typer application instance.
    """
    app.command(name="import-trades")(import_trades)
    app.command(name="portfolio")(portfolio)
    app.command(name="backtest-report")(backtest_report)


def import_trades(
    csv_path: str = typer.Argument(..., help="Path to Polymarket CSV export."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate only, no DB writes."),
    fmt: str = typer.Option("table", "--format", help="Output format: table or json."),
) -> None:
    """Import trades from a Polymarket CSV export."""
    path = Path(csv_path)
    if not path.exists():
        sys.stdout.write(f"File not found: {csv_path}\n")
        raise typer.Exit(code=1)

    from arb_scanner.backtesting.csv_importer import parse_csv

    try:
        trades = parse_csv(path)
    except ValueError as exc:
        sys.stdout.write(f"Parse error: {exc}\n")
        raise typer.Exit(code=1) from exc

    if dry_run:
        _render_import_dry_run(trades, fmt)
        return

    config = _load_config()
    try:
        result = asyncio.run(_run_import(config, trades))
    except Exception as exc:
        logger.error("import_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    else:
        _render_import_table(result)


def portfolio(
    status: str = typer.Option("", "--status", help="Filter: open, closed, resolved."),
    category: str = typer.Option("", "--category", help="Filter by market category."),
    fmt: str = typer.Option("table", "--format", help="Output format: table or json."),
) -> None:
    """Reconstruct positions and compute portfolio metrics."""
    config = _load_config()
    try:
        data = asyncio.run(_run_portfolio(config, status.strip() or None, category.strip() or None))
    except Exception as exc:
        logger.error("portfolio_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    else:
        _render_portfolio_table(data)


def backtest_report(
    category: str = typer.Option("", "--category", help="Filter by market category."),
    since: str = typer.Option("", "--since", help="Start time (ISO 8601)."),
    until: str = typer.Option("", "--until", help="End time (ISO 8601)."),
    fmt: str = typer.Option("table", "--format", help="Output format: table or json."),
) -> None:
    """Combined signal comparison, portfolio, and category performance report."""
    config = _load_config()
    since_dt, until_dt = _parse_optional_range(since, until)
    cat = category.strip() or None
    try:
        data = asyncio.run(_run_backtest_report(config, cat, since_dt, until_dt))
    except Exception as exc:
        logger.error("backtest_report_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    else:
        _render_backtest_report(data)


# ── Async runners ───────────────────────────────────────────────────


async def _run_import(
    config: Any,
    trades: list[Any],
) -> dict[str, int]:
    """Persist parsed trades to the database.

    Args:
        config: Application settings.
        trades: Validated ImportedTrade objects.

    Returns:
        Dict with inserted/duplicates/errors counts.
    """
    from arb_scanner.storage.backtesting_repository import BacktestingRepository
    from arb_scanner.storage.db import Database

    async with Database(config.storage.database_url) as db:
        repo = BacktestingRepository(db.pool)
        result = await repo.import_trades(trades)
        return {
            "inserted": result.inserted,
            "duplicates": result.duplicates,
            "errors": result.errors,
        }


async def _run_portfolio(
    config: Any,
    status_filter: str | None,
    category_filter: str | None,
) -> dict[str, Any]:
    """Reconstruct positions, compute portfolio, persist category performance.

    Args:
        config: Application settings.
        status_filter: Optional position status filter.
        category_filter: Optional category filter.

    Returns:
        Dict with portfolio summary and category performance.
    """
    from arb_scanner.backtesting.performance_tracker import (
        classify_market_category,
        compute_category_performance,
    )
    from arb_scanner.backtesting.portfolio_calculator import calculate_portfolio
    from arb_scanner.backtesting.position_engine import reconstruct_positions
    from arb_scanner.flippening.category_keywords import DEFAULT_SPORT_KEYWORDS
    from arb_scanner.models.backtesting import ImportedTrade, TradeAction
    from arb_scanner.storage.backtesting_repository import BacktestingRepository
    from arb_scanner.storage.db import Database

    async with Database(config.storage.database_url) as db:
        repo = BacktestingRepository(db.pool)
        trade_rows = await repo.get_trades()
        trades = [ImportedTrade(**{k: v for k, v in r.items() if k != "id"}) for r in trade_rows]
        positions = reconstruct_positions(trades)

        if status_filter:
            positions = [p for p in positions if p.status.value == status_filter]
        if category_filter:
            positions = [
                p
                for p in positions
                if classify_market_category(p.market_name, DEFAULT_SPORT_KEYWORDS)
                == category_filter
            ]

        summary = calculate_portfolio(positions)
        cat_perfs = compute_category_performance(positions, [], DEFAULT_SPORT_KEYWORDS)

        for perf in cat_perfs:
            await repo.upsert_category_performance(perf)

        buy_sells = [t for t in trades if t.action in (TradeAction.Buy, TradeAction.Sell)]
        return {
            "portfolio": summary.model_dump(mode="json"),
            "category_performance": [p.model_dump(mode="json") for p in cat_perfs],
            "trade_count": len(buy_sells),
        }


async def _run_backtest_report(
    config: Any,
    category_filter: str | None,
    since: Any,
    until: Any,
) -> dict[str, Any]:
    """Generate a combined backtest report.

    Args:
        config: Application settings.
        category_filter: Optional category filter.
        since: Start datetime or None.
        until: End datetime or None.

    Returns:
        Dict with portfolio, signal comparison, and category performance.
    """
    from arb_scanner.backtesting.performance_tracker import (
        classify_market_category,
        compute_category_performance,
    )
    from arb_scanner.backtesting.portfolio_calculator import calculate_portfolio
    from arb_scanner.backtesting.position_engine import reconstruct_positions
    from arb_scanner.backtesting.signal_comparator import (
        aggregate_by_alignment,
        compare_trades_to_signals,
    )
    from arb_scanner.flippening.category_keywords import DEFAULT_SPORT_KEYWORDS
    from arb_scanner.models.backtesting import ImportedTrade, TradeAction
    from arb_scanner.storage.backtesting_repository import BacktestingRepository
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.flippening_repository import FlippeningRepository

    async with Database(config.storage.database_url) as db:
        repo = BacktestingRepository(db.pool)
        flip_repo = FlippeningRepository(db.pool)

        trade_rows = await repo.get_trades(since=since, until=until)
        trades = [ImportedTrade(**{k: v for k, v in r.items() if k != "id"}) for r in trade_rows]
        positions = reconstruct_positions(trades)
        if category_filter:
            positions = [
                p
                for p in positions
                if classify_market_category(p.market_name, DEFAULT_SPORT_KEYWORDS)
                == category_filter
            ]

        summary = calculate_portfolio(positions)

        signals = await flip_repo.get_history(
            limit=500,
            category=category_filter,
        )
        buy_sells = [t for t in trades if t.action in (TradeAction.Buy, TradeAction.Sell)]
        comparisons = compare_trades_to_signals(buy_sells, signals)
        alignment_agg = aggregate_by_alignment(comparisons)

        cat_perfs = compute_category_performance(
            positions,
            comparisons,
            DEFAULT_SPORT_KEYWORDS,
        )
        for perf in cat_perfs:
            await repo.upsert_category_performance(perf)

        return {
            "portfolio": summary.model_dump(mode="json"),
            "signal_alignment": alignment_agg,
            "category_performance": [p.model_dump(mode="json") for p in cat_perfs],
        }


# ── Renderers ───────────────────────────────────────────────────────


def _render_import_dry_run(trades: list[Any], fmt: str) -> None:
    """Display dry-run import results."""
    if fmt == "json":
        sys.stdout.write(json.dumps({"parsed": len(trades)}) + "\n")
    else:
        sys.stdout.write(f"Dry run: {len(trades)} trade(s) parsed successfully.\n")


def _render_import_table(result: dict[str, int]) -> None:
    """Display import results as a summary."""
    sys.stdout.write("Import complete\n")
    sys.stdout.write(f"  Inserted:   {result['inserted']}\n")
    sys.stdout.write(f"  Duplicates: {result['duplicates']}\n")
    sys.stdout.write(f"  Errors:     {result['errors']}\n")


def _render_portfolio_table(data: dict[str, Any]) -> None:
    """Display portfolio summary and category performance."""
    p = data["portfolio"]
    sys.stdout.write("Portfolio Summary\n")
    sys.stdout.write("=" * 40 + "\n")
    sys.stdout.write(f"  Trades:       {p.get('trade_count', 0)}\n")
    sys.stdout.write(f"  Wins/Losses:  {p.get('win_count', 0)}/{p.get('loss_count', 0)}\n")
    sys.stdout.write(f"  Win Rate:     {float(p.get('win_rate', 0)):.1%}\n")
    sys.stdout.write(f"  Net P&L:      {float(p.get('net_pnl', 0)):+.2f}\n")
    sys.stdout.write(f"  ROI:          {float(p.get('roi', 0)):.1%}\n")
    sys.stdout.write(f"  Fees:         {float(p.get('total_fees', 0)):.2f}\n")

    cats = data.get("category_performance", [])
    if cats:
        sys.stdout.write("\nCategory Performance\n")
        sys.stdout.write("-" * 60 + "\n")
        hdr = f"{'Category':<16} {'Trades':>6} {'Win%':>6} {'PnL':>10} {'PF':>6}"
        sys.stdout.write(hdr + "\n")
        for c in cats:
            sys.stdout.write(
                f"{str(c.get('category', '')):<16} "
                f"{c.get('trade_count', 0):>6} "
                f"{float(c.get('win_rate', 0)):>5.1%} "
                f"{float(c.get('total_pnl', 0)):>+10.2f} "
                f"{float(c.get('profit_factor', 0)):>6.2f}\n",
            )


def _render_backtest_report(data: dict[str, Any]) -> None:
    """Display full backtest report."""
    _render_portfolio_table(data)

    alignment = data.get("signal_alignment", {})
    if alignment:
        sys.stdout.write("\nSignal Alignment\n")
        sys.stdout.write("-" * 40 + "\n")
        for key in ("aligned", "contrary", "no_signal"):
            info = alignment.get(key, {})
            count = info.get("count", 0)
            avg = info.get("avg_pnl", 0)
            sys.stdout.write(f"  {key:<12} {count:>4} trades  avg_pnl={float(avg):+.4f}\n")


# ── Helpers ─────────────────────────────────────────────────────────


def _load_config() -> Any:
    """Load config, exiting on failure."""
    try:
        return load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc


def _parse_optional_range(
    since: str,
    until: str,
) -> tuple[Any, Any]:
    """Parse optional ISO 8601 time range, returning None for empty strings.

    Args:
        since: ISO 8601 start time or empty string.
        until: ISO 8601 end time or empty string.

    Returns:
        Tuple of (since_dt_or_None, until_dt_or_None).
    """
    from datetime import datetime

    since_dt = None
    until_dt = None
    if since.strip():
        try:
            since_dt = datetime.fromisoformat(since.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid --since: {since}") from exc
    if until.strip():
        try:
            until_dt = datetime.fromisoformat(until.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid --until: {until}") from exc
    return since_dt, until_dt
