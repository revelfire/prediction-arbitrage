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
    from arb_scanner.cli.replay_commands import register as register_replay

    app.command(name="flip-watch")(flip_watch)
    app.command(name="flip-history")(flip_history)
    app.command(name="flip-stats")(flip_stats)
    app.command(name="flip-discover")(flip_discover)
    app.command(name="flip-ws-validate")(flip_ws_validate)
    register_replay(app)


def flip_watch(
    categories: str = typer.Option(
        "",
        "--categories",
        help="Comma-separated category IDs to monitor.",
    ),
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
    auto_execute: bool = typer.Option(
        False,
        "--auto-execute",
        help="Enable autonomous execution pipeline.",
    ),
) -> None:
    """Watch live markets for flippening opportunities."""
    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if min_confidence > 0:
        config.flippening.min_confidence = min_confidence

    if auto_execute:
        config.auto_execution.enabled = True
        config.auto_execution.mode = "auto"
        logger.info("auto_execute_enabled_via_cli")

    category_filter = _build_category_filter(categories, sports, config)

    from arb_scanner.flippening.orchestrator import run_flip_watch

    try:
        asyncio.run(
            run_flip_watch(config, dry_run=dry_run, category_filter=category_filter),
        )
    except KeyboardInterrupt:
        logger.info("flip_watch_interrupted")


def flip_history(
    last: int = typer.Option(20, "--last", help="Number of records."),
    sport: str = typer.Option("", "--sport", help="Filter by sport."),
    category: str = typer.Option("", "--category", help="Filter by category."),
    category_type: str = typer.Option("", "--category-type", help="Filter by category type."),
    fmt: str = typer.Option("table", "--format", help="Output format: table or json."),
) -> None:
    """Show flippening signal history."""
    from arb_scanner.cli._flip_render_helpers import fetch_history, render_history_table

    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    cat_val = category.strip().lower() or sport.strip().lower() or None
    cat_type_val = category_type.strip().lower() or None

    try:
        rows = asyncio.run(fetch_history(config, last, cat_val, cat_type_val))
    except Exception as exc:
        logger.error("flip_history_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        sys.stdout.write(json.dumps(rows, indent=2, default=str) + "\n")
    else:
        render_history_table(rows)


def flip_stats(
    sport: str = typer.Option("", "--sport", help="Filter by sport."),
    category: str = typer.Option("", "--category", help="Filter by category."),
    category_type: str = typer.Option("", "--category-type", help="Filter by category type."),
    since: str = typer.Option("", "--since", help="ISO 8601 start date."),
) -> None:
    """Show aggregated flippening statistics."""
    from arb_scanner.cli._flip_render_helpers import fetch_stats, render_stats

    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    cat_val = category.strip().lower() or sport.strip().lower() or None
    cat_type_val = category_type.strip().lower() or None
    since_dt = None
    if since:
        from datetime import datetime

        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid ISO 8601 date: {since}") from exc

    try:
        data = asyncio.run(fetch_stats(config, cat_val, cat_type_val, since_dt))
    except Exception as exc:
        logger.error("flip_stats_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    render_stats(data)


def flip_discover(
    categories: str = typer.Option("", "--categories", help="Comma-separated category filter."),
    sports: str = typer.Option("", "--sports", help="Comma-separated sport filter."),
    verbose: bool = typer.Option(False, "--verbose", help="Show all matched markets."),
    fmt: str = typer.Option("table", "--format", help="Output format: table or json."),
) -> None:
    """One-shot market discovery diagnostic."""
    from arb_scanner.cli._flip_discover_helpers import render_discover_table, run_discover

    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    cat_filter = _build_category_filter(categories, sports, config)
    cat_ids = set(cat_filter) if cat_filter else set(config.flippening.categories.keys())
    filtered_categories = {k: v for k, v in config.flippening.categories.items() if k in cat_ids}

    try:
        result = asyncio.run(run_discover(config, filtered_categories))
    except Exception as exc:
        logger.error("flip_discover_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if fmt == "json":
        sys.stdout.write(json.dumps(result, indent=2, default=str) + "\n")
    else:
        render_discover_table(result, verbose=verbose)


def flip_ws_validate(
    tokens: str = typer.Option("", "--tokens", help="Comma-separated token IDs."),
    count: int = typer.Option(100, "--count", help="Max messages to capture."),
    timeout: int = typer.Option(60, "--timeout", help="Max seconds to wait."),
    fmt: str = typer.Option("table", "--format", help="Output format: table or json."),
    save: str = typer.Option("", "--save", help="Save raw messages as JSONL."),
) -> None:
    """Validate WebSocket message schema against parser expectations."""
    from arb_scanner.cli._ws_validate_helpers import (
        render_ws_validate_table,
        run_ws_validate,
        save_jsonl,
    )

    try:
        config = load_config()
    except Exception as exc:
        logger.error("config_load_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    token_ids = [t.strip() for t in tokens.split(",") if t.strip()] if tokens else None

    try:
        report = asyncio.run(
            run_ws_validate(config.flippening, token_ids, count, timeout, settings=config),
        )
    except Exception as exc:
        logger.error("ws_validate_failed", error=str(exc))
        raise typer.Exit(code=1) from exc

    if save:
        n = save_jsonl(report.get("raw_messages", []), save)
        sys.stdout.write(f"Saved {n} messages to {save}\n")

    report_out = {k: v for k, v in report.items() if k != "raw_messages"}
    if fmt == "json":
        sys.stdout.write(json.dumps(report_out, indent=2, default=str) + "\n")
    else:
        render_ws_validate_table(report_out)


def _build_category_filter(
    categories: str,
    sports: str,
    config: Any,
) -> list[str] | None:
    """Build a category filter list from CLI options.

    Args:
        categories: Comma-separated category IDs.
        sports: Comma-separated sport slugs.
        config: Application settings.

    Returns:
        List of category IDs to filter, or None for all.
    """
    if categories:
        return [c.strip().lower() for c in categories.split(",") if c.strip()]
    if sports:
        sport_ids = {s.strip().lower() for s in sports.split(",") if s.strip()}
        return [
            cid
            for cid, cfg in config.flippening.categories.items()
            if cfg.category_type == "sport" and cid in sport_ids
        ]
    return None
