"""Tests for backtesting data models."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arb_scanner.models.backtesting import (
    ImportedTrade,
    ImportResult,
    OptimalParamSnapshot,
    PortfolioSummary,
    PositionStatus,
    SignalAlignment,
    TradeAction,
    TradePosition,
)


class TestEnums:
    def test_trade_action_values(self) -> None:
        assert TradeAction.Buy.value == "Buy"
        assert TradeAction.Sell.value == "Sell"
        assert TradeAction.Deposit.value == "Deposit"
        assert TradeAction.Withdraw.value == "Withdraw"

    def test_position_status_values(self) -> None:
        assert PositionStatus.open.value == "open"
        assert PositionStatus.closed.value == "closed"
        assert PositionStatus.resolved.value == "resolved"

    def test_signal_alignment_values(self) -> None:
        assert SignalAlignment.aligned.value == "aligned"
        assert SignalAlignment.contrary.value == "contrary"
        assert SignalAlignment.no_signal.value == "no_signal"

    def test_enum_string_serialization(self) -> None:
        assert str(TradeAction.Buy) == "TradeAction.Buy"
        assert TradeAction("Buy") is TradeAction.Buy


class TestImportedTrade:
    def test_valid_trade(self) -> None:
        trade = ImportedTrade(
            market_name="BTC above $80k?",
            action=TradeAction.Buy,
            usdc_amount=Decimal("10.50"),
            token_amount=Decimal("50"),
            token_name="Yes",
            timestamp=datetime(2026, 3, 4, tzinfo=UTC),
            tx_hash="0xabc123",
        )
        assert trade.usdc_amount == Decimal("10.50")
        assert trade.condition_id is None
        assert trade.imported_at is None

    def test_rejects_empty_tx_hash(self) -> None:
        with pytest.raises(ValueError, match="tx_hash must not be empty"):
            ImportedTrade(
                market_name="Test",
                action=TradeAction.Buy,
                usdc_amount=Decimal("1"),
                token_amount=Decimal("1"),
                token_name="Yes",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                tx_hash="  ",
            )

    def test_rejects_negative_amount(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            ImportedTrade(
                market_name="Test",
                action=TradeAction.Sell,
                usdc_amount=Decimal("-5"),
                token_amount=Decimal("10"),
                token_name="No",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                tx_hash="0xdef",
            )

    def test_decimal_precision_preserved(self) -> None:
        trade = ImportedTrade(
            market_name="Test",
            action=TradeAction.Buy,
            usdc_amount=Decimal("18.438"),
            token_amount=Decimal("184.00664"),
            token_name="No",
            timestamp=datetime(2026, 3, 4, tzinfo=UTC),
            tx_hash="0x123",
        )
        assert trade.usdc_amount == Decimal("18.438")
        assert trade.token_amount == Decimal("184.00664")


class TestImportResult:
    def test_construction(self) -> None:
        result = ImportResult(inserted=35, duplicates=2, errors=1)
        assert result.inserted == 35
        assert result.duplicates == 2
        assert result.errors == 1


class TestPortfolioSummary:
    def test_with_positions(self) -> None:
        pos = TradePosition(
            market_name="BTC $80k",
            token_name="Yes",
            cost_basis=Decimal("10"),
            tokens_held=Decimal("50"),
            avg_entry_price=Decimal("0.20"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("5"),
            status=PositionStatus.open,
            fee_paid=Decimal("0.50"),
            first_trade_at=datetime(2026, 3, 1, tzinfo=UTC),
            last_trade_at=datetime(2026, 3, 4, tzinfo=UTC),
        )
        summary = PortfolioSummary(
            total_realized_pnl=Decimal("0"),
            total_unrealized_pnl=Decimal("5"),
            total_fees=Decimal("0.50"),
            net_pnl=Decimal("4.50"),
            win_count=0,
            loss_count=0,
            win_rate=0.0,
            total_capital_deployed=Decimal("10"),
            roi=0.45,
            trade_count=1,
            avg_trade_size=Decimal("10"),
            positions=[pos],
        )
        assert len(summary.positions) == 1
        assert summary.roi == 0.45


class TestOptimalParamSnapshot:
    def test_construction(self) -> None:
        snap = OptimalParamSnapshot(
            category="nba",
            param_name="spike_threshold_pct",
            optimal_value=0.12,
            win_rate_at_optimal=0.68,
            sweep_date=datetime(2026, 3, 1, tzinfo=UTC),
        )
        assert snap.optimal_value == 0.12
