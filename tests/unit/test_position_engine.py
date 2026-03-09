"""Tests for FIFO cost-basis position reconstruction."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from arb_scanner.backtesting.position_engine import (
    reconstruct_positions,
    trades_only,
)
from arb_scanner.models.backtesting import (
    ImportedTrade,
    PositionStatus,
    TradeAction,
)

_T0 = datetime(2026, 3, 1, tzinfo=UTC)
_T1 = datetime(2026, 3, 2, tzinfo=UTC)
_T2 = datetime(2026, 3, 3, tzinfo=UTC)


def _trade(
    action: TradeAction,
    usdc: str,
    tokens: str,
    ts: datetime = _T0,
    market: str = "BTC $80k",
    token: str = "Yes",
) -> ImportedTrade:
    return ImportedTrade(
        market_name=market,
        action=action,
        usdc_amount=Decimal(usdc),
        token_amount=Decimal(tokens),
        token_name=token,
        timestamp=ts,
        tx_hash=f"0x{hash((action, usdc, tokens, ts, market, token)):032x}",
    )


class TestTradesOnly:
    def test_filters_deposits_withdrawals(self) -> None:
        trades = [
            _trade(TradeAction.Buy, "10", "50"),
            _trade(TradeAction.Deposit, "1000", "1000", token="USDC"),
            _trade(TradeAction.Sell, "15", "50"),
        ]
        result = trades_only(trades)
        assert len(result) == 2
        assert all(
            t.action in (TradeAction.Buy, TradeAction.Sell) for t in result
        )


class TestSimpleBuySell:
    def test_full_liquidation(self) -> None:
        trades = [
            _trade(TradeAction.Buy, "10", "50", ts=_T0),
            _trade(TradeAction.Sell, "15", "50", ts=_T1),
        ]
        positions = reconstruct_positions(trades)
        assert len(positions) == 1

        pos = positions[0]
        assert pos.tokens_held == Decimal("0")
        assert pos.status == PositionStatus.closed
        assert pos.realized_pnl == Decimal("5")
        assert pos.cost_basis == Decimal("10")

    def test_losing_trade(self) -> None:
        trades = [
            _trade(TradeAction.Buy, "20", "50", ts=_T0),
            _trade(TradeAction.Sell, "10", "50", ts=_T1),
        ]
        positions = reconstruct_positions(trades)
        assert positions[0].realized_pnl == Decimal("-10")


class TestPartialSell:
    def test_remaining_tokens(self) -> None:
        trades = [
            _trade(TradeAction.Buy, "10", "100", ts=_T0),
            _trade(TradeAction.Sell, "7.5", "50", ts=_T1),
        ]
        positions = reconstruct_positions(trades)
        pos = positions[0]

        assert pos.tokens_held == Decimal("50")
        assert pos.status == PositionStatus.open
        # Buy at 0.10/token, sell 50 at 0.15/token → pnl = (0.15 - 0.10) * 50 = 2.5
        assert pos.realized_pnl == Decimal("2.5")


class TestFIFOOrdering:
    def test_multiple_buys_fifo(self) -> None:
        """First buy consumed first: buy 50@0.10, buy 50@0.20, sell 50."""
        trades = [
            _trade(TradeAction.Buy, "5", "50", ts=_T0),
            _trade(TradeAction.Buy, "10", "50", ts=_T1),
            _trade(TradeAction.Sell, "7.5", "50", ts=_T2),
        ]
        positions = reconstruct_positions(trades)
        pos = positions[0]

        assert pos.tokens_held == Decimal("50")
        # Sell 50 at 0.15, FIFO consumes first lot at 0.10
        # PnL = (0.15 - 0.10) * 50 = 2.5
        assert pos.realized_pnl == Decimal("2.5")
        # Remaining lot is at 0.20
        assert pos.avg_entry_price == Decimal("0.2")


class TestMultiMarket:
    def test_independent_positions(self) -> None:
        trades = [
            _trade(TradeAction.Buy, "10", "50", market="Market A"),
            _trade(TradeAction.Buy, "20", "100", market="Market B"),
        ]
        positions = reconstruct_positions(trades)
        assert len(positions) == 2
        names = {p.market_name for p in positions}
        assert names == {"Market A", "Market B"}


class TestSellWithoutBuy:
    def test_unknown_basis(self) -> None:
        """Sell without prior buy: treat cost basis as zero."""
        trades = [
            _trade(TradeAction.Sell, "15", "50", ts=_T0),
        ]
        positions = reconstruct_positions(trades)
        pos = positions[0]

        # No lots to consume → pnl = sell_price * qty = 0.30 * 50 = 15
        assert pos.realized_pnl == Decimal("15")
        assert pos.tokens_held == Decimal("0")
        assert pos.status == PositionStatus.closed


class TestBuyOnly:
    def test_open_position(self) -> None:
        trades = [
            _trade(TradeAction.Buy, "10", "50"),
        ]
        positions = reconstruct_positions(trades)
        pos = positions[0]

        assert pos.tokens_held == Decimal("50")
        assert pos.realized_pnl == Decimal("0")
        assert pos.status == PositionStatus.open
        assert pos.avg_entry_price == Decimal("0.2")


class TestDecimalPrecision:
    def test_preserves_precision(self) -> None:
        trades = [
            _trade(TradeAction.Buy, "18.438", "184.00664", ts=_T0),
            _trade(TradeAction.Sell, "19.5", "184.00664", ts=_T1),
        ]
        positions = reconstruct_positions(trades)
        pos = positions[0]
        assert pos.cost_basis == Decimal("18.438")
        assert pos.tokens_held == Decimal("0")
        # P&L should be 19.5 - 18.438 = 1.062
        expected_pnl = Decimal("19.5") / Decimal("184.00664") - Decimal(
            "18.438"
        ) / Decimal("184.00664")
        expected_pnl_total = expected_pnl * Decimal("184.00664")
        assert abs(pos.realized_pnl - expected_pnl_total) < Decimal("0.0001")


class TestEmptyInput:
    def test_no_trades(self) -> None:
        assert reconstruct_positions([]) == []

    def test_only_deposits(self) -> None:
        trades = [
            _trade(
                TradeAction.Deposit, "1000", "1000", token="USDC"
            ),
        ]
        assert reconstruct_positions(trades) == []
