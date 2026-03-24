"""Automated backtesting workflow and config suggestion helpers."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from arb_scanner.backtesting.csv_importer import parse_csv_bytes
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
from arb_scanner.flippening.replay_engine import ReplayEngine
from arb_scanner.flippening.replay_evaluator import evaluate_replay
from arb_scanner.models.backtesting import ImportedTrade, TradeAction, TradePosition
from arb_scanner.models.config import Settings
from arb_scanner.storage.tick_repository import TickRepository

_ALIGNMENT_KEYS = ("aligned", "contrary", "no_signal")
_MAX_SWEEP_CATEGORIES = 3
_MIN_SIGNALS_FOR_SUGGESTION = 5
_AUTO_EXEC_CONFIDENCE_CANDIDATES = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
_SWEEP_SPECS: dict[str, dict[str, Any]] = {
    "min_confidence": {
        "min": 0.50,
        "max": 0.85,
        "step": 0.05,
        "caster": float,
    },
    "spike_threshold_pct": {
        "min": 0.05,
        "max": 0.25,
        "step": 0.02,
        "caster": float,
    },
    "stop_loss_pct": {
        "min": 0.05,
        "max": 0.25,
        "step": 0.02,
        "caster": float,
    },
    "max_hold_minutes": {
        "min": 10.0,
        "max": 90.0,
        "step": 5.0,
        "caster": int,
    },
}


def deserialize_trade_rows(rows: list[dict[str, Any]]) -> list[ImportedTrade]:
    """Build validated ImportedTrade models from DB rows."""
    return [ImportedTrade(**{k: v for k, v in row.items() if k != "id"}) for row in rows]


def trade_window(trades: list[ImportedTrade]) -> tuple[datetime, datetime]:
    """Return an inclusive start / exclusive end window for a trade set."""
    buy_sells = [t.timestamp for t in trades if t.action in (TradeAction.Buy, TradeAction.Sell)]
    if not buy_sells:
        now = datetime.now(tz=UTC)
        return now - timedelta(days=90), now + timedelta(minutes=1)
    return min(buy_sells), max(buy_sells) + timedelta(minutes=1)


def build_signal_alignment(
    trades: list[ImportedTrade],
    signals: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    list[tuple[ImportedTrade, Any, dict[str, Any] | None]],
]:
    """Compare trades to signals and return normalized aggregate counts."""
    buy_sells = [t for t in trades if t.action in (TradeAction.Buy, TradeAction.Sell)]
    comparisons = compare_trades_to_signals(buy_sells, signals)
    aggregate = aggregate_by_alignment(comparisons)
    normalized = {
        key: aggregate.get(key, {"count": 0, "total_pnl": 0.0, "avg_pnl": 0.0})
        for key in _ALIGNMENT_KEYS
    }
    return normalized, comparisons


async def build_backtest_report_data(
    repo: Any,
    flip_repo: Any,
    *,
    category: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Build the portfolio + signal report payload from persisted data."""
    rows = await repo.get_trades(since=since, until=until, limit=None)
    trades = deserialize_trade_rows(rows)
    positions = reconstruct_positions(trades)

    if category:
        positions = [
            p
            for p in positions
            if classify_market_category(p.market_name, DEFAULT_SPORT_KEYWORDS) == category
        ]
        trades = [
            t
            for t in trades
            if classify_market_category(t.market_name, DEFAULT_SPORT_KEYWORDS) == category
        ]

    summary = calculate_portfolio(positions)
    signals = await flip_repo.get_history(
        limit=None,
        category=category,
        since=since,
        until=until,
    )
    alignment_agg, comparisons = build_signal_alignment(trades, signals)
    cat_perfs = compute_category_performance(positions, comparisons, DEFAULT_SPORT_KEYWORDS)

    return {
        "portfolio": summary.model_dump(mode="json"),
        "signal_alignment": alignment_agg,
        "category_performance": [p.model_dump(mode="json") for p in cat_perfs],
        "category_models": cat_perfs,
        "positions": positions,
        "trades": trades,
        "signals": signals,
        "comparisons": comparisons,
    }


async def run_import_workflow(
    content: bytes,
    *,
    config: Settings,
    repo: Any,
    flip_repo: Any,
) -> dict[str, Any]:
    """Import a Polymarket CSV and run the full backtesting workflow."""
    imported_trades = parse_csv_bytes(content)
    import_result = await repo.import_trades(imported_trades)

    report = await build_backtest_report_data(repo, flip_repo)
    positions = cast(list[TradePosition], report["positions"])
    trades = cast(list[ImportedTrade], report["trades"])
    signals = cast(list[dict[str, Any]], report["signals"])
    category_models = cast(list[Any], report["category_models"])
    suggestions = await generate_config_suggestions(
        config,
        positions=positions,
        trades=trades,
        signal_history=signals,
        pool=getattr(repo, "_pool"),
    )

    for perf in category_models:
        await repo.upsert_category_performance(perf)

    for pos in positions:
        await repo.upsert_position(pos)

    return {
        "import_result": import_result.model_dump(),
        "portfolio": report["portfolio"],
        "signal_alignment": report["signal_alignment"],
        "category_performance": report["category_performance"],
        "trade_count": len([t for t in trades if t.action in (TradeAction.Buy, TradeAction.Sell)]),
        "suggestions": suggestions,
    }


