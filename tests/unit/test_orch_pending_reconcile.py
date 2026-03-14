"""Tests for reconciling flip exit_pending positions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from arb_scanner.flippening._orch_processing import reconcile_pending_db_positions
from arb_scanner.models.execution import OrderResponse


def _make_config(
    *,
    positions: list[dict[str, object]],
    order: dict[str, object] | None,
    venue_status: OrderResponse | None = None,
    max_retry_attempts: int = 3,
    stale_seconds: int = 45,
) -> tuple[MagicMock, AsyncMock, AsyncMock, AsyncMock]:
    pos_repo = AsyncMock()
    pos_repo.get_exit_pending_positions.return_value = positions
    exec_repo = AsyncMock()
    exec_repo.get_order.return_value = order
    poly = AsyncMock()
    if venue_status is not None:
        poly.get_order_status.return_value = venue_status

    pipeline = MagicMock()
    pipeline._position_repo = pos_repo
    pipeline._exec_repo = exec_repo
    pipeline._poly = poly
    pipeline._exit_watchdog_metrics = MagicMock()
    pipeline._ac = MagicMock()
    pipeline._ac.exit_pending_stale_seconds = stale_seconds
    pipeline._ac.exit_retry_max_attempts = max_retry_attempts
    pipeline._ac.exit_retry_reprice_pct = 0.015
    pipeline._ac.exit_retry_min_price = 0.01

    config = MagicMock()
    config._flip_pipeline = pipeline
    return config, pos_repo, exec_repo, poly


@pytest.mark.asyncio()
async def test_reconcile_closes_filled_pending_position() -> None:
    config, pos_repo, exec_repo, _poly = _make_config(
        positions=[
            {
                "market_id": "m1",
                "exit_order_id": "o1",
                "entry_price": Decimal("0.40"),
                "size_contracts": 100,
                "exit_reason": "timeout",
                "exit_price": Decimal("0.45"),
            },
        ],
        order={
            "id": "o1",
            "status": "submitted",
            "venue_order_id": "v1",
            "fill_price": None,
        },
        venue_status=OrderResponse(
            venue_order_id="v1",
            status="filled",
            fill_price=Decimal("0.48"),
        ),
    )

    resolved = await reconcile_pending_db_positions(config)

    assert resolved == 1
    exec_repo.update_order_status.assert_awaited_once()
    pos_repo.close_position.assert_awaited_once()
    args = pos_repo.close_position.await_args.args
    kwargs = pos_repo.close_position.await_args.kwargs
    assert args[0] == "m1"
    assert kwargs["exit_order_id"] == "o1"
    assert kwargs["exit_price"] == Decimal("0.48")
    assert kwargs["realized_pnl"] == Decimal("8.00")


@pytest.mark.asyncio()
async def test_reconcile_marks_exit_failed() -> None:
    config, pos_repo, exec_repo, _poly = _make_config(
        positions=[
            {
                "market_id": "m2",
                "exit_order_id": "o2",
                "entry_price": Decimal("0.60"),
                "size_contracts": 50,
            },
        ],
        order={
            "id": "o2",
            "status": "submitted",
            "venue_order_id": "v2",
            "fill_price": None,
        },
        venue_status=OrderResponse(
            venue_order_id="v2",
            status="failed",
            error_message="rejected",
        ),
    )

    resolved = await reconcile_pending_db_positions(config)

    assert resolved == 1
    exec_repo.update_order_status.assert_awaited_once()
    pos_repo.mark_exit_failed.assert_awaited_once_with("m2")
    pos_repo.close_position.assert_not_awaited()


@pytest.mark.asyncio()
async def test_reconcile_keeps_pending_when_still_submitted() -> None:
    config, pos_repo, exec_repo, _poly = _make_config(
        positions=[
            {
                "market_id": "m3",
                "exit_order_id": "o3",
                "entry_price": Decimal("0.50"),
                "size_contracts": 30,
            },
        ],
        order={
            "id": "o3",
            "status": "submitted",
            "venue_order_id": "v3",
            "fill_price": None,
        },
        venue_status=OrderResponse(
            venue_order_id="v3",
            status="submitted",
            fill_price=None,
        ),
    )

    resolved = await reconcile_pending_db_positions(config)

    assert resolved == 0
    exec_repo.update_order_status.assert_awaited_once()
    pos_repo.mark_exit_failed.assert_not_awaited()
    pos_repo.close_position.assert_not_awaited()


@pytest.mark.asyncio()
async def test_reconcile_uses_local_filled_without_venue_fetch() -> None:
    config, pos_repo, exec_repo, poly = _make_config(
        positions=[
            {
                "market_id": "m4",
                "exit_order_id": "o4",
                "entry_price": Decimal("0.20"),
                "size_contracts": 10,
                "exit_reason": "timeout",
            },
        ],
        order={
            "id": "o4",
            "status": "filled",
            "venue_order_id": "",
            "fill_price": Decimal("0.30"),
        },
    )

    resolved = await reconcile_pending_db_positions(config)

    assert resolved == 1
    poly.get_order_status.assert_not_awaited()
    pos_repo.close_position.assert_awaited_once()


@pytest.mark.asyncio()
async def test_reconcile_retries_stale_pending_exit_with_reprice() -> None:
    old = datetime.now(UTC) - timedelta(seconds=120)
    config, pos_repo, exec_repo, poly = _make_config(
        positions=[
            {
                "arb_id": "arb-5",
                "market_id": "m5",
                "exit_order_id": "o5",
                "entry_price": Decimal("0.50"),
                "size_contracts": 100,
                "side": "yes",
                "token_id": "tok-5",
                "exit_price": Decimal("0.50"),
                "exit_reason": "timeout",
            },
        ],
        order={
            "id": "o5",
            "status": "submitted",
            "venue_order_id": "v5",
            "requested_price": Decimal("0.50"),
            "created_at": old,
            "updated_at": old,
            "fill_price": None,
        },
        venue_status=OrderResponse(
            venue_order_id="v5",
            status="submitted",
            fill_price=None,
        ),
    )
    exec_repo.get_orders_for_ticket.return_value = [{"side": "sell_yes"}]
    poly.cancel_order.return_value = True
    poly.place_order.return_value = OrderResponse(
        venue_order_id="v5-retry",
        status="submitted",
        fill_price=None,
    )

    resolved = await reconcile_pending_db_positions(config)

    assert resolved == 0
    poly.cancel_order.assert_awaited_once_with("v5")
    poly.place_order.assert_awaited_once()
    req = poly.place_order.await_args.args[0]
    assert req.price < Decimal("0.50")
    pos_repo.mark_exit_pending.assert_awaited_once()
    metrics = config._flip_pipeline._exit_watchdog_metrics
    metrics.incr.assert_any_call("stale_detected")
    metrics.incr.assert_any_call("retries_placed")


@pytest.mark.asyncio()
async def test_reconcile_marks_failed_when_retry_budget_exhausted() -> None:
    old = datetime.now(UTC) - timedelta(seconds=120)
    config, pos_repo, exec_repo, poly = _make_config(
        positions=[
            {
                "arb_id": "arb-6",
                "market_id": "m6",
                "exit_order_id": "o6",
                "entry_price": Decimal("0.50"),
                "size_contracts": 100,
                "side": "yes",
                "token_id": "tok-6",
            },
        ],
        order={
            "id": "o6",
            "status": "submitted",
            "venue_order_id": "v6",
            "requested_price": Decimal("0.40"),
            "created_at": old,
            "updated_at": old,
            "fill_price": None,
        },
        venue_status=OrderResponse(
            venue_order_id="v6",
            status="submitted",
            fill_price=None,
        ),
        max_retry_attempts=1,
    )
    exec_repo.get_orders_for_ticket.return_value = [{"side": "sell_yes"}]
    poly.cancel_order.return_value = True

    resolved = await reconcile_pending_db_positions(config)

    assert resolved == 1
    poly.cancel_order.assert_awaited_once_with("v6")
    poly.place_order.assert_not_awaited()
    pos_repo.mark_exit_failed.assert_awaited_once_with("m6")


@pytest.mark.asyncio()
async def test_reconcile_keeps_pending_when_cancel_fails() -> None:
    old = datetime.now(UTC) - timedelta(seconds=120)
    config, pos_repo, exec_repo, poly = _make_config(
        positions=[
            {
                "arb_id": "arb-7",
                "market_id": "m7",
                "exit_order_id": "o7",
                "entry_price": Decimal("0.50"),
                "size_contracts": 100,
                "side": "yes",
                "token_id": "tok-7",
            },
        ],
        order={
            "id": "o7",
            "status": "submitted",
            "venue_order_id": "v7",
            "requested_price": Decimal("0.40"),
            "created_at": old,
            "updated_at": old,
            "fill_price": None,
        },
        venue_status=OrderResponse(
            venue_order_id="v7",
            status="submitted",
            fill_price=None,
        ),
    )
    exec_repo.get_orders_for_ticket.return_value = [{"side": "sell_yes"}]
    poly.cancel_order.return_value = False

    resolved = await reconcile_pending_db_positions(config)

    assert resolved == 0
    poly.place_order.assert_not_awaited()
    pos_repo.mark_exit_failed.assert_not_awaited()
