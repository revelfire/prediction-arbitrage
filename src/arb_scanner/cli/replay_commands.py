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
    persist: bool = typer.Option(False, "--persist", help="Save best result to DB."),
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

    if persist:
        _persist_best_param(config, result, cat_val, param.strip())


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


def _persist_best_param(
    config: Any,
    sweep_result: dict[str, Any],
    category: str,
    param_name: str,
) -> None:
    """Save the best sweep result to the database.

    Args:
        config: Application settings.
        sweep_result: Sweep result dict with 'results' list.
        category: Category that was swept.
        param_name: Parameter that was swept.
    """
    from datetime import UTC, datetime

    from arb_scanner.models.backtesting import OptimalParamSnapshot

    results = sweep_result.get("results", [])
    if not results:
        sys.stdout.write("No results to persist.\n")
        return

    best_val, best_eval = max(
        results,
        key=lambda r: r[1].get("win_rate", 0) if isinstance(r[1], dict) else r[1].win_rate,
    )
    win_rate = best_eval.get("win_rate", 0) if isinstance(best_eval, dict) else best_eval.win_rate

    snapshot = OptimalParamSnapshot(
        category=category,
        param_name=param_name,
        optimal_value=float(best_val),
        win_rate_at_optimal=float(win_rate),
        sweep_date=datetime.now(tz=UTC),
    )

    async def _save() -> None:
        from arb_scanner.storage.backtesting_repository import BacktestingRepository
        from arb_scanner.storage.db import Database

        db = Database(config.storage.database_url)
        await db.connect()
        try:
            repo = BacktestingRepository(db.pool)
            await repo.upsert_optimal_param(snapshot)
        finally:
            await db.disconnect()

    try:
        asyncio.run(_save())
        sys.stdout.write(
            f"Persisted optimal {param_name}={best_val:.4f}"
            f" (win_rate={float(win_rate):.1%}) for {category}\n",
        )
    except Exception as exc:
        logger.error("persist_optimal_param_failed", error=str(exc))
        sys.stdout.write(f"Failed to persist: {exc}\n")


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
