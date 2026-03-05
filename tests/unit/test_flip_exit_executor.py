"""Unit tests for FlipExitExecutor."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from arb_scanner.execution.flip_exit_executor import (
    FlipExitExecutor,
    _build_sell_request,
    _compute_realized_pnl,
)
from arb_scanner.models.execution import OrderResponse
from arb_scanner.models.flippening import (
    EntrySignal,
    ExitReason,
    ExitSignal,
    FlippeningEvent,
    SpikeDirection,
)

_NOW = datetime.now(timezone.utc)


def _make_event(market_id: str = "market-1") -> FlippeningEvent:
    return FlippeningEvent(
        market_id=market_id,
        market_title="Test Game",
        baseline_yes=Decimal("0.65"),
        spike_price=Decimal("0.45"),
        spike_magnitude_pct=Decimal("30"),
        spike_direction=SpikeDirection.FAVORITE_DROP,
        confidence=Decimal("0.9"),
        sport="basketball",
        detected_at=_NOW,
    )


def _make_entry() -> EntrySignal:
    return EntrySignal(
        event_id="evt-1",
        side="yes",
        entry_price=Decimal("0.45"),
        target_exit_price=Decimal("0.60"),
        stop_loss_price=Decimal("0.35"),
        suggested_size_usd=Decimal("100"),
        expected_profit_pct=Decimal("33"),
        max_hold_minutes=90,
        created_at=_NOW,
    )


def _make_exit(reason: ExitReason = ExitReason.REVERSION) -> ExitSignal:
    return ExitSignal(
        event_id="evt-1",
        side="yes",
        exit_price=Decimal("0.60"),
        exit_reason=reason,
        realized_pnl=Decimal("15"),
        realized_pnl_pct=Decimal("33"),
        hold_minutes=Decimal("45"),
        created_at=_NOW,
    )


def _make_position(
    market_id: str = "market-1",
    side: str = "yes",
    size_contracts: int = 200,
    entry_price: str = "0.45",
) -> dict:
    return {
        "market_id": market_id,
        "arb_id": "arb-1",
        "token_id": "token-abc",
        "side": side,
        "size_contracts": size_contracts,
        "entry_price": Decimal(entry_price),
        "status": "open",
    }


def _make_executor(
    position: dict | None = None,
    poly_resp: OrderResponse | None = None,
    stop_loss_aggression_pct: float = 0.02,
) -> tuple[FlipExitExecutor, MagicMock, MagicMock, MagicMock]:
    poly = MagicMock()
    poly.place_order = AsyncMock(
        return_value=poly_resp
        or OrderResponse(
            venue_order_id="poly-order-99",
            status="filled",
            fill_price=Decimal("0.60"),
        )
    )
    exec_repo = MagicMock()
    exec_repo.insert_order = AsyncMock()
    exec_repo.update_order_status = AsyncMock()

    position_repo = MagicMock()
    position_repo.get_open_position = AsyncMock(return_value=position)
    position_repo.close_position = AsyncMock()
    position_repo.mark_exit_failed = AsyncMock()

    executor = FlipExitExecutor(
        poly=poly,
        exec_repo=exec_repo,
        position_repo=position_repo,
        stop_loss_aggression_pct=stop_loss_aggression_pct,
    )
    return executor, poly, exec_repo, position_repo


class TestExecuteExitNoPosition:
    """execute_exit() when no open position exists."""

    @pytest.mark.asyncio()
    async def test_returns_none(self) -> None:
        """Returns None and skips order placement."""
        executor, poly, exec_repo, _ = _make_executor(position=None)
        result = await executor.execute_exit(_make_exit(), _make_entry(), _make_event())
        assert result is None
        poly.place_order.assert_not_awaited()
        exec_repo.insert_order.assert_not_awaited()


class TestExecuteExitSuccess:
    """execute_exit() happy-path execution."""

    @pytest.mark.asyncio()
    async def test_returns_order_id_string(self) -> None:
        """Returns a non-empty order ID on success."""
        executor, _, _, _ = _make_executor(position=_make_position())
        result = await executor.execute_exit(_make_exit(), _make_entry(), _make_event())
        assert result is not None
        assert len(result) > 0

    @pytest.mark.asyncio()
    async def test_calls_place_order(self) -> None:
        """Calls poly.place_order() once."""
        executor, poly, _, _ = _make_executor(position=_make_position())
        await executor.execute_exit(_make_exit(), _make_entry(), _make_event())
        poly.place_order.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_inserts_then_updates_order(self) -> None:
        """Inserts an order record before placement, then updates status."""
        executor, _, exec_repo, _ = _make_executor(position=_make_position())
        await executor.execute_exit(_make_exit(), _make_entry(), _make_event())
        exec_repo.insert_order.assert_awaited_once()
        exec_repo.update_order_status.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_closes_position(self) -> None:
        """Calls close_position() after a successful sell."""
        executor, _, _, position_repo = _make_executor(position=_make_position())
        await executor.execute_exit(_make_exit(), _make_entry(), _make_event())
        position_repo.close_position.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_close_position_exit_reason(self) -> None:
        """Passes the exit_reason string to close_position."""
        executor, _, _, position_repo = _make_executor(position=_make_position())
        await executor.execute_exit(_make_exit(ExitReason.TIMEOUT), _make_entry(), _make_event())
        _args = position_repo.close_position.call_args
        assert _args.kwargs["exit_reason"] == "timeout"


class TestExecuteExitFailure:
    """execute_exit() error handling."""

    @pytest.mark.asyncio()
    async def test_marks_exit_failed_on_exception(self) -> None:
        """Calls mark_exit_failed when place_order raises."""
        executor, poly, _, position_repo = _make_executor(position=_make_position())
        poly.place_order.side_effect = RuntimeError("network error")
        with pytest.raises(RuntimeError):
            await executor.execute_exit(_make_exit(), _make_entry(), _make_event())
        position_repo.mark_exit_failed.assert_awaited_once_with("market-1")

    @pytest.mark.asyncio()
    async def test_does_not_close_position_on_failure(self) -> None:
        """Does NOT call close_position when order fails."""
        executor, poly, _, position_repo = _make_executor(position=_make_position())
        poly.place_order.side_effect = RuntimeError("timeout")
        with pytest.raises(RuntimeError):
            await executor.execute_exit(_make_exit(), _make_entry(), _make_event())
        position_repo.close_position.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_updates_order_status_to_failed(self) -> None:
        """Updates order record to 'failed' status on exception."""
        executor, poly, exec_repo, _ = _make_executor(position=_make_position())
        poly.place_order.side_effect = RuntimeError("oops")
        with pytest.raises(RuntimeError):
            await executor.execute_exit(_make_exit(), _make_entry(), _make_event())
        # insert_order + update_order_status (failed) should both be called
        exec_repo.insert_order.assert_awaited_once()
        exec_repo.update_order_status.assert_awaited_once()
        _args = exec_repo.update_order_status.call_args
        assert _args.args[1] == "failed"


class TestBuildSellRequest:
    """_build_sell_request() unit tests."""

    def test_reversion_applies_base_aggression(self) -> None:
        """REVERSION exit applies base aggression discount to hit bids."""
        exit_sig = _make_exit(ExitReason.REVERSION)
        pos = _make_position(side="yes")
        req = _build_sell_request(pos, exit_sig, Decimal("0.02"))
        expected = (Decimal("0.60") * Decimal("0.98")).quantize(Decimal("0.0001"))
        assert req.price == expected

    def test_stop_loss_applies_double_aggression(self) -> None:
        """STOP_LOSS exit applies 2x aggression for faster fills."""
        exit_sig = _make_exit(ExitReason.STOP_LOSS)
        pos = _make_position(side="yes")
        req = _build_sell_request(pos, exit_sig, Decimal("0.02"))
        expected = (Decimal("0.60") * Decimal("0.96")).quantize(Decimal("0.0001"))
        assert req.price == expected

    def test_sell_yes_side(self) -> None:
        """Position with side='yes' produces 'sell_yes' order side."""
        req = _build_sell_request(_make_position(side="yes"), _make_exit(), Decimal("0"))
        assert req.side == "sell_yes"

    def test_sell_no_side(self) -> None:
        """Position with side='no' produces 'sell_no' order side."""
        req = _build_sell_request(_make_position(side="no"), _make_exit(), Decimal("0"))
        assert req.side == "sell_no"

    def test_size_contracts_from_position(self) -> None:
        """size_contracts comes from the position record."""
        req = _build_sell_request(_make_position(size_contracts=150), _make_exit(), Decimal("0"))
        assert req.size_contracts == 150

    def test_token_id_from_position(self) -> None:
        """token_id is taken from the position dict."""
        req = _build_sell_request(_make_position(), _make_exit(), Decimal("0"))
        assert req.token_id == "token-abc"

    def test_venue_is_polymarket(self) -> None:
        """Always targets Polymarket venue."""
        req = _build_sell_request(_make_position(), _make_exit(), Decimal("0"))
        assert req.venue == "polymarket"


class TestComputeRealizedPnl:
    """_compute_realized_pnl() calculation tests."""

    def test_profit(self) -> None:
        """Exit higher than entry is a profit."""
        pnl = _compute_realized_pnl(Decimal("0.45"), Decimal("0.60"), 100)
        assert pnl == Decimal("15")

    def test_loss(self) -> None:
        """Exit lower than entry is a loss."""
        pnl = _compute_realized_pnl(Decimal("0.60"), Decimal("0.45"), 100)
        assert pnl == Decimal("-15")

    def test_breakeven(self) -> None:
        """Same entry/exit price is zero P&L."""
        pnl = _compute_realized_pnl(Decimal("0.50"), Decimal("0.50"), 50)
        assert pnl == Decimal("0")

    def test_scales_with_contracts(self) -> None:
        """P&L scales linearly with number of contracts."""
        pnl = _compute_realized_pnl(Decimal("0.40"), Decimal("0.60"), 200)
        assert pnl == Decimal("40")
