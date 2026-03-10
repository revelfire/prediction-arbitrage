"""Compare imported trades against flippening signals for alignment analysis."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from arb_scanner.models.backtesting import ImportedTrade, SignalAlignment, TradeAction


def compare_trades_to_signals(
    trades: list[ImportedTrade],
    signals: list[dict[str, Any]],
    window_minutes: int = 30,
) -> list[tuple[ImportedTrade, SignalAlignment, dict[str, Any] | None]]:
    """Match each trade to the closest flippening signal within a time window.

    Args:
        trades: Imported trades to classify.
        signals: Flippening signal dicts (from GET_HISTORY query).
        window_minutes: Maximum minutes between trade and signal entry.

    Returns:
        List of (trade, alignment, matched_signal_or_None) tuples.
    """
    window = timedelta(minutes=window_minutes)
    results: list[tuple[ImportedTrade, SignalAlignment, dict[str, Any] | None]] = []

    for trade in trades:
        if trade.action not in (TradeAction.Buy, TradeAction.Sell):
            continue
        match = _find_matching_signal(trade, signals, window)
        if match is None:
            results.append((trade, SignalAlignment.no_signal, None))
        else:
            alignment = _classify_alignment(trade, match)
            results.append((trade, alignment, match))

    return results


def _find_matching_signal(
    trade: ImportedTrade,
    signals: list[dict[str, Any]],
    window: timedelta,
) -> dict[str, Any] | None:
    """Find the closest signal matching a trade by name and time.

    Args:
        trade: The trade to match.
        signals: Available signals.
        window: Maximum time difference.

    Returns:
        Best matching signal dict, or None.
    """
    best: dict[str, Any] | None = None
    best_delta = window + timedelta(seconds=1)
    trade_lower = trade.market_name.lower()

    for sig in signals:
        title = str(sig.get("market_title", "")).lower()
        if not _names_match(trade_lower, title):
            continue
        entry_at = sig.get("entry_at")
        if entry_at is None:
            continue
        delta = abs(trade.timestamp - entry_at)
        if delta <= window and delta < best_delta:
            best = sig
            best_delta = delta

    return best


def _names_match(trade_name: str, signal_title: str) -> bool:
    """Check if trade and signal refer to the same market (substring match).

    Args:
        trade_name: Lowered trade market name.
        signal_title: Lowered signal market title.

    Returns:
        True if one is a substring of the other.
    """
    return signal_title in trade_name or trade_name in signal_title


def _classify_alignment(
    trade: ImportedTrade,
    signal: dict[str, Any],
) -> SignalAlignment:
    """Determine if trade direction aligns with signal side.

    Args:
        trade: The imported trade.
        signal: Matched signal dict with 'side' field.

    Returns:
        aligned or contrary.
    """
    side = str(signal.get("side", "")).lower()
    if trade.action == TradeAction.Buy:
        return SignalAlignment.aligned if side == "yes" else SignalAlignment.contrary
    # Sell
    return SignalAlignment.aligned if side == "no" else SignalAlignment.contrary


def aggregate_by_alignment(
    comparisons: list[tuple[ImportedTrade, SignalAlignment, dict[str, Any] | None]],
) -> dict[str, dict[str, Any]]:
    """Group comparison results by alignment and compute summary stats.

    Args:
        comparisons: Output of compare_trades_to_signals.

    Returns:
        Dict keyed by alignment value with count, total_pnl, avg_pnl.
    """
    groups: dict[str, list[float]] = {}
    for _trade, alignment, sig in comparisons:
        key = alignment.value
        pnl = float(sig.get("realized_pnl", 0)) if sig else 0.0
        groups.setdefault(key, []).append(pnl)

    result: dict[str, dict[str, Any]] = {}
    for key, pnls in groups.items():
        total = sum(pnls)
        result[key] = {
            "count": len(pnls),
            "total_pnl": total,
            "avg_pnl": total / len(pnls) if pnls else 0.0,
        }
    return result
