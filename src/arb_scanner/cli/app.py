"""Typer CLI application for the arb scanner."""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from typing import Any

import structlog
import typer

from arb_scanner.cli._helpers import (
    determine_exit_code,
    format_report_markdown,
    load_config_safe,
    parse_iso_datetime,
    render_output,
)
from arb_scanner.cli.orchestrator import run_scan
from arb_scanner.config.loader import load_config

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="cli.app")

app = typer.Typer(
    name="arb-scanner",
    add_completion=False,
    help="Cross-venue arbitrage scanner for Polymarket and Kalshi prediction markets.",
)

# Register analytics commands (history, stats) from separate module.
from arb_scanner.cli import analytics_commands as _analytics  # noqa: E402

_analytics.register(app)

# Register alert commands (alerts) from separate module.
from arb_scanner.cli import alert_commands as _alerts  # noqa: E402

_alerts.register(app)

# Register flippening commands (flip-watch, flip-history, flip-stats) from separate module.
from arb_scanner.cli import flippening_commands as _flippening  # noqa: E402

_flippening.register(app)


@app.command()
def scan(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Use test fixtures instead of live venue APIs."
    ),
    min_spread: float = typer.Option(
        0.0, "--min-spread", help="Minimum net spread percentage to report."
    ),
    output: str = typer.Option("json", "--output", help="Output format: 'json' or 'table'."),
) -> None:
    """Run a single scan cycle: ingest, match, calculate, and output opportunities."""
    try:
        config = load_config_safe(dry_run)
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if min_spread > 0:
        from decimal import Decimal

        config.arb_thresholds.min_net_spread_pct = Decimal(str(min_spread))

    try:
        result = asyncio.run(run_scan(config, dry_run=dry_run))
    except Exception as exc:
        logger.error("scan_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    exit_code = determine_exit_code(result)
    render_output(result, output)
    raise typer.Exit(code=exit_code)


@app.command()
def watch(
    interval: int = typer.Option(60, "--interval", help="Seconds between scan cycles."),
    min_spread: float = typer.Option(
        0.02, "--min-spread", help="Minimum spread percentage to trigger webhook alerts."
    ),
) -> None:
    """Continuous polling loop with webhook alerts. Ctrl+C for graceful shutdown."""
    from decimal import Decimal

    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    config.scanning.interval_seconds = interval
    config.notifications.min_spread_to_notify_pct = Decimal(str(min_spread))

    try:
        asyncio.run(_run_watch_with_signals(config))
    except KeyboardInterrupt:
        logger.info("watch_interrupted")


async def _run_watch_with_signals(config: Any) -> None:
    """Set up signal handlers, init arb pipeline, and run the watch loop."""
    from arb_scanner.cli.watch import run_watch

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    db = await _maybe_init_arb_pipeline(config)
    logger.info("watch_started", interval=config.scanning.interval_seconds)
    try:
        await run_watch(config, stop_event)
    finally:
        if db is not None:
            await db.disconnect()
    logger.info("watch_stopped")


async def _maybe_init_arb_pipeline(config: Any) -> Any:
    """Initialise arb auto-execution pipeline for the watch loop.

    Returns the Database instance (for cleanup) or None if skipped.
    """
    ac = config.auto_execution
    if not ac.enabled or ac.mode == "off":
        logger.info("arb_pipeline_skipped", reason="auto_execution_disabled")
        return None
    try:
        from arb_scanner.execution.arb_critic import ArbTradeCritic
        from arb_scanner.execution.arb_pipeline import ArbAutoExecutionPipeline
        from arb_scanner.execution.capital_manager import CapitalManager
        from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
        from arb_scanner.execution.kalshi_executor import KalshiExecutor
        from arb_scanner.execution.polymarket_executor import PolymarketExecutor
        from arb_scanner.storage.auto_exec_repository import AutoExecRepository
        from arb_scanner.storage.db import Database

        db = Database(config.database.url)
        await db.connect()
        pool = db.pool
        auto_repo = AutoExecRepository(pool)
        poly = PolymarketExecutor(config.execution.polymarket)
        kalshi = KalshiExecutor(config.execution.kalshi)
        capital = CapitalManager(
            config.execution, poly.get_balance, kalshi.get_balance,
        )
        breakers = CircuitBreakerManager(ac)
        critic = ArbTradeCritic(ac.critic, config.claude)
        from arb_scanner.execution.orchestrator import ExecutionOrchestrator
        from arb_scanner.storage.execution_repository import ExecutionRepository
        from arb_scanner.storage.ticket_repository import TicketRepository

        exec_repo = ExecutionRepository(pool)
        ticket_repo = TicketRepository(pool)
        orch = ExecutionOrchestrator(
            config=config.execution,
            capital=capital,
            poly=poly,
            kalshi=kalshi,
            exec_repo=exec_repo,
            ticket_repo=ticket_repo,
        )
        pipeline = ArbAutoExecutionPipeline(
            config=config,
            auto_config=ac,
            orchestrator=orch,
            critic=critic,
            breakers=breakers,
            capital=capital,
            poly=poly,
            kalshi=kalshi,
            auto_repo=auto_repo,
        )
        object.__setattr__(config, "_arb_pipeline", pipeline)
        logger.info("arb_pipeline_initialised", mode=str(ac.mode))
        return db
    except Exception:
        logger.exception("arb_pipeline_init_failed")
        return None


@app.command()
def report(
    last: int = typer.Option(10, "--last", help="Number of recent opportunities to include."),
    fmt: str = typer.Option("markdown", "--format", help="Output format: 'markdown' or 'json'."),
    since: str | None = typer.Option(None, "--since", help="ISO 8601 start date (inclusive)."),
    until: str | None = typer.Option(None, "--until", help="ISO 8601 end date (exclusive)."),
) -> None:
    """Generate a Markdown report of recent opportunities and execution tickets."""
    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    try:
        if since is not None:
            since_dt = parse_iso_datetime(since)
            until_dt = parse_iso_datetime(until) if until else None
            data = asyncio.run(_fetch_report_data_range(config, since_dt, until_dt, last))
        else:
            data = asyncio.run(_fetch_report_data(config, last))
    except typer.BadParameter:
        raise
    except Exception as exc:
        logger.error("report_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    else:
        from arb_scanner.notifications.reporter import format_tickets_table, write_output

        write_output(format_tickets_table(data["tickets"]))
        write_output(format_report_markdown(data["opportunities"]))


async def _fetch_report_data(
    config: Any,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch opportunities and tickets from the database."""
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.repository import Repository

    async with Database(config.storage.database_url) as db:
        repo = Repository(db.pool)
        opps = await repo.get_recent_opportunities(limit)
        tickets = await repo.get_tickets_with_opportunities(limit)
        return {"opportunities": opps, "tickets": tickets}


async def _fetch_report_data_range(
    config: Any,
    since: Any,
    until: Any,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch opportunities and tickets within a date range."""
    from arb_scanner.storage.analytics_repository import AnalyticsRepository
    from arb_scanner.storage.db import Database

    async with Database(config.storage.database_url) as db:
        repo = AnalyticsRepository(db.pool)
        opps = await repo.get_opportunities_date_range(since, until, limit)
        tickets = await repo.get_tickets_date_range(since, until, limit)
        return {"opportunities": opps, "tickets": tickets}


@app.command(name="match-audit")
def match_audit(
    include_expired: bool = typer.Option(
        False,
        "--include-expired",
        help="Show expired cache entries alongside active ones.",
    ),
    min_confidence: float = typer.Option(
        0.0,
        "--min-confidence",
        help="Filter matches below this confidence threshold (0.0-1.0).",
    ),
    since: str | None = typer.Option(None, "--since", help="ISO 8601 start date filter."),
) -> None:
    """Dump all cached contract matches for review and auditing."""
    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    try:
        if since is not None:
            since_dt = parse_iso_datetime(since)
            matches = asyncio.run(
                _fetch_match_data_range(config, since_dt, include_expired, min_confidence),
            )
        else:
            matches = asyncio.run(
                _fetch_match_data(config, include_expired, min_confidence),
            )
    except typer.BadParameter:
        raise
    except Exception as exc:
        logger.error("match_audit_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    from arb_scanner.notifications.reporter import format_matches_table, write_output

    write_output(format_matches_table(matches))


async def _fetch_match_data(
    config: Any,
    include_expired: bool,
    min_confidence: float,
) -> list[dict[str, Any]]:
    """Fetch match result data from the database."""
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.repository import Repository

    async with Database(config.storage.database_url) as db:
        repo = Repository(db.pool)
        return await repo.get_all_matches(
            include_expired=include_expired,
            min_confidence=min_confidence,
        )


async def _fetch_match_data_range(
    config: Any,
    since: Any,
    include_expired: bool,
    min_confidence: float,
) -> list[dict[str, Any]]:
    """Fetch match results within a date range."""
    from arb_scanner.storage.analytics_repository import AnalyticsRepository
    from arb_scanner.storage.db import Database

    async with Database(config.storage.database_url) as db:
        repo = AnalyticsRepository(db.pool)
        return await repo.get_matches_date_range(
            since=since,
            include_expired=include_expired,
            min_confidence=min_confidence,
        )


@app.command()
def migrate() -> None:
    """Apply all pending SQL migrations to the database."""
    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    try:
        applied = asyncio.run(_run_migrate(config))
    except Exception as exc:
        logger.error("migration_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if applied:
        for name in applied:
            logger.info("migration_applied", filename=name)
        sys.stdout.write(f"Applied {len(applied)} migration(s).\n")
    else:
        sys.stdout.write("No pending migrations.\n")


async def _run_migrate(config: Any) -> list[str]:
    """Run database migrations using the migrations runner."""
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.migrations_runner import run_migrations

    async with Database(config.storage.database_url) as db:
        return await run_migrations(db.pool)


@app.command(name="ticket-prune")
def ticket_prune(
    days: int | None = typer.Option(
        None, "--days", help="Delete terminal tickets older than N days (default: config)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be deleted without deleting."
    ),
) -> None:
    """Delete expired/executed/cancelled tickets older than retention threshold."""
    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    try:
        result = asyncio.run(_run_ticket_prune(config, days, dry_run))
    except Exception as exc:
        logger.error("ticket_prune_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if dry_run:
        retention = days if days is not None else config.ticket_lifecycle.retention_days
        sys.stdout.write(
            f"Dry run: would delete terminal tickets older than {retention} days "
            f"(before {result['cutoff']}).\n"
        )
    else:
        sys.stdout.write(f"Deleted {result['deleted']} terminal ticket(s).\n")


async def _run_ticket_prune(
    config: Any,
    days: int | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Run ticket pruning.

    Args:
        config: Application settings.
        days: Retention days override (None = use config default).
        dry_run: Report only, don't delete.

    Returns:
        Dict with deleted count or dry-run info.
    """
    from datetime import UTC, datetime, timedelta

    from arb_scanner.storage.db import Database
    from arb_scanner.storage.ticket_repository import TicketRepository

    retention = days if days is not None else config.ticket_lifecycle.retention_days
    cutoff = datetime.now(tz=UTC) - timedelta(days=retention)
    async with Database(config.storage.database_url) as db:
        repo = TicketRepository(db.pool)
        if dry_run:
            return {"cutoff": cutoff.isoformat(), "days": retention, "dry_run": True}
        count = await repo.prune_tickets(cutoff)
        return {"deleted": count, "cutoff": cutoff.isoformat(), "days": retention}


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address for the dashboard server."),
    port: int = typer.Option(8060, "--port", help="Port for the dashboard server."),
    no_db: bool = typer.Option(False, "--no-db", help="Start without database (UI preview only)."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev mode)."),
    flip_watch: bool = typer.Option(
        False, "--flip-watch", help="Run flippening engine in-process."
    ),
) -> None:
    """Start the web dashboard and API server."""
    try:
        config = load_config_safe(no_db)
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    import uvicorn

    if reload:
        import os

        os.environ.setdefault("ARB_NO_DB", "1" if no_db else "0")
        os.environ.setdefault("ARB_FLIP_WATCH", "1" if flip_watch else "0")
        uvicorn.run(
            "arb_scanner.api.app:create_app_from_env",
            host=host,
            port=port,
            reload=True,
            reload_dirs=["src"],
            factory=True,
        )
    else:
        from arb_scanner.api.app import create_app

        api_app = create_app(config, no_db=no_db, flip_watch=flip_watch)
        uvicorn.run(api_app, host=host, port=port)
