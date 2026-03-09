"""Tests for per-category performance tracker."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from arb_scanner.backtesting.performance_tracker import (
    classify_market_category,
    compute_category_performance,
)
from arb_scanner.models.backtesting import (
    ImportedTrade,
    PositionStatus,
    SignalAlignment,
    TradeAction,
    TradePosition,
)

_T0 = datetime(2026, 3, 1, tzinfo=UTC)
_T1 = datetime(2026, 3, 2, tzinfo=UTC)

_KEYWORDS: dict[str, list[str]] = {
    "btc_threshold": ["bitcoin", "btc"],
    "nba": ["lakers", "celtics", "warriors"],
}


def _pos(
    market: str = "Lakers vs Celtics",
    pnl: str = "5",
    status: PositionStatus = PositionStatus.closed,
) -> TradePosition:
    return TradePosition(
        market_name=market,
        token_name="Yes",
        cost_basis=Decimal("10"),
        tokens_held=Decimal("0"),
        avg_entry_price=Decimal("0.20"),
        realized_pnl=Decimal(pnl),
        unrealized_pnl=Decimal("0"),
        status=status,
        fee_paid=Decimal("0"),
        first_trade_at=_T0,
        last_trade_at=_T1,
    )


def _trade(market: str = "Lakers vs Celtics") -> ImportedTrade:
    return ImportedTrade(
        market_name=market,
        action=TradeAction.Buy,
        usdc_amount=Decimal("10"),
        token_amount=Decimal("50"),
        token_name="Yes",
        timestamp=_T0,
        tx_hash=f"0x{id(market):016x}",
    )


class TestClassifyMarket:
    def test_bitcoin_matches_btc_threshold(self) -> None:
        assert classify_market_category("Bitcoin above 100k?", _KEYWORDS) == "btc_threshold"

    def test_nba_team_matches(self) -> None:
        assert classify_market_category("Will Lakers win tonight?", _KEYWORDS) == "nba"

    def test_unknown_returns_uncategorized(self) -> None:
        assert classify_market_category("Presidential election", _KEYWORDS) == "uncategorized"

    def test_case_insensitive(self) -> None:
        assert classify_market_category("BITCOIN price", _KEYWORDS) == "btc_threshold"


class TestCategoryPerformance:
    def test_single_category(self) -> None:
        positions = [_pos(pnl="5"), _pos(pnl="-2")]
        comparisons: list[tuple[ImportedTrade, SignalAlignment, dict | None]] = []
        result = compute_category_performance(positions, comparisons, _KEYWORDS)

        assert len(result) == 1
        perf = result[0]
        assert perf.category == "nba"
        assert perf.trade_count == 2
        assert perf.win_rate == 0.5
        assert perf.total_pnl == 3.0

    def test_empty_positions(self) -> None:
        result = compute_category_performance([], [], _KEYWORDS)
        assert result == []

    def test_multiple_categories(self) -> None:
        positions = [
            _pos(market="Lakers game", pnl="10"),
            _pos(market="Bitcoin above 100k", pnl="-3"),
        ]
        result = compute_category_performance(positions, [], _KEYWORDS)

        assert len(result) == 2
        cats = {p.category for p in result}
        assert cats == {"btc_threshold", "nba"}


class TestSignalAlignmentRate:
    def test_alignment_metrics(self) -> None:
        positions = [_pos(pnl="5")]
        trade = _trade()
        sig = {"market_title": "Lakers", "realized_pnl": 0.05}
        comparisons = [(trade, SignalAlignment.aligned, sig)]

        result = compute_category_performance(positions, comparisons, _KEYWORDS)
        perf = result[0]

        assert perf.signal_alignment_rate > 0
        assert perf.aligned_win_rate == 1.0
        assert perf.contrary_win_rate == 0.0


class TestProfitFactor:
    def test_no_losses_capped(self) -> None:
        positions = [_pos(pnl="10")]
        result = compute_category_performance(positions, [], _KEYWORDS)

        assert result[0].profit_factor == 999.0

    def test_losses_present(self) -> None:
        positions = [_pos(pnl="10"), _pos(pnl="-5")]
        result = compute_category_performance(positions, [], _KEYWORDS)

        assert result[0].profit_factor == 2.0


class TestHoldMinutes:
    def test_avg_hold_calculated(self) -> None:
        positions = [_pos()]
        result = compute_category_performance(positions, [], _KEYWORDS)

        expected = (_T1 - _T0).total_seconds() / 60.0
        assert result[0].avg_hold_minutes == expected
