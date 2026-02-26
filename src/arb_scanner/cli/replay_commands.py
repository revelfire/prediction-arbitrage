"""CLI commands for flippening replay, evaluation, sweep, and tick pruning."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import structlog
import typer

from arb_scanner.config.loader import load_config

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="cli.replay",
)


def register(app: typer.Typer) -> None:
    """Register replay commands on the main Typer app.

    Args:
        app: The main CLI Typer application instance.
    """
    app.command(name="flip-replay")(flip_replay)
    app.command(name="flip-evaluate")(flip_evaluate)
    app.command(name="flip-sweep")(flip_sweep)
    app.command(name="flip-tick-prune")(flip_tick_prune)


def flip_replay(
    market_id: str = typer.Option("", "--market-id", help="Specific market to replay."),
    sport: str = typer.Option("", "--sport", help="Replay all markets for a sport."),
    category: str = typer.Option("", "--category", help="Replay all markets for a category."),
    since: str = typer.Option("", "--since", help="Start time (ISO 8601)."),
    until: str = typer.Option("", "--until", help="End time (ISO 8601)."),
    override: list[str] = typer.Option([], "--override", help="Config overrides (key=value)."),
    fmt: str = typer.Option("table", "--format", help="Output format: table or json."),
) -> None:
    """Replay stored ticks through spike/signal pipeline."""
    cat_val = category.strip().lower() or sport.strip().lower() or ""
    if not market_id and not cat_val:
        raise typer.BadParameter("Provide --market-id, --sport, or --category.")

    from arb_scanner.cli._replay_helpers import (
        parse_overrides,
        render_replay_table,
        run_replay,
    )

    config = _load_config_or_exit()
    since_dt, until_dt = _parse_time_range(since, until)
    overrides = parse_overrides(override) if override else None

    try:
        signals = asyncio.run(
            run_replay(
                config,
                market_id.strip() or None,
                cat_val or None,
                since_dt,
                until_dt,
                overrides,
            )
        )
    except Exception as exc:
        logger.error("flip_replay_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        sys.stdout.write(json.dumps(signals, indent=2, default=str) + "\n")
    else:
        render_replay_table(signals)


def flip_evaluate(
    sport: str = typer.Option("", "--sport", help="Sport to evaluate."),
    category: str = typer.Option("", "--category", help="Category to evaluate."),
    since: str = typer.Option("", "--since", help="Start time (ISO 8601)."),
    until: str = typer.Option("", "--until", help="End time (ISO 8601)."),
    fmt: str = typer.Option("table", "--format", help="Output format: table or json."),
) -> None:
    """Evaluate replay results for a category."""
    cat_val = category.strip().lower() or sport.strip().lower()
    if not cat_val:
        raise typer.BadParameter("Provide --sport or --category.")

    from arb_scanner.cli._replay_helpers import render_evaluate_table, run_evaluate

    config = _load_config_or_exit()
    since_dt, until_dt = _parse_time_range(since, until)

    try:
        evaluation = asyncio.run(run_evaluate(config, cat_val, since_dt, until_dt))
    except Exception as exc:
        logger.error("flip_evaluate_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        sys.stdout.write(json.dumps(evaluation, indent=2, default=str) + "\n")
    else:
        render_evaluate_table(evaluation)


def flip_sweep(
    param: str = typer.Option(..., "--param", help="Config field to sweep."),
    min_val: float = typer.Option(..., "--min", help="Minimum value."),
    max_val: float = typer.Option(..., "--max", help="Maximum value."),
    step: float = typer.Option(..., "--step", help="Step size."),
    sport: str = typer.Option("", "--sport", help="Sport to sweep."),
    category: str = typer.Option("", "--category", help="Category to sweep."),
    since: str = typer.Option("", "--since", help="Start time (ISO 8601)."),
    until: str = typer.Option("", "--until", help="End time (ISO 8601)."),
    fmt: str = typer.Option("table", "--format", help="Output format: table or json."),
) -> None:
    """Sweep a config parameter and evaluate each value."""
    cat_val = category.strip().lower() or sport.strip().lower()
    if not cat_val:
        raise typer.BadParameter("Provide --sport or --category.")

    from arb_scanner.cli._replay_helpers import render_sweep_table, run_sweep

    config = _load_config_or_exit()
    since_dt, until_dt = _parse_time_range(since, until)

    try:
        result = asyncio.run(
            run_sweep(
                config,
                cat_val,
                since_dt,
                until_dt,
                param.strip(),
                min_val,
                max_val,
                step,
            )
        )
    except Exception as exc:
        logger.error("flip_sweep_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        sys.stdout.write(json.dumps(result, indent=2, default=str) + "\n")
    else:
        render_sweep_table(result)


def flip_tick_prune(
    days: int = typer.Option(0, "--days", help="Retention days (0 = use config)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show count without deleting."),
) -> None:
    """Delete ticks older than the retention window."""
    from arb_scanner.cli._replay_helpers import run_prune

    config = _load_config_or_exit()
    retention = days if days > 0 else None

    try:
        result = asyncio.run(run_prune(config, retention, dry_run))
    except Exception as exc:
        logger.error("flip_tick_prune_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if dry_run:
        sys.stdout.write(
            f"Would prune ticks before {result['cutoff']} ({result['days']} day retention)\n",
        )
    else:
        sys.stdout.write(f"Pruned {result.get('deleted', 0)} ticks.\n")


def _load_config_or_exit() -> Any:
    """Load config, exiting on failure."""
    try:
        return load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc


def _parse_time_range(
    since: str,
    until: str,
) -> tuple[Any, Any]:
    """Parse ISO 8601 time range strings."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(tz=UTC)
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid --since: {since}") from exc
    else:
        since_dt = now - timedelta(hours=24)

    if until:
        try:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid --until: {until}") from exc
    else:
        until_dt = now

    return since_dt, until_dt
