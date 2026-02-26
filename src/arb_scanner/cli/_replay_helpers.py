"""Async helpers and renderers for replay CLI commands."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from arb_scanner.flippening.replay_engine import ReplayEngine
from arb_scanner.flippening.replay_evaluator import evaluate_replay, sweep_parameter
from arb_scanner.models.config import Settings
from arb_scanner.storage.tick_repository import TickRepository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="cli.replay_helpers",
)


async def create_replay_engine(
    config: Settings,
) -> tuple[ReplayEngine, Any]:
    """Create a ReplayEngine with DB connection.

    Args:
        config: Application settings.

    Returns:
        Tuple of (ReplayEngine, Database) — caller should close db.
    """
    from arb_scanner.storage.db import Database

    db = Database(config.storage.database_url)
    await db.connect()
    tick_repo = TickRepository(db.pool)
    engine = ReplayEngine(tick_repo, config.flippening)
    return engine, db


async def run_replay(
    config: Settings,
    market_id: str | None,
    sport: str | None,
    since: datetime,
    until: datetime,
    overrides: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Run replay and return serialized results.

    Args:
        config: Application settings.
        market_id: Specific market to replay (or None).
        sport: Sport to replay all markets (or None).
        since: Start timestamp.
        until: End timestamp.
        overrides: Config overrides.

    Returns:
        List of signal dicts.
    """
    engine, db = await create_replay_engine(config)
    try:
        if market_id:
            signals = await engine.replay_market(
                market_id,
                since,
                until,
                overrides,
            )
        else:
            signals = await engine.replay_sport(
                sport or "",
                since,
                until,
                overrides,
            )
        return [s.model_dump(mode="json") for s in signals]
    finally:
        await db.disconnect()


