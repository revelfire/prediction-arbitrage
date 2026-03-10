"""Per-category performance metrics from imported trades and signal comparisons."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from arb_scanner.models.backtesting import (
    CategoryPerformance,
    SignalAlignment,
    TradePosition,
)

_PROFIT_FACTOR_CAP = 999.0


def classify_market_category(
    market_name: str,
    keyword_map: dict[str, list[str]],
) -> str:
    """Classify a market name into a category using keyword matching.

    Args:
        market_name: The market name to classify.
        keyword_map: Mapping of category_id to keyword lists.

    Returns:
        First matching category_id or "uncategorized".
    """
    name_lower = market_name.lower()
    for cat_id in sorted(keyword_map):
        for keyword in keyword_map[cat_id]:
            if keyword in name_lower:
                return cat_id
    return "uncategorized"


def compute_category_performance(
    positions: list[TradePosition],
    comparisons: list[tuple[Any, SignalAlignment, dict[str, Any] | None]],
    keyword_map: dict[str, list[str]],
) -> list[CategoryPerformance]:
    """Compute per-category performance from positions and signal comparisons.

    Args:
        positions: Materialized trade positions.
        comparisons: Output of compare_trades_to_signals.
        keyword_map: Category keyword mapping for classification.

    Returns:
        List of CategoryPerformance models, one per category.
    """
    if not positions:
        return []

    cat_positions = _group_positions_by_category(positions, keyword_map)
    alignment_stats = _build_alignment_stats(comparisons, keyword_map)
    now = datetime.now(tz=UTC)

    results: list[CategoryPerformance] = []
    for cat_id in sorted(cat_positions):
        pos_list = cat_positions[cat_id]
        stats = alignment_stats.get(cat_id)
        perf = _compute_single_category(cat_id, pos_list, stats, now)
        results.append(perf)

    return results


def _group_positions_by_category(
    positions: list[TradePosition],
    keyword_map: dict[str, list[str]],
) -> dict[str, list[TradePosition]]:
    """Group positions by their classified category.

    Args:
        positions: Trade positions to group.
        keyword_map: Category keyword mapping.

    Returns:
        Dict of category_id to list of positions.
    """
    groups: dict[str, list[TradePosition]] = defaultdict(list)
    for pos in positions:
        cat = classify_market_category(pos.market_name, keyword_map)
        groups[cat].append(pos)
    return dict(groups)


def _build_alignment_stats(
    comparisons: list[tuple[Any, SignalAlignment, dict[str, Any] | None]],
    keyword_map: dict[str, list[str]],
) -> dict[str, dict[str, Any]]:
    """Aggregate signal alignment stats per category.

    Args:
        comparisons: Signal comparison tuples.
        keyword_map: Category keyword mapping.

    Returns:
        Dict of category_id to alignment counts and win tallies.
    """
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "aligned": 0, "contrary": 0, "aligned_wins": 0, "contrary_wins": 0},
    )
    for trade, alignment, sig in comparisons:
        cat = classify_market_category(trade.market_name, keyword_map)
        s = stats[cat]
        s["total"] += 1
        if alignment == SignalAlignment.aligned:
            s["aligned"] += 1
            if sig and float(sig.get("realized_pnl", 0)) > 0:
                s["aligned_wins"] += 1
        elif alignment == SignalAlignment.contrary:
            s["contrary"] += 1
            if sig and float(sig.get("realized_pnl", 0)) > 0:
                s["contrary_wins"] += 1
    return dict(stats)


def _compute_single_category(
    cat_id: str,
    positions: list[TradePosition],
    alignment: dict[str, Any] | None,
    now: datetime,
) -> CategoryPerformance:
    """Build CategoryPerformance for one category.

    Args:
        cat_id: Category identifier.
        positions: Positions in this category.
        alignment: Alignment stats dict (or None).
        now: Current timestamp.

    Returns:
        CategoryPerformance model.
    """
    pnls = [float(p.realized_pnl) for p in positions]
    wins = sum(1 for p in pnls if p > 0)
    losses = [p for p in pnls if p < 0]
    total_pnl = sum(pnls)
    count = len(positions)

    gains_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(losses))
    profit_factor = (
        min(gains_sum / loss_sum, _PROFIT_FACTOR_CAP) if loss_sum > 0 else _PROFIT_FACTOR_CAP
    )

    hold_deltas = [(p.last_trade_at - p.first_trade_at).total_seconds() / 60.0 for p in positions]
    avg_hold = sum(hold_deltas) / len(hold_deltas) if hold_deltas else 0.0

    aligned_ct = alignment["aligned"] if alignment else 0
    contrary_ct = alignment["contrary"] if alignment else 0
    total_signals = aligned_ct + contrary_ct
    sig_rate = total_signals / count if count > 0 else 0.0
    a_wins = alignment["aligned_wins"] if alignment else 0
    c_wins = alignment["contrary_wins"] if alignment else 0

    return CategoryPerformance(
        category=cat_id,
        win_rate=wins / count if count > 0 else 0.0,
        avg_pnl=total_pnl / count if count > 0 else 0.0,
        trade_count=count,
        total_pnl=total_pnl,
        profit_factor=profit_factor,
        avg_hold_minutes=avg_hold,
        signal_alignment_rate=sig_rate,
        aligned_win_rate=a_wins / aligned_ct if aligned_ct > 0 else 0.0,
        contrary_win_rate=c_wins / contrary_ct if contrary_ct > 0 else 0.0,
        computed_at=now,
    )
