"""Unit tests for ExecutionRepository with mocked pool."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from arb_scanner.storage import _execution_queries as EQ
from arb_scanner.storage.execution_repository import ExecutionRepository


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Create a mock asyncpg pool."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    return pool


@pytest.fixture()
def repo(mock_pool: AsyncMock) -> ExecutionRepository:
    """Create an ExecutionRepository with mocked pool."""
    return ExecutionRepository(mock_pool)


class TestInsertOrder:
    """Tests for insert_order()."""

    @pytest.mark.asyncio()
    async def test_delegates_to_pool(self, repo: ExecutionRepository, mock_pool: AsyncMock) -> None:
        """Passes all params to pool.execute."""
        await repo.insert_order(
            order_id="o1",
            arb_id="t1",
            venue="polymarket",
            venue_order_id=None,
            side="buy_yes",
            requested_price=Decimal("0.55"),
            fill_price=None,
            size_usd=Decimal("10"),
            size_contracts=18,
            status="submitting",
            error_message=None,
        )
        mock_pool.execute.assert_awaited_once_with(
            EQ.INSERT_ORDER,
            "o1",
            "t1",
            "polymarket",
            None,
            "buy_yes",
            Decimal("0.55"),
            None,
            Decimal("10"),
            18,
            "submitting",
            None,
        )


class TestUpdateOrderStatus:
    """Tests for update_order_status()."""

    @pytest.mark.asyncio()
    async def test_updates_status(self, repo: ExecutionRepository, mock_pool: AsyncMock) -> None:
        """Passes correct args to pool.execute."""
        await repo.update_order_status(
            "o1",
            "filled",
            fill_price=Decimal("0.56"),
            venue_order_id="v1",
        )
        mock_pool.execute.assert_awaited_once_with(
            EQ.UPDATE_ORDER_STATUS,
            "o1",
            "filled",
            Decimal("0.56"),
            "v1",
            None,
        )


class TestGetOrdersForTicket:
    """Tests for get_orders_for_ticket()."""

    @pytest.mark.asyncio()
    async def test_returns_dicts(self, repo: ExecutionRepository, mock_pool: AsyncMock) -> None:
        """Rows are converted to dicts."""
        mock_pool.fetch.return_value = [{"id": "o1", "venue": "polymarket"}]
        result = await repo.get_orders_for_ticket("t1")
        assert result == [{"id": "o1", "venue": "polymarket"}]
        mock_pool.fetch.assert_awaited_once_with(EQ.GET_ORDERS_FOR_TICKET, "t1")


class TestGetOpenOrders:
    """Tests for get_open_orders()."""

    @pytest.mark.asyncio()
    async def test_returns_empty(self, repo: ExecutionRepository, mock_pool: AsyncMock) -> None:
        """Empty result when no open orders."""
        result = await repo.get_open_orders()
        assert result == []
        mock_pool.fetch.assert_awaited_once_with(EQ.GET_OPEN_ORDERS)


class TestCountOpenPositions:
    """Tests for count_open_positions()."""

    @pytest.mark.asyncio()
    async def test_returns_count(self, repo: ExecutionRepository, mock_pool: AsyncMock) -> None:
        """Returns integer count."""
        mock_pool.fetchrow.return_value = {"count": 3}
        result = await repo.count_open_positions()
        assert result == 3

    @pytest.mark.asyncio()
    async def test_returns_zero_on_none(
        self, repo: ExecutionRepository, mock_pool: AsyncMock
    ) -> None:
        """Returns 0 when no row."""
        mock_pool.fetchrow.return_value = None
        result = await repo.count_open_positions()
        assert result == 0


class TestInsertResult:
    """Tests for insert_result()."""

    @pytest.mark.asyncio()
    async def test_delegates_to_pool(self, repo: ExecutionRepository, mock_pool: AsyncMock) -> None:
        """Passes all params to pool.execute."""
        await repo.insert_result(
            result_id="r1",
            arb_id="t1",
            total_cost_usd=Decimal("20"),
            actual_spread=None,
            slippage_from_ticket=Decimal("0.03"),
            poly_order_id="o1",
            kalshi_order_id="o2",
            status="complete",
        )
        mock_pool.execute.assert_awaited_once_with(
            EQ.INSERT_RESULT,
            "r1",
            "t1",
            Decimal("20"),
            None,
            Decimal("0.03"),
            "o1",
            "o2",
            "complete",
        )


class TestGetResult:
    """Tests for get_result()."""

    @pytest.mark.asyncio()
    async def test_found(self, repo: ExecutionRepository, mock_pool: AsyncMock) -> None:
        """Returns dict when found."""
        mock_pool.fetchrow.return_value = {"id": "r1", "status": "complete"}
        result = await repo.get_result("t1")
        assert result == {"id": "r1", "status": "complete"}

    @pytest.mark.asyncio()
    async def test_not_found(self, repo: ExecutionRepository, mock_pool: AsyncMock) -> None:
        """Returns None when not found."""
        result = await repo.get_result("nonexistent")
        assert result is None


class TestGetDailyPnl:
    """Tests for get_daily_pnl()."""

    @pytest.mark.asyncio()
    async def test_returns_decimal(self, repo: ExecutionRepository, mock_pool: AsyncMock) -> None:
        """Returns Decimal from daily P&L query."""
        mock_pool.fetchrow.return_value = {"daily_pnl": "-25.50"}
        result = await repo.get_daily_pnl()
        assert result == Decimal("-25.50")

    @pytest.mark.asyncio()
    async def test_returns_zero_on_none(
        self, repo: ExecutionRepository, mock_pool: AsyncMock
    ) -> None:
        """Returns zero when no row."""
        mock_pool.fetchrow.return_value = None
        result = await repo.get_daily_pnl()
        assert result == Decimal("0")
