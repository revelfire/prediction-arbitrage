"""Tests for reconciling open DB positions against exchange token balances."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from arb_scanner.flippening._orch_processing import (
    reconcile_open_positions_with_exchange,
)


def _make_config(
    *,
    positions: list[dict[str, object]],
    token_balances: dict[str, int],
) -> tuple[MagicMock, AsyncMock, AsyncMock]:
    """Build a mock config with pipeline, position repo, and poly executor."""
    pos_repo = AsyncMock()
    pos_repo.get_open_positions.return_value = positions

    poly = AsyncMock()

    async def _get_token_balance(token_id: str) -> int:
        return token_balances.get(token_id, -1)

    poly.get_token_balance = _get_token_balance

    pipeline = MagicMock()
    pipeline._position_repo = pos_repo
    pipeline._poly = poly

    config = MagicMock()
    config._flip_pipeline = pipeline
    return config, pos_repo, poly


@pytest.mark.asyncio()
async def test_closes_position_with_zero_balance() -> None:
    """Position with zero token balance on exchange is closed in DB."""
    config, pos_repo, _poly = _make_config(
        positions=[
            {
                "market_id": "m1",
                "token_id": "tok-1",
                "entry_price": Decimal("0.50"),
                "size_contracts": 100,
                "status": "open",
            },
        ],
        token_balances={"tok-1": 0},
    )

    closed = await reconcile_open_positions_with_exchange(config)

    assert closed == 1
    pos_repo.close_position.assert_awaited_once()
    call_kwargs = pos_repo.close_position.await_args.kwargs
    assert call_kwargs["exit_reason"] == "reconciled_no_balance"


@pytest.mark.asyncio()
async def test_keeps_position_with_nonzero_balance() -> None:
    """Position with tokens still held on exchange is not closed."""
    config, pos_repo, _poly = _make_config(
        positions=[
            {
                "market_id": "m2",
                "token_id": "tok-2",
                "entry_price": Decimal("0.60"),
                "size_contracts": 50,
                "status": "open",
            },
        ],
        token_balances={"tok-2": 50},
    )

    closed = await reconcile_open_positions_with_exchange(config)

    assert closed == 0
    pos_repo.close_position.assert_not_awaited()


@pytest.mark.asyncio()
async def test_skips_position_on_balance_error() -> None:
    """Position is skipped when token balance query fails (-1)."""
    config, pos_repo, _poly = _make_config(
        positions=[
            {
                "market_id": "m3",
                "token_id": "tok-3",
                "entry_price": Decimal("0.40"),
                "size_contracts": 30,
                "status": "open",
            },
        ],
        token_balances={},  # tok-3 not in map → returns -1
    )

    closed = await reconcile_open_positions_with_exchange(config)

    assert closed == 0
    pos_repo.close_position.assert_not_awaited()


@pytest.mark.asyncio()
async def test_reconciles_mixed_positions() -> None:
    """Multiple positions: closes stale ones, keeps active ones."""
    config, pos_repo, _poly = _make_config(
        positions=[
            {
                "market_id": "m1",
                "token_id": "tok-1",
                "entry_price": Decimal("0.50"),
                "size_contracts": 100,
                "status": "open",
            },
            {
                "market_id": "m2",
                "token_id": "tok-2",
                "entry_price": Decimal("0.60"),
                "size_contracts": 50,
                "status": "exit_failed",
            },
            {
                "market_id": "m3",
                "token_id": "tok-3",
                "entry_price": Decimal("0.40"),
                "size_contracts": 30,
                "status": "open",
            },
        ],
        token_balances={"tok-1": 0, "tok-2": 50, "tok-3": 0},
    )

    closed = await reconcile_open_positions_with_exchange(config)

    assert closed == 2
    assert pos_repo.close_position.await_count == 2


@pytest.mark.asyncio()
async def test_returns_zero_when_no_pipeline() -> None:
    """Returns 0 when no flip pipeline is configured."""
    config = MagicMock()
    config._flip_pipeline = None

    closed = await reconcile_open_positions_with_exchange(config)

    assert closed == 0


@pytest.mark.asyncio()
async def test_returns_zero_when_no_open_positions() -> None:
    """Returns 0 when DB has no open positions."""
    config, pos_repo, _poly = _make_config(positions=[], token_balances={})

    closed = await reconcile_open_positions_with_exchange(config)

    assert closed == 0