async def run_evaluate(
    config: Settings,
    sport: str,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """Replay + evaluate for a sport.

    Args:
        config: Application settings.
        sport: Sport to evaluate.
        since: Start timestamp.
        until: End timestamp.

    Returns:
        Evaluation dict.
    """
    engine, db = await create_replay_engine(config)
    try:
        signals = await engine.replay_sport(sport, since, until)
        evaluation = evaluate_replay(signals)
        return evaluation.model_dump(mode="json")
    finally:
        await db.disconnect()


async def run_sweep(
    config: Settings,
    sport: str,
    since: datetime,
    until: datetime,
    param: str,
    min_val: float,
    max_val: float,
    step: float,
) -> dict[str, Any]:
    """Run parameter sweep for a sport.

    Args:
        config: Application settings.
        sport: Sport to sweep.
        since: Start timestamp.
        until: End timestamp.
        param: Config parameter name.
        min_val: Minimum value.
        max_val: Maximum value.
        step: Step size.

    Returns:
        Sweep result dict.
    """
    engine, db = await create_replay_engine(config)
    try:
        result = await sweep_parameter(
            engine,
            sport,
            since,
            until,
            param,
            min_val,
            max_val,
            step,
        )
        return result.model_dump(mode="json")
    finally:
        await db.disconnect()


async def run_prune(
    config: Settings,
    days: int | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Prune old ticks.

    Args:
        config: Application settings.
        days: Retention days (None = use config default).
        dry_run: If True, report count without deleting.

    Returns:
        Dict with deleted count.
    """
    from arb_scanner.storage.db import Database

    retention = days or config.flippening.tick_retention_days
    cutoff = datetime.now(tz=UTC) - timedelta(days=retention)
    db = Database(config.storage.database_url)
    await db.connect()
    try:
        tick_repo = TickRepository(db.pool)
        if dry_run:
            # For dry run, we just report the cutoff
            return {"cutoff": cutoff.isoformat(), "days": retention, "dry_run": True}
        count = await tick_repo.prune_ticks(cutoff)
        return {"deleted": count, "cutoff": cutoff.isoformat(), "days": retention}
    finally:
        await db.disconnect()


def parse_overrides(override_strs: list[str]) -> dict[str, Any]:
    """Parse key=value override strings into a dict.

    Args:
        override_strs: List of "key=value" strings.

    Returns:
        Dict with parsed values (numeric strings coerced to float).
    """
    result: dict[str, Any] = {}
    for item in override_strs:
        if "=" not in item:
            continue
        key, val = item.split("=", 1)
        key = key.strip()
        val = val.strip()
        try:
            result[key] = float(val)
        except ValueError:
            result[key] = val
    return result


def render_replay_table(signals: list[dict[str, Any]]) -> None:
    """Render replay signals as a text table.

    Args:
        signals: List of signal dicts.
    """
    if not signals:
        sys.stdout.write("No signals produced.\n")
        return
    hdr = (
        f"{'Market':<14} {'Side':<4} {'Entry':>7} {'Exit':>7} {'P&L':>8} {'Hold':>6} {'Reason':<10}"
    )
    sys.stdout.write(hdr + "\n")
    sys.stdout.write("-" * len(hdr) + "\n")
    for s in signals:
        mid = str(s.get("market_id", ""))[:14]
        side = str(s.get("side", ""))[:4]
        entry = f"{float(s.get('entry_price', 0)):.4f}"
        exit_p = f"{float(s.get('exit_price', 0)):.4f}"
        pnl = f"{float(s.get('realized_pnl', 0)):+.4f}"
        hold = f"{float(s.get('hold_minutes', 0)):.0f}m"
        reason = str(s.get("exit_reason", ""))[:10]
        sys.stdout.write(
            f"{mid:<14} {side:<4} {entry:>7} {exit_p:>7} {pnl:>8} {hold:>6} {reason:<10}\n",
        )


def render_evaluate_table(evaluation: dict[str, Any]) -> None:
    """Render evaluation summary.

    Args:
        evaluation: Evaluation dict.
    """
    sys.stdout.write("Replay Evaluation\n")
    sys.stdout.write("=" * 35 + "\n")
    sys.stdout.write(f"  Signals:        {evaluation.get('total_signals', 0)}\n")
    sys.stdout.write(f"  Wins:           {evaluation.get('win_count', 0)}\n")
    wr = evaluation.get("win_rate", 0)
    sys.stdout.write(f"  Win Rate:       {float(wr):.1%}\n")
    sys.stdout.write(f"  Avg P&L:        {float(evaluation.get('avg_pnl', 0)):+.6f}\n")
    sys.stdout.write(f"  Avg Hold:       {float(evaluation.get('avg_hold_minutes', 0)):.1f} min\n")
    sys.stdout.write(f"  Max Drawdown:   {float(evaluation.get('max_drawdown', 0)):.6f}\n")
    sys.stdout.write(f"  Profit Factor:  {float(evaluation.get('profit_factor', 0)):.2f}\n")


def render_sweep_table(sweep: dict[str, Any]) -> None:
    """Render parameter sweep grid.

    Args:
        sweep: Sweep result dict.
    """
    param = sweep.get("param_name", "param")
    results = sweep.get("results", [])
    if not results:
        sys.stdout.write("No sweep results.\n")
        return
    hdr = f"{param:>10} | {'Signals':>7} | {'Win%':>6} | {'Avg P&L':>10} | {'PF':>6}"
    sys.stdout.write(hdr + "\n")
    sys.stdout.write("-" * len(hdr) + "\n")
    for val, evl in results:
        n = evl.get("total_signals", 0) if isinstance(evl, dict) else evl.total_signals
        wr = evl.get("win_rate", 0) if isinstance(evl, dict) else evl.win_rate
        ap = evl.get("avg_pnl", 0) if isinstance(evl, dict) else evl.avg_pnl
        pf = evl.get("profit_factor", 0) if isinstance(evl, dict) else evl.profit_factor
        sys.stdout.write(
            f"{val:>10.4f} | {n:>7} | {float(wr):>5.1%} | {float(ap):>+10.6f} | {float(pf):>6.2f}\n",
        )
