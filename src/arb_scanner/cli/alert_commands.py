"""CLI commands for trend alert management."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
import typer

from arb_scanner.config.loader import load_config
from arb_scanner.models.analytics import TrendAlert

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="cli.alert_commands")


def register(app: typer.Typer) -> None:
    """Register alert commands on the Typer app.

    Args:
        app: The main CLI Typer application instance.
    """
    app.command()(alerts)


def alerts(
    last: int = typer.Option(20, "--last", help="Number of recent alerts to show"),
    alert_type: str | None = typer.Option(None, "--type", help="Filter by alert type"),
    output_format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """List recent trend alerts."""
    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    try:
        result = asyncio.run(_fetch_alerts(config, last, alert_type))
    except Exception as exc:
        logger.error("alerts_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if output_format == "json":
        _print_json(result)
    else:
        _print_table(result)


async def _fetch_alerts(config: Any, last: int, alert_type: str | None) -> list[TrendAlert]:
    """Fetch recent trend alerts from the database.

    Args:
        config: Application settings.
        last: Maximum number of alerts to return.
        alert_type: Optional filter by alert type value.

    Returns:
        List of TrendAlert models.
    """
    from arb_scanner.storage.analytics_repository import AnalyticsRepository
    from arb_scanner.storage.db import Database

    async with Database(config.storage.database_url) as db:
        repo = AnalyticsRepository(db.pool)
        return await repo.get_recent_alerts(limit=last, alert_type=alert_type)


def _print_json(alert_list: list[TrendAlert]) -> None:
    """Print alerts as JSON.

    Args:
        alert_list: List of TrendAlert models to serialize.
    """
    data: list[dict[str, Any]] = [
        {
            "alert_type": a.alert_type.value,
            "poly_event_id": a.poly_event_id,
            "kalshi_event_id": a.kalshi_event_id,
            "spread_before": (str(a.spread_before) if a.spread_before is not None else None),
            "spread_after": (str(a.spread_after) if a.spread_after is not None else None),
            "message": a.message,
            "dispatched_at": a.dispatched_at.isoformat(),
        }
        for a in alert_list
    ]
    typer.echo(json.dumps(data, indent=2))


def _print_table(alert_list: list[TrendAlert]) -> None:
    """Print alerts as a formatted table.

    Args:
        alert_list: List of TrendAlert models to render.
    """
    from arb_scanner.notifications.reporter import format_alerts_table

    typer.echo(format_alerts_table(alert_list))
