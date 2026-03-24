"""Unit tests for AutoExecRepository with mocked pool."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from arb_scanner.storage import _auto_exec_queries as AQ
from arb_scanner.storage.auto_exec_repository import AutoExecRepository


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Create a mock asyncpg pool."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    return pool


@pytest.fixture()
def repo(mock_pool: AsyncMock) -> AutoExecRepository:
    """Create an AutoExecRepository with mocked pool."""
    return AutoExecRepository(mock_pool)


class TestInit:
    """Tests for AutoExecRepository initialization."""

    def test_stores_pool(self, repo: AutoExecRepository, mock_pool: AsyncMock) -> None:
        """Pool reference is stored on init."""
        assert repo._pool is mock_pool


class TestInsertLog:
    """Tests for insert_log()."""

    @pytest.mark.asyncio()
    async def test_delegates_to_pool(self, repo: AutoExecRepository, mock_pool: AsyncMock) -> None:
        """Passes all params to pool.execute."""
        await repo.insert_log(
            log_id="log-1",
            arb_id="t1",
            trigger_spread_pct=Decimal("0.05"),
            trigger_confidence=Decimal("0.90"),
            criteria_snapshot={"reason": "test"},
            pre_exec_balances={"poly": "500", "kalshi": "400"},
            size_usd=Decimal("25.00"),
            critic_verdict={"approved": True},
            execution_result_id="r1",
            actual_spread=Decimal("0.04"),
            actual_pnl=Decimal("1.50"),
            slippage=Decimal("0.005"),
            duration_ms=150,
            circuit_breaker_state=[],
            status="executed",
            source="arb_watch",
        )
        mock_pool.execute.assert_awaited_once()
        call_args = mock_pool.execute.call_args
        assert call_args[0][0] == AQ.INSERT_LOG
        assert call_args[0][1] == "log-1"


class TestListLog:
    """Tests for list_log()."""

    @pytest.mark.asyncio()
    async def test_returns_dicts(self, repo: AutoExecRepository, mock_pool: AsyncMock) -> None:
        """Rows are converted to dicts."""
        mock_pool.fetch.return_value = [{"id": "log-1", "status": "executed"}]
        result = await repo.list_log(limit=10)
        assert result == [{"id": "log-1", "status": "executed"}]
        mock_pool.fetch.assert_awaited_once_with(AQ.LIST_LOG_DEDUPED, 10)


class TestGetLog:
    """Tests for get_log()."""

    @pytest.mark.asyncio()
    async def test_found(self, repo: AutoExecRepository, mock_pool: AsyncMock) -> None:
        """Returns dict when log entry found."""
        mock_pool.fetchrow.return_value = {"id": "log-1", "status": "executed"}
        result = await repo.get_log("log-1")
        assert result == {"id": "log-1", "status": "executed"}

    @pytest.mark.asyncio()
    async def test_not_found(self, repo: AutoExecRepository, mock_pool: AsyncMock) -> None:
        """Returns None when not found."""
        result = await repo.get_log("nonexistent")
        assert result is None


class TestUpdateLog:
    """Tests for update_log()."""

    @pytest.mark.asyncio()
    async def test_delegates_to_pool(self, repo: AutoExecRepository, mock_pool: AsyncMock) -> None:
        """Passes correct args to pool.execute."""
        await repo.update_log(
            "log-1",
            execution_result_id="r2",
            status="complete",
        )
        mock_pool.execute.assert_awaited_once()
        call_args = mock_pool.execute.call_args
        assert call_args[0][0] == AQ.UPDATE_LOG
        assert call_args[0][1] == "log-1"


class TestInsertPosition:
    """Tests for insert_position()."""

    @pytest.mark.asyncio()
    async def test_delegates_to_pool(self, repo: AutoExecRepository, mock_pool: AsyncMock) -> None:
        """Passes all params to pool.execute."""
        await repo.insert_position(
            position_id="p1",
            arb_id="t1",
            poly_market_id="m1",
            kalshi_ticker="KXTICKER",
            entry_spread=Decimal("0.05"),
            entry_cost_usd=Decimal("25.00"),
            status="open",
        )
        mock_pool.execute.assert_awaited_once()
        call_args = mock_pool.execute.call_args
        assert call_args[0][0] == AQ.INSERT_POSITION


class TestClosePosition:
    """Tests for close_position()."""

    @pytest.mark.asyncio()
    async def test_delegates_to_pool(self, repo: AutoExecRepository, mock_pool: AsyncMock) -> None:
        """Passes correct args to pool.execute."""
        await repo.close_position("p1", Decimal("30.00"))
        mock_pool.execute.assert_awaited_once_with(AQ.CLOSE_POSITION, "p1", Decimal("30.00"))


class TestGetOpenPositions:
    """Tests for get_open_positions()."""

    @pytest.mark.asyncio()
    async def test_returns_list(self, repo: AutoExecRepository, mock_pool: AsyncMock) -> None:
        """Returns list of position dicts."""
        mock_pool.fetch.return_value = [
            {"id": "p1", "arb_id": "t1", "status": "open"},
        ]
        result = await repo.get_open_positions()
        assert result == [{"id": "p1", "arb_id": "t1", "status": "open"}]
        mock_pool.fetch.assert_awaited_once_with(AQ.GET_OPEN_POSITIONS)

    @pytest.mark.asyncio()
    async def test_returns_empty(self, repo: AutoExecRepository, mock_pool: AsyncMock) -> None:
        """Returns empty list when no open positions."""
        result = await repo.get_open_positions()
        assert result == []


class TestGetDailyStats:
    """Tests for get_daily_stats()."""

    @pytest.mark.asyncio()
    async def test_returns_stats(self, repo: AutoExecRepository, mock_pool: AsyncMock) -> None:
        """Returns stats dict from fetchrow."""
        mock_pool.fetchrow.return_value = {
            "total_trades": 5,
            "wins": 3,
            "losses": 2,
            "total_pnl": Decimal("12.50"),
        }
        result = await repo.get_daily_stats(days=7)
        assert result["total_trades"] == 5
        assert result["total_pnl"] == Decimal("12.50")

    @pytest.mark.asyncio()
    async def test_returns_defaults_on_none(
        self, repo: AutoExecRepository, mock_pool: AsyncMock
    ) -> None:
        """Returns default zeros when no data."""
        mock_pool.fetchrow.return_value = None
        result = await repo.get_daily_stats()
        assert result["total_trades"] == 0
        assert result["total_pnl"] == Decimal("0")
        assert result["critic_rejections"] == 0
