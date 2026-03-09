"""Tests for portfolio-level metrics calculation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from arb_scanner.backtesting.portfolio_calculator import calculate_portfolio
from arb_scanner.models.backtesting import PositionStatus, TradePosition

_T0 = datetime(2026, 3, 1, tzinfo=UTC)
_T1 = datetime(2026, 3, 4, tzinfo=UTC)


def _pos(
    pnl: str,
    cost: str = "10",
    status: PositionStatus = PositionStatus.closed,
    market: str = "Test",
    token: str = "Yes",
) -> TradePosition:
    return TradePosition(
        market_name=market,
        token_name=token,
        cost_basis=Decimal(cost),
        tokens_held=Decimal("0") if status != PositionStatus.open else Decimal("50"),
        avg_entry_price=Decimal("0.20"),
        realized_pnl=Decimal(pnl),
        unrealized_pnl=Decimal("0"),
        status=status,
        fee_paid=Decimal("0"),
        first_trade_at=_T0,
        last_trade_at=_T1,
    )


class TestAllWinners:
    def test_fees_deducted(self) -> None:
        positions = [_pos("5"), _pos("10")]
        result = calculate_portfolio(positions, fee_rate=Decimal("0.02"))

        assert result.win_count == 2
        assert result.loss_count == 0
        assert result.total_fees == Decimal("0.30")  # 0.02 * (5 + 10)
        assert result.win_rate == 1.0


class TestAllLosers:
    def test_no_fees_on_losses(self) -> None:
        positions = [_pos("-5"), _pos("-3")]
        result = calculate_portfolio(positions, fee_rate=Decimal("0.02"))

        assert result.win_count == 0
        assert result.loss_count == 2
        assert result.total_fees == Decimal("0")
        assert result.win_rate == 0.0


class TestMixed:
    def test_wins_and_losses(self) -> None:
        positions = [_pos("10"), _pos("-3")]
        result = calculate_portfolio(positions, fee_rate=Decimal("0.02"))

        assert result.win_count == 1
        assert result.loss_count == 1
        assert result.win_rate == 0.5
        # fee = 0.02 * 10 = 0.20
        assert result.total_fees == Decimal("0.20")
        # net = (10 + -3) + 0 - 0.20 = 6.80
        assert result.net_pnl == Decimal("6.80")


class TestZeroCapital:
    def test_roi_zero(self) -> None:
        positions = [_pos("5", cost="0")]
        result = calculate_portfolio(positions)

        assert result.roi == 0.0


class TestEmptyPositions:
    def test_returns_zero_summary(self) -> None:
        result = calculate_portfolio([])

        assert result.trade_count == 0
        assert result.net_pnl == Decimal("0")
        assert result.win_rate == 0.0
        assert result.roi == 0.0
        assert result.positions == []


class TestZeroFeeRate:
    def test_no_fees(self) -> None:
        positions = [_pos("10")]
        result = calculate_portfolio(positions, fee_rate=Decimal("0"))

        assert result.total_fees == Decimal("0")
        assert result.net_pnl == Decimal("10")


class TestOpenPositions:
    def test_open_not_counted_as_win_or_loss(self) -> None:
        positions = [_pos("0", status=PositionStatus.open)]
        result = calculate_portfolio(positions)

        assert result.win_count == 0
        assert result.loss_count == 0
        assert result.trade_count == 1


class TestCapitalAndSize:
    def test_aggregates(self) -> None:
        positions = [_pos("5", cost="20"), _pos("3", cost="30")]
        result = calculate_portfolio(positions)

        assert result.total_capital_deployed == Decimal("50")
        assert result.avg_trade_size == Decimal("25")
