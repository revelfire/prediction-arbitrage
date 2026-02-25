"""CLI commands for the flippening engine."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import structlog
import typer

from arb_scanner.config.loader import load_config

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="cli.flippening",
)


def register(app: typer.Typer) -> None:
    """Register flippening commands on the main Typer app.

    Args:
        app: The main CLI Typer application instance.
    """
    app.command(name="flip-watch")(flip_watch)
    app.command(name="flip-history")(flip_history)
    app.command(name="flip-stats")(flip_stats)


def flip_watch(
    sports: str = typer.Option(
        "",
        "--sports",
        help="Comma-separated sport filter (e.g. nba,nhl).",
    ),
    min_confidence: float = typer.Option(
        0.0,
        "--min-confidence",
        help="Override minimum confidence threshold (0.0-1.0).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Run without persistence or alerts.",
    ),
) -> None:
    """Watch live sports markets for flippening opportunities."""
    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if min_confidence > 0:
        config.flippening.min_confidence = min_confidence

    sport_filter = [s.strip().lower() for s in sports.split(",") if s.strip()] if sports else None

    from arb_scanner.flippening.orchestrator import run_flip_watch

    try:
        asyncio.run(
            run_flip_watch(config, dry_run=dry_run, sport_filter=sport_filter),
        )
    except KeyboardInterrupt:
        logger.info("flip_watch_interrupted")


def flip_history(
    last: int = typer.Option(20, "--last", help="Number of records."),
    sport: str = typer.Option("", "--sport", help="Filter by sport."),
    fmt: str = typer.Option(
        "table",
        "--format",
        help="Output format: table or json.",
    ),
) -> None:
    """Show flippening signal history."""
    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    sport_val = sport.strip().lower() or None

    try:
        rows = asyncio.run(_fetch_history(config, last, sport_val))
    except Exception as exc:
        logger.error("flip_history_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        sys.stdout.write(json.dumps(rows, indent=2, default=str) + "\n")
    else:
        _render_history_table(rows)


def flip_stats(
    sport: str = typer.Option("", "--sport", help="Filter by sport."),
    since: str = typer.Option(
        "",
        "--since",
        help="ISO 8601 start date.",
    ),
) -> None:
    """Show aggregated flippening statistics."""
    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    sport_val = sport.strip().lower() or None
    since_dt = None
    if since:
        from datetime import datetime

        try:
            since_dt = datetime.fromisoformat(
                since.replace("Z", "+00:00"),
            )
        except ValueError as exc:
            raise typer.BadParameter(
                f"Invalid ISO 8601 date: {since}",
            ) from exc

    try:
        data = asyncio.run(_fetch_stats(config, sport_val, since_dt))
    except Exception as exc:
        logger.error("flip_stats_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    _render_stats(data)


async def _fetch_history(
    config: Any,
    limit: int,
    sport: str | None,
) -> list[dict[str, Any]]:
    """Fetch flippening history from the database.

    Args:
        config: Application settings.
        limit: Max rows.
        sport: Optional sport filter.

    Returns:
        List of history records.
    """
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.flippening_repository import (
        FlippeningRepository,
    )

    async with Database(config.storage.database_url) as db:
        repo = FlippeningRepository(db.pool)
        return await repo.get_history(limit=limit, sport=sport)


async def _fetch_stats(
    config: Any,
    sport: str | None,
    since: Any,
) -> list[dict[str, Any]]:
    """Fetch flippening stats from the database.

    Args:
        config: Application settings.
        sport: Optional sport filter.
        since: Optional start datetime.

    Returns:
        Stats dictionary.
    """
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.flippening_repository import (
        FlippeningRepository,
    )

    async with Database(config.storage.database_url) as db:
        repo = FlippeningRepository(db.pool)
        return await repo.get_stats(sport=sport, since=since)


def _render_history_table(rows: list[dict[str, Any]]) -> None:
    """Render history as a text table.

    Args:
        rows: History records.
    """
    if not rows:
        sys.stdout.write("No flippening history found.\n")
        return
    header = f"{'Sport':<6} {'Side':<4} {'Entry':>7} {'Exit':>7} {'P&L':>8} {'Hold':>6}"
    sys.stdout.write(header + "\n")
    sys.stdout.write("-" * len(header) + "\n")
    for row in rows:
        sport = str(row.get("sport", ""))[:6]
        side = str(row.get("side", ""))[:4]
        entry = f"{float(row.get('entry_price', 0)):.2f}"
        exit_p = f"{float(row.get('exit_price', 0)):.2f}"
        pnl = f"{float(row.get('realized_pnl', 0)):+.2f}"
        hold = f"{float(row.get('hold_minutes', 0)):.0f}m"
        sys.stdout.write(
            f"{sport:<6} {side:<4} {entry:>7} {exit_p:>7} {pnl:>8} {hold:>6}\n",
        )


def _render_stats(rows: list[dict[str, Any]]) -> None:
    """Render stats summary.

    Args:
        rows: List of per-sport stats dictionaries.
    """
    if not rows:
        sys.stdout.write("No flippening stats found.\n")
        return
    sys.stdout.write("Flippening Stats\n")
    sys.stdout.write("=" * 40 + "\n")
    for row in rows:
        sport = row.get("sport", "all")
        sys.stdout.write(f"\n  Sport:   {sport}\n")
        sys.stdout.write(f"  Signals: {row.get('total', 0)}\n")
        win_rate = row.get("win_rate", 0)
        sys.stdout.write(f"  Win rate: {float(win_rate):.1%}\n")
        avg_pnl = row.get("avg_pnl", 0)
        sys.stdout.write(f"  Avg P&L:  {float(avg_pnl):+.4f}\n")
        avg_hold = row.get("avg_hold", 0)
        sys.stdout.write(f"  Avg hold: {float(avg_hold):.0f} min\n")
