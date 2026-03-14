"""Tests for exit response status handling."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from arb_scanner.execution.flip_exit_executor import FlipExitExecutor
from arb_scanner.models.flippening import (
    EntrySignal,
    ExitReason,
    ExitSignal,
    FlippeningEvent,
    SpikeDirection,
)

_NOW_DT = __import__("datetime").datetime.now(tz=__import__("datetime").UTC)


def _position() -> dict[str, object]:
    return {
        "arb_id": "arb-1",
        "market_id": "mkt-1",
        "token_id": "tok-1",
        "side": "yes",
        "size_contracts": 100,
        "entry_price": Decimal("0.50"),
    }


def _exit_sig(reason: ExitReason = ExitReason.REVERSION) -> ExitSignal:
    return ExitSignal(
        event_id="e1",
        side="yes",
        exit_price=Decimal("0.60"),
        exit_reason=reason,
        realized_pnl=Decimal("10"),
        realized_pnl_pct=Decimal("0.20"),
        hold_minutes=Decimal("30"),
        created_at=_NOW_DT,
    )


def _entry_sig() -> EntrySignal:
    return EntrySignal(
        event_id="e1",
        side="yes",
        entry_price=Decimal("0.50"),
        target_exit_price=Decimal("0.60"),
        stop_loss_price=Decimal("0.42"),
        suggested_size_usd=Decimal("80"),
        expected_profit_pct=Decimal("0.20"),
        max_hold_minutes=45,
        created_at=_NOW_DT,
    )


def _event() -> FlippeningEvent:
    return FlippeningEvent(
        id="e1",
        market_id="mkt-1",
        token_id="tok-1",
        market_title="Test",
        baseline_yes=Decimal("0.65"),
        spike_price=Decimal("0.50"),
        spike_magnitude_pct=Decimal("23"),
        spike_direction=SpikeDirection.FAVORITE_DROP,
        confidence=Decimal("0.8"),
        sport="nba",
        detected_at=_NOW_DT,
    )


class TestExitStatusHandling:
    """Verify failed exit responses don't close positions."""

    @pytest.mark.asyncio
    async def test_failed_response_marks_exit_failed(self) -> None:
        """Failed order response marks position as exit_failed."""
        poly = AsyncMock()
        poly.place_order.return_value = SimpleNamespace(
            status="failed",
            fill_price=None,
            venue_order_id=None,
            error_message="order_rejected",
        )
        exec_repo = AsyncMock()
        pos_repo = AsyncMock()
        pos_repo.get_open_position.return_value = _position()

        executor = FlipExitExecutor(poly, exec_repo, pos_repo)
        result = await executor.execute_exit(_exit_sig(), _entry_sig(), _event())

        assert result is None
        pos_repo.mark_exit_failed.assert_called_once_with("mkt-1")
        pos_repo.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_filled_response_closes_position(self) -> None:
        """Filled order response closes position with P&L."""
        poly = AsyncMock()
        poly.place_order.return_value = SimpleNamespace(
            status="filled",
            fill_price=Decimal("0.58"),
            venue_order_id="v-1",
            error_message=None,
        )
        exec_repo = AsyncMock()
        pos_repo = AsyncMock()
        pos_repo.get_open_position.return_value = _position()

        executor = FlipExitExecutor(poly, exec_repo, pos_repo)
        result = await executor.execute_exit(_exit_sig(), _entry_sig(), _event())

        assert result is not None
        pos_repo.close_position.assert_called_once()
        pos_repo.mark_exit_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_submitted_response_marks_exit_pending(self) -> None:
        """Submitted without fill price leaves position as exit_pending."""
        poly = AsyncMock()
        poly.place_order.return_value = SimpleNamespace(
            status="submitted",
            fill_price=None,
            venue_order_id="v-2",
            error_message=None,
        )
        exec_repo = AsyncMock()
        pos_repo = AsyncMock()
        pos_repo.get_open_position.return_value = _position()

        executor = FlipExitExecutor(poly, exec_repo, pos_repo)
        result = await executor.execute_exit(_exit_sig(), _entry_sig(), _event())

        assert result is not None
        pos_repo.mark_exit_pending.assert_called_once()
        pos_repo.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_submitted_with_fill_price_closes_position(self) -> None:
        """Submitted + fill_price closes position using fill price."""
        poly = AsyncMock()
        poly.place_order.return_value = SimpleNamespace(
            status="submitted",
            fill_price=Decimal("0.57"),
            venue_order_id="v-3",
            error_message=None,
        )
        exec_repo = AsyncMock()
        pos_repo = AsyncMock()
        pos_repo.get_open_position.return_value = _position()

        executor = FlipExitExecutor(poly, exec_repo, pos_repo)
        result = await executor.execute_exit(_exit_sig(), _entry_sig(), _event())

        assert result is not None
        pos_repo.close_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_partially_filled_stays_pending(self) -> None:
        """Partially filled response remains exit_pending for later reconciliation."""
        poly = AsyncMock()
        poly.place_order.return_value = SimpleNamespace(
            status="partially_filled",
            fill_price=Decimal("0.57"),
            venue_order_id="v-4",
            error_message=None,
        )
        exec_repo = AsyncMock()
        pos_repo = AsyncMock()
        pos_repo.get_open_position.return_value = _position()

        executor = FlipExitExecutor(poly, exec_repo, pos_repo)
        result = await executor.execute_exit(_exit_sig(), _entry_sig(), _event())

        assert result is not None
        pos_repo.mark_exit_pending.assert_called_once()
        pos_repo.close_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_position_skips_gracefully(self) -> None:
        """No open position returns None without placing order."""
        poly = AsyncMock()
        exec_repo = AsyncMock()
        pos_repo = AsyncMock()
        pos_repo.get_open_position.return_value = None

        executor = FlipExitExecutor(poly, exec_repo, pos_repo)
        result = await executor.execute_exit(_exit_sig(), _entry_sig(), _event())

        assert result is None
        poly.place_order.assert_not_called()