async def generate_config_suggestions(
    config: Settings,
    *,
    positions: list[TradePosition],
    trades: list[ImportedTrade],
    signal_history: list[dict[str, Any]],
    pool: Any,
) -> list[dict[str, Any]]:
    """Generate replay-backed config suggestions for imported trade categories."""
    if not trades:
        return []

    since, until = trade_window(trades)
    categories = _top_categories(positions)
    suggestions: list[dict[str, Any]] = []

    auto_exec_suggestion = _suggest_flip_auto_exec_min_confidence(config, signal_history)
    if auto_exec_suggestion is not None:
        suggestions.append(auto_exec_suggestion)

    if not categories:
        return suggestions

    engine = ReplayEngine(TickRepository(pool), config.flippening)
    for category in categories[:_MAX_SWEEP_CATEGORIES]:
        try:
            current_eval = evaluate_replay(
                await engine.replay_category(category, since, until),
            )
        except Exception:
            continue
        if current_eval.total_signals < _MIN_SIGNALS_FOR_SUGGESTION:
            continue

        for param_name, spec in _SWEEP_SPECS.items():
            try:
                sweep_results = await _sweep_category_parameter(
                    engine,
                    config,
                    category,
                    since,
                    until,
                    param_name,
                    min_value=spec["min"],
                    max_value=spec["max"],
                    step=spec["step"],
                    caster=spec["caster"],
                )
            except Exception:
                continue
            suggestion = _build_category_suggestion(
                config,
                category=category,
                param_name=param_name,
                current_eval=current_eval.model_dump(mode="json"),
                sweep_results=sweep_results,
                caster=spec["caster"],
            )
            if suggestion is not None:
                suggestions.append(suggestion)

    suggestions.sort(
        key=lambda s: (
            float(s.get("win_rate_delta", 0.0)),
            float(s.get("avg_pnl_delta", 0.0)),
            float(s.get("drawdown_improvement", 0.0)),
        ),
        reverse=True,
    )
    return suggestions


def _top_categories(positions: list[TradePosition]) -> list[str]:
    """Return the most active non-uncategorized categories in the positions set."""
    counts: Counter[str] = Counter()
    for pos in positions:
        category = classify_market_category(pos.market_name, DEFAULT_SPORT_KEYWORDS)
        if category and category != "uncategorized":
            counts[category] += 1
    return [category for category, _count in counts.most_common()]


