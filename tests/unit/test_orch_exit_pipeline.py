"""Tests for exit pipeline helpers: _to_int, _to_decimal, reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.flippening._orch_processing import (
    _close_pending_position,
    _to_decimal,
    _to_int,
    reconcile_pending_db_positions,
    sweep_overtime_db_positions,
)


class TestToInt:
    """_to_int must handle all types returned by asyncpg for NUMERIC columns."""

    def test_none(self) -> None:
        assert _to_int(None) is None

    def test_plain_int(self) -> None:
        assert _to_int(42) == 42

    def test_int_string(self) -> None:
        assert _to_int("21") == 21

    def test_decimal_whole(self) -> None:
        """asyncpg returns NUMERIC(18,6) as Decimal('21.000000')."""
        assert _to_int(Decimal("21.000000")) == 21

    def test_decimal_string(self) -> None:
        """str(Decimal('31.000000')) == '31.000000' — must not raise."""
        assert _to_int("31.000000") == 31

    def test_float(self) -> None:
        assert _to_int(26.0) == 26

    def test_decimal_fractional(self) -> None:
        """Fractional Decimals truncate to int."""
        assert _to_int(Decimal("21.5")) == 21

    def test_invalid_string(self) -> None:
        assert _to_int("abc") is None

    def test_empty_string(self) -> None:
        assert _to_int("") is None


class TestToDecimal:
    """_to_decimal must handle values from asyncpg."""

    def test_none(self) -> None:
        assert _to_decimal(None) is None

    def test_empty_string(self) -> None:
        assert _to_decimal("") is None

    def test_decimal(self) -> None:
        assert _to_decimal(Decimal("0.37")) == Decimal("0.37")

    def test_float(self) -> None:
        result = _to_decimal(0.37)
        assert result is not None
        assert abs(float(result) - 0.37) < 0.001

    def test_string(self) -> None:
        assert _to_decimal("0.50") == Decimal("0.50")

    def test_invalid(self) -> None:
        assert _to_decimal("not_a_number") is None


def _exit_pending_position(
    *,
    entry_price: Any = Decimal("0.50"),
    size_contracts: Any = Decimal("21.000000"),
    exit_price: Any = Decimal("0.01"),
) -> dict[str, Any]:
    """Build a position dict matching asyncpg output for NUMERIC columns."""
    return {
        "id": "pos-1",
        "arb_id": "arb-1",
        "market_id": "0xabc123",
        "token_id": "tok-1",
        "side": "yes",
        "entry_price": entry_price,
        "size_contracts": size_contracts,
        "exit_price": exit_price,
        "exit_order_id": "order-1",
        "exit_reason": "timeout",
        "status": "exit_pending",
        "opened_at": datetime.now(UTC) - timedelta(hours=1),
        "max_hold_minutes": 45,
        "market_title": "Test Market",
        "market_slug": "test-market",
    }


class TestClosePendingPosition:
    """_close_pending_position must handle NUMERIC(18,6) Decimal values."""

    @pytest.mark.asyncio()
    async def test_closes_with_decimal_size_contracts(self) -> None:
        """Reproduces the production bug: Decimal('21.000000') size_contracts."""
        pos = _exit_pending_position()
        order = {"fill_price": Decimal("0.48"), "requested_price": Decimal("0.01")}
        pos_repo = AsyncMock()

        result = await _close_pending_position(
            pos,
            order=order,
            fill_price=Decimal("0.48"),
            pos_repo=pos_repo,
            exit_order_id="order-1",
        )

        assert result is True
        pos_repo.close_position.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_fails_with_none_entry_price(self) -> None:
        """Missing entry_price returns False without closing."""
        pos = _exit_pending_position(entry_price=None)
        order = {"fill_price": Decimal("0.48")}
        pos_repo = AsyncMock()

        result = await _close_pending_position(
            pos,
            order=order,
            fill_price=Decimal("0.48"),
            pos_repo=pos_repo,
            exit_order_id="order-1",
        )

        assert result is False
        pos_repo.close_position.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_uses_exit_price_fallback(self) -> None:
        """When fill_price is None, falls through to position exit_price."""
        pos = _exit_pending_position(exit_price=Decimal("0.45"))
        order: dict[str, Any] = {}
        pos_repo = AsyncMock()

        result = await _close_pending_position(
            pos,
            order=order,
            fill_price=None,
            pos_repo=pos_repo,
            exit_order_id="order-1",
        )

        assert result is True
        pos_repo.close_position.assert_awaited_once()
        call_kwargs = pos_repo.close_position.call_args.kwargs
        assert float(call_kwargs["exit_price"]) == pytest.approx(0.45, abs=0.01)


class TestReconcilePendingWithDecimalContracts:
    """End-to-end test: reconciliation must handle NUMERIC size_contracts."""

    @pytest.mark.asyncio()
    async def test_reconcile_closes_pending_position(self) -> None:
        """Full reconcile flow with Decimal(21.000000) size_contracts."""
        pos = _exit_pending_position()
        pos_repo = AsyncMock()
        pos_repo.get_exit_pending_positions.return_value = [pos]

        exec_repo = AsyncMock()
        exec_repo.get_order.return_value = {
            "id": "order-1",
            "status": "filled",
            "fill_price": Decimal("0.48"),
            "requested_price": Decimal("0.01"),
            "venue_order_id": "venue-1",
            "created_at": datetime.now(UTC),
        }
        exec_repo.update_order_status = AsyncMock()

        poly = AsyncMock()
        poly.get_order_status.return_value = None

        pipeline = MagicMock()
        pipeline._position_repo = pos_repo
        pipeline._exec_repo = exec_repo
        pipeline._poly = poly
        pipeline._exit_watchdog_metrics = None
        pipeline._ac = None  # use default retry policy

        config = MagicMock()
        config._flip_pipeline = pipeline

        resolved = await reconcile_pending_db_positions(config)
        assert resolved == 1
        pos_repo.close_position.assert_awaited_once()


class TestSweepWithDecimalSizeContracts:
    """Verify sweep works when position has NUMERIC(18,6) size_contracts."""

    @pytest.mark.asyncio()
    async def test_sweep_with_decimal_contracts(self) -> None:
        """Positions with Decimal size_contracts don't crash the sweep."""
        pos: dict[str, object] = {
            "arb_id": "arb-1",
            "market_id": "0xabc123",
            "token_id": "tok-1",
            "side": "yes",
            "entry_price": Decimal("0.37"),
            "size_contracts": Decimal("31.000000"),
            "max_hold_minutes": 37,
            "opened_at": datetime.now(UTC) - timedelta(hours=2),
            "status": "open",
        }
        pos_repo = AsyncMock()
        pos_repo.get_open_positions.return_value = [pos]
        pipeline = MagicMock()
        pipeline._position_repo = pos_repo
        config = MagicMock()
        config._flip_pipeline = pipeline
        config.flippening.max_hold_minutes = 45

        with patch(
            "arb_scanner.flippening._orch_exit._feed_exit_pipeline",
            new_callable=AsyncMock,
        ) as fed:
            count = await sweep_overtime_db_positions(config)

        assert count == 1
        fed.assert_awaited_once()
        # Verify the EntrySignal was built without error
        call_args = fed.call_args
        event = call_args[0][0]
        entry_sig = call_args[0][1]
        assert entry_sig.max_hold_minutes == 37
        assert event.market_id == "0xabc123"
