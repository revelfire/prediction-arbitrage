"""Tests for signal comparator: trade-to-signal alignment classification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arb_scanner.backtesting.signal_comparator import (
    aggregate_by_alignment,
    compare_trades_to_signals,
)
from arb_scanner.models.backtesting import ImportedTrade, SignalAlignment, TradeAction

_T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _trade(
    action: TradeAction = TradeAction.Buy,
    market: str = "Will Lakers win?",
    ts: datetime = _T0,
) -> ImportedTrade:
    return ImportedTrade(
        market_name=market,
        action=action,
        usdc_amount=Decimal("10"),
        token_amount=Decimal("50"),
        token_name="Yes",
        timestamp=ts,
        tx_hash=f"0x{id(ts):016x}",
    )


def _signal(
    title: str = "Lakers win",
    side: str = "yes",
    entry_at: datetime = _T0,
    pnl: float = 0.05,
) -> dict[str, object]:
    return {
        "market_title": title,
        "side": side,
        "entry_at": entry_at,
        "realized_pnl": pnl,
    }


class TestAligned:
    def test_buy_matches_yes_signal(self) -> None:
        trades = [_trade()]
        signals = [_signal()]
        result = compare_trades_to_signals(trades, signals)

        assert len(result) == 1
        assert result[0][1] == SignalAlignment.aligned
        assert result[0][2] is not None


class TestContrary:
    def test_buy_against_no_signal(self) -> None:
        trades = [_trade()]
        signals = [_signal(side="no")]
        result = compare_trades_to_signals(trades, signals)

        assert result[0][1] == SignalAlignment.contrary

    def test_sell_against_yes_signal(self) -> None:
        trades = [_trade(action=TradeAction.Sell)]
        signals = [_signal(side="yes")]
        result = compare_trades_to_signals(trades, signals)

        assert result[0][1] == SignalAlignment.contrary


class TestNoSignal:
    def test_no_matching_market(self) -> None:
        trades = [_trade(market="Bitcoin price")]
        signals = [_signal(title="Lakers win")]
        result = compare_trades_to_signals(trades, signals)

        assert result[0][1] == SignalAlignment.no_signal
        assert result[0][2] is None

    def test_outside_time_window(self) -> None:
        trades = [_trade(ts=_T0 + timedelta(minutes=60))]
        signals = [_signal(entry_at=_T0)]
        result = compare_trades_to_signals(trades, signals, window_minutes=30)

        assert result[0][1] == SignalAlignment.no_signal


class TestEdgeCases:
    def test_signal_just_inside_window(self) -> None:
        trades = [_trade(ts=_T0 + timedelta(minutes=30))]
        signals = [_signal(entry_at=_T0)]
        result = compare_trades_to_signals(trades, signals, window_minutes=30)

        assert result[0][1] == SignalAlignment.aligned

    def test_deposits_skipped(self) -> None:
        trades = [_trade(action=TradeAction.Deposit)]
        signals = [_signal()]
        result = compare_trades_to_signals(trades, signals)

        assert len(result) == 0


class TestMixed:
    def test_multiple_trades(self) -> None:
        t1 = _trade(market="Will Lakers win?", ts=_T0)
        t2 = _trade(market="Bitcoin above 100k", ts=_T0)
        signals = [_signal(title="Lakers win", side="yes", entry_at=_T0)]
        result = compare_trades_to_signals([t1, t2], signals)

        assert result[0][1] == SignalAlignment.aligned
        assert result[1][1] == SignalAlignment.no_signal


class TestAggregation:
    def test_groups_by_alignment(self) -> None:
        comparisons = [
            (_trade(), SignalAlignment.aligned, _signal(pnl=0.1)),
            (_trade(), SignalAlignment.aligned, _signal(pnl=-0.05)),
            (_trade(), SignalAlignment.contrary, _signal(pnl=0.02)),
            (_trade(), SignalAlignment.no_signal, None),
        ]
        agg = aggregate_by_alignment(comparisons)

        assert agg["aligned"]["count"] == 2
        assert agg["aligned"]["total_pnl"] == 0.1 + (-0.05)
        assert agg["contrary"]["count"] == 1
        assert agg["no_signal"]["count"] == 1
        assert agg["no_signal"]["total_pnl"] == 0.0
