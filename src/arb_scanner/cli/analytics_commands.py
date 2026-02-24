"""CLI commands for analytics: history and stats."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
import typer

from arb_scanner.config.loader import load_config

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="cli.analytics")


def register(app: typer.Typer) -> None:
    """Register analytics commands on the main Typer app.

    Args:
        app: The main CLI Typer application instance.
    """
    app.command()(history)
    app.command()(stats)


def history(
    pair: str = typer.Option(..., help="Pair ID: POLY_EVENT_ID/KALSHI_EVENT_ID"),
    hours: int = typer.Option(24, help="Time window in hours"),
    fmt: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Show spread history for a specific market pair."""
    parts = pair.split("/")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise typer.BadParameter(
            "Pair must be in format POLY_EVENT_ID/KALSHI_EVENT_ID (separated by '/')."
        )
    poly_id, kalshi_id = parts[0].strip(), parts[1].strip()
    since = datetime.now(tz=timezone.utc) - timedelta(hours=hours)

    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    try:
        snapshots = asyncio.run(_fetch_history(config, poly_id, kalshi_id, since))
    except Exception as exc:
        logger.error("history_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        data = [s.model_dump(mode="json") for s in snapshots]
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    else:
        from arb_scanner.notifications.reporter import format_spread_history, write_output

        label = f"{poly_id} / {kalshi_id}"
        write_output(format_spread_history(label, snapshots))


async def _fetch_history(config: Any, poly_id: str, kalshi_id: str, since: datetime) -> Any:
    """Fetch spread history data from the database.

    Args:
        config: Application settings.
        poly_id: Polymarket event ID.
        kalshi_id: Kalshi event ID.
        since: Start of the time window.

    Returns:
        List of SpreadSnapshot models.
    """
    from arb_scanner.storage.analytics_repository import AnalyticsRepository
    from arb_scanner.storage.db import Database

    async with Database(config.storage.database_url) as db:
        repo = AnalyticsRepository(db.pool)
        return await repo.get_spread_history(poly_id, kalshi_id, since)


def stats(
    hours: int = typer.Option(24, help="Time window in hours"),
    top: int = typer.Option(10, help="Number of top pairs to show"),
    fmt: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Show aggregated statistics and scanner health."""
    since = datetime.now(tz=timezone.utc) - timedelta(hours=hours)

    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    try:
        summaries, health = asyncio.run(_fetch_stats(config, since))
    except Exception as exc:
        logger.error("stats_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        data = {
            "pairs": [s.model_dump(mode="json") for s in summaries[:top]],
            "health": [h.model_dump(mode="json") for h in health],
        }
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    else:
        from arb_scanner.notifications.reporter import format_stats_report, write_output

        write_output(format_stats_report(summaries, health, top_n=top))


async def _fetch_stats(config: Any, since: datetime) -> Any:
    """Fetch pair summaries and scan health from the database.

    Args:
        config: Application settings.
        since: Start of the time window.

    Returns:
        Tuple of (summaries, health).
    """
    from arb_scanner.storage.analytics_repository import AnalyticsRepository
    from arb_scanner.storage.db import Database

    async with Database(config.storage.database_url) as db:
        repo = AnalyticsRepository(db.pool)
        summaries = await repo.get_pair_summaries(since)
        health = await repo.get_scan_health(since)
        return summaries, health
