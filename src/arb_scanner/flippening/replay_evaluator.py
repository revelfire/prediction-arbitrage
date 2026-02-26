"""Evaluation and parameter sweep for replay results."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from arb_scanner.flippening.replay_engine import ReplayEngine
from arb_scanner.models.flippening import ExitReason
from arb_scanner.models.replay import ReplayEvaluation, ReplaySignal, SweepResult

_MAX_PROFIT_FACTOR = 999.99


def evaluate_replay(
    signals: list[ReplaySignal],
    config_overrides: dict[str, Any] | None = None,
) -> ReplayEvaluation:
    """Compute aggregate metrics from replay signals.

    Args:
        signals: List of hypothetical signals from replay.
        config_overrides: Optional config overrides used.

    Returns:
        ReplayEvaluation with win rate, P&L, drawdown, etc.
    """
    total = len(signals)
    if total == 0:
        return ReplayEvaluation(
            total_signals=0,
            win_count=0,
            win_rate=0.0,
            avg_pnl=0.0,
            avg_hold_minutes=0.0,
            max_drawdown=0.0,
            profit_factor=0.0,
            config_overrides=config_overrides or {},
        )

    wins = [s for s in signals if s.exit_reason == ExitReason.REVERSION]
    win_count = len(wins)
    win_rate = win_count / total

    pnls = [float(s.realized_pnl) for s in signals]
    avg_pnl = sum(pnls) / total
    avg_hold = sum(float(s.hold_minutes) for s in signals) / total

    max_dd = _max_drawdown(pnls)
    pf = _profit_factor(pnls)

    return ReplayEvaluation(
        total_signals=total,
        win_count=win_count,
        win_rate=round(win_rate, 4),
        avg_pnl=round(avg_pnl, 6),
        avg_hold_minutes=round(avg_hold, 2),
        max_drawdown=round(max_dd, 6),
        profit_factor=round(pf, 4),
        config_overrides=config_overrides or {},
    )


def _max_drawdown(pnls: list[float]) -> float:
    """Compute maximum peak-to-trough drawdown from P&L series.

    Args:
        pnls: Sequence of per-trade P&L values.

    Returns:
        Maximum drawdown (positive number).
    """
    if not pnls:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _profit_factor(pnls: list[float]) -> float:
    """Compute profit factor (gross wins / gross losses).

    Args:
        pnls: Sequence of per-trade P&L values.

    Returns:
        Profit factor, capped at 999.99 if no losses.
    """
    gross_wins = sum(p for p in pnls if p > 0)
    gross_losses = abs(sum(p for p in pnls if p < 0))
    if gross_losses == 0:
        return _MAX_PROFIT_FACTOR if gross_wins > 0 else 0.0
    return min(gross_wins / gross_losses, _MAX_PROFIT_FACTOR)


async def sweep_parameter(
    engine: ReplayEngine,
    sport: str,
    since: datetime,
    until: datetime,
    param_name: str,
    min_val: float,
    max_val: float,
    step: float,
) -> SweepResult:
    """Run replays across a range of values for a config parameter.

    Args:
        engine: ReplayEngine instance.
        sport: Sport to replay.
        since: Start of time range.
        until: End of time range.
        param_name: Config field name to sweep.
        min_val: Minimum parameter value.
        max_val: Maximum parameter value (inclusive).
        step: Increment between values.

    Returns:
        SweepResult with evaluations per parameter value.
    """
    values = _generate_values(min_val, max_val, step)
    results: list[tuple[float, ReplayEvaluation]] = []

    for val in values:
        overrides = {param_name: val}
        signals = await engine.replay_sport(
            sport,
            since,
            until,
            overrides,
        )
        evaluation = evaluate_replay(signals, overrides)
        results.append((val, evaluation))

    return SweepResult(param_name=param_name, results=results)


def _generate_values(
    min_val: float,
    max_val: float,
    step: float,
) -> list[float]:
    """Generate parameter values from min to max inclusive.

    Uses Decimal for precision to avoid float accumulation errors.

    Args:
        min_val: Start value.
        max_val: End value (inclusive).
        step: Step size.

    Returns:
        List of float values.
    """
    d_min = Decimal(str(min_val))
    d_max = Decimal(str(max_val))
    d_step = Decimal(str(step))
    values: list[float] = []
    current = d_min
    while current <= d_max:
        values.append(float(current))
        current += d_step
    return values