def _suggest_flip_auto_exec_min_confidence(
    config: Settings,
    signal_history: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Suggest a tighter flip auto-exec confidence threshold from realized signal outcomes."""
    if len(signal_history) < _MIN_SIGNALS_FOR_SUGGESTION:
        return None

    current = float(config.auto_execution.effective_config("flip").min_confidence)
    current_metrics = _confidence_bucket_metrics(signal_history, current)
    if current_metrics["count"] < _MIN_SIGNALS_FOR_SUGGESTION:
        return None

    candidates = [
        _confidence_bucket_metrics(signal_history, cutoff)
        for cutoff in _AUTO_EXEC_CONFIDENCE_CANDIDATES
        if cutoff >= current
    ]
    viable = [
        metrics
        for metrics in candidates
        if metrics["count"] >= max(_MIN_SIGNALS_FOR_SUGGESTION, int(current_metrics["count"] * 0.4))
    ]
    if not viable:
        return None

    best = max(
        viable,
        key=lambda m: (
            m["win_rate"],
            m["avg_pnl"],
            m["total_pnl"],
        ),
    )
    if best["threshold"] <= current:
        return None
    if best["win_rate"] - current_metrics["win_rate"] < 0.05:
        return None
    if best["avg_pnl"] < current_metrics["avg_pnl"]:
        return None

    return {
        "scope": "auto_execution",
        "category": "flip",
        "param_name": "min_confidence",
        "config_path": "auto_execution.flip_overrides.min_confidence",
        "current_value": round(current, 4),
        "suggested_value": round(best["threshold"], 4),
        "sample_size": best["count"],
        "current_win_rate": round(current_metrics["win_rate"], 4),
        "suggested_win_rate": round(best["win_rate"], 4),
        "win_rate_delta": round(best["win_rate"] - current_metrics["win_rate"], 4),
        "avg_pnl_delta": round(best["avg_pnl"] - current_metrics["avg_pnl"], 6),
        "drawdown_improvement": 0.0,
        "reason": (
            f"Flip exits at confidence >= {best['threshold']:.2f} improved win rate "
            f"from {current_metrics['win_rate']:.1%} to {best['win_rate']:.1%} "
            f"over {best['count']} historical signals."
        ),
    }


def _confidence_bucket_metrics(
    signal_history: list[dict[str, Any]],
    threshold: float,
) -> dict[str, float | int]:
    """Compute realized metrics for a confidence cutoff on historical flip exits."""
    kept = [row for row in signal_history if float(row.get("confidence", 0.0)) >= threshold]
    pnls = [float(row.get("realized_pnl", 0.0) or 0.0) for row in kept]
    wins = sum(1 for pnl in pnls if pnl > 0)
    count = len(pnls)
    total_pnl = sum(pnls)
    return {
        "threshold": threshold,
        "count": count,
        "win_rate": (wins / count) if count else 0.0,
        "avg_pnl": (total_pnl / count) if count else 0.0,
        "total_pnl": total_pnl,
    }


def _build_category_suggestion(
    config: Settings,
    *,
    category: str,
    param_name: str,
    current_eval: dict[str, Any],
    sweep_results: list[list[Any]],
    caster: type[int] | type[float],
) -> dict[str, Any] | None:
    """Convert sweep output into a persisted config suggestion when improvement is material."""
    if not sweep_results:
        return None

    current_value = _current_category_config_value(config, category, param_name)
    viable = [
        (row[0], row[1])
        for row in sweep_results
        if isinstance(row, list) and len(row) == 2 and row[1].get("total_signals", 0) >= _MIN_SIGNALS_FOR_SUGGESTION
    ]
    if not viable:
        return None

    best_value, best_eval = max(
        viable,
        key=lambda row: (
            row[1].get("win_rate", 0.0),
            row[1].get("avg_pnl", 0.0),
            -row[1].get("max_drawdown", 0.0),
            row[1].get("total_signals", 0),
        ),
    )
    cast_value = caster(best_value)
    if cast_value == current_value:
        return None

    win_rate_delta = float(best_eval.get("win_rate", 0.0)) - float(current_eval.get("win_rate", 0.0))
    avg_pnl_delta = float(best_eval.get("avg_pnl", 0.0)) - float(current_eval.get("avg_pnl", 0.0))
    drawdown_improvement = float(current_eval.get("max_drawdown", 0.0)) - float(
        best_eval.get("max_drawdown", 0.0)
    )
    if win_rate_delta < 0.03 and avg_pnl_delta <= 0 and drawdown_improvement <= 0:
        return None

    return {
        "scope": "flippening_category",
        "category": category,
        "param_name": param_name,
        "config_path": f"flippening.categories.{category}.{param_name}",
        "current_value": current_value,
        "suggested_value": cast_value,
        "sample_size": int(best_eval.get("total_signals", 0)),
        "current_win_rate": round(float(current_eval.get("win_rate", 0.0)), 4),
        "suggested_win_rate": round(float(best_eval.get("win_rate", 0.0)), 4),
        "win_rate_delta": round(win_rate_delta, 4),
        "avg_pnl_delta": round(avg_pnl_delta, 6),
        "drawdown_improvement": round(drawdown_improvement, 6),
        "reason": (
            f"Replay sweep for {category} found {param_name}={cast_value} improved win rate "
            f"from {float(current_eval.get('win_rate', 0.0)):.1%} "
            f"to {float(best_eval.get('win_rate', 0.0)):.1%}."
        ),
    }


def _current_category_config_value(
    config: Settings,
    category: str,
    param_name: str,
) -> Any:
    """Resolve the current effective category value for a sweepable parameter."""
    category_cfg = config.flippening.categories.get(category)
    if category_cfg is not None:
        value = getattr(category_cfg, param_name, None)
        if value is not None:
            return value
    return getattr(config.flippening, param_name)


async def _sweep_category_parameter(
    engine: ReplayEngine,
    config: Settings,
    category: str,
    since: datetime,
    until: datetime,
    param_name: str,
    *,
    min_value: float,
    max_value: float,
    step: float,
    caster: type[int] | type[float],
) -> list[list[Any]]:
    """Run a category-specific replay sweep by injecting only that category override."""
    results: list[list[Any]] = []
    for value in _generate_sweep_values(min_value, max_value, step):
        cast_value = caster(value)
        overrides = _category_override_payload(config, category, param_name, cast_value)
        signals = await engine.replay_category(
            category,
            since,
            until,
            overrides=overrides,
        )
        evaluation = evaluate_replay(signals, overrides)
        results.append([cast_value, evaluation.model_dump(mode="json")])
    return results


def _category_override_payload(
    config: Settings,
    category: str,
    param_name: str,
    value: int | float,
) -> dict[str, Any]:
    """Build a nested replay override that only touches one category field."""
    categories = config.flippening.model_dump(mode="python").get("categories", {})
    category_cfg = dict(categories.get(category, {}))
    category_cfg[param_name] = value
    categories[category] = category_cfg
    return {"categories": categories}


def _generate_sweep_values(min_value: float, max_value: float, step: float) -> list[float]:
    """Generate deterministic sweep values without float drift."""
    values: list[float] = []
    current = Decimal(str(min_value))
    upper = Decimal(str(max_value))
    delta = Decimal(str(step))
    while current <= upper:
        values.append(float(current))
        current += delta
    return values
