"""Unit tests for FlipPositionRepo with mocked pool."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from arb_scanner.execution.flip_position_repo import FlipPositionRepo
from arb_scanner.storage import _flip_position_queries as Q


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Mocked asyncpg pool."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return pool


@pytest.fixture()
def repo(mock_pool: AsyncMock) -> FlipPositionRepo:
    """FlipPositionRepo with mocked pool."""
    return FlipPositionRepo(mock_pool)


class TestInsertPosition:
    """Tests for insert_position()."""

    @pytest.mark.asyncio()
    async def test_returns_id(self, repo: FlipPositionRepo, mock_pool: AsyncMock) -> None:
        """Returns the new position id from DB row."""
        mock_pool.fetchrow.return_value = {"id": "pos-123"}
        result = await repo.insert_position(
            arb_id="arb-1",
            market_id="market-slug",
            token_id="token-abc",
            side="yes",
            size_contracts=100,
            entry_price=Decimal("0.37"),
        )
        assert result == "pos-123"

    @pytest.mark.asyncio()
    async def test_passes_correct_params(
        self, repo: FlipPositionRepo, mock_pool: AsyncMock
    ) -> None:
        """Passes all params to the INSERT query."""
        mock_pool.fetchrow.return_value = {"id": "x"}
        await repo.insert_position(
            arb_id="a1",
            market_id="m1",
            token_id="t1",
            side="no",
            size_contracts=50,
            entry_price=Decimal("0.63"),
            entry_order_id="order-uuid",
        )
        mock_pool.fetchrow.assert_awaited_once_with(
            Q.INSERT_POSITION,
            "a1",
            "m1",
            "t1",
            "no",
            50,
            Decimal("0.63"),
            "order-uuid",
            None,
            "",
            "",
        )


class TestGetOpenPosition:
    """Tests for get_open_position()."""

    @pytest.mark.asyncio()
    async def test_returns_dict_when_found(
        self, repo: FlipPositionRepo, mock_pool: AsyncMock
    ) -> None:
        """Returns a dict when a row is found."""
        row = MagicMock()
        row.__iter__ = MagicMock(return_value=iter([("market_id", "m1"), ("side", "yes")]))
        row.keys = MagicMock(return_value=["market_id", "side"])
        mock_pool.fetchrow.return_value = {"market_id": "m1", "side": "yes"}
        result = await repo.get_open_position("m1")
        assert result is not None
        assert result["market_id"] == "m1"

    @pytest.mark.asyncio()
    async def test_returns_none_when_missing(
        self, repo: FlipPositionRepo, mock_pool: AsyncMock
    ) -> None:
        """Returns None when no open position exists."""
        mock_pool.fetchrow.return_value = None
        result = await repo.get_open_position("unknown-market")
        assert result is None


class TestGetPositionByArbId:
    """Tests for get_position_by_arb_id()."""

    @pytest.mark.asyncio()
    async def test_returns_dict(self, repo: FlipPositionRepo, mock_pool: AsyncMock) -> None:
        """Returns dict when row found."""
        mock_pool.fetchrow.return_value = {"arb_id": "a1", "status": "open"}
        result = await repo.get_position_by_arb_id("a1")
        assert result is not None
        assert result["arb_id"] == "a1"

    @pytest.mark.asyncio()
    async def test_returns_none(self, repo: FlipPositionRepo, mock_pool: AsyncMock) -> None:
        """Returns None when no row found."""
        mock_pool.fetchrow.return_value = None
        assert await repo.get_position_by_arb_id("missing") is None


class TestClosePosition:
    """Tests for close_position()."""

    @pytest.mark.asyncio()
    async def test_executes_update(self, repo: FlipPositionRepo, mock_pool: AsyncMock) -> None:
        """Executes CLOSE_POSITION query with correct params."""
        await repo.close_position(
            "m1",
            exit_order_id="order-99",
            exit_price=Decimal("0.55"),
            realized_pnl=Decimal("18.00"),
            exit_reason="reversion",
        )
        mock_pool.execute.assert_awaited_once_with(
            Q.CLOSE_POSITION,
            "m1",
            "order-99",
            Decimal("0.55"),
            Decimal("18.00"),
            "reversion",
        )


class TestMarkExitFailed:
    """Tests for mark_exit_failed()."""

    @pytest.mark.asyncio()
    async def test_executes_update(self, repo: FlipPositionRepo, mock_pool: AsyncMock) -> None:
        """Executes MARK_EXIT_FAILED query."""
        await repo.mark_exit_failed("m1")
        mock_pool.execute.assert_awaited_once_with(Q.MARK_EXIT_FAILED, "m1")


class TestGetOrphanedPositions:
    """Tests for get_orphaned_positions()."""

    @pytest.mark.asyncio()
    async def test_returns_list(self, repo: FlipPositionRepo, mock_pool: AsyncMock) -> None:
        """Returns list of dicts."""
        mock_pool.fetch.return_value = [
            {"market_id": "m1", "side": "yes"},
            {"market_id": "m2", "side": "no"},
        ]
        result = await repo.get_orphaned_positions()
        assert len(result) == 2
        assert result[0]["market_id"] == "m1"

    @pytest.mark.asyncio()
    async def test_returns_empty_list(self, repo: FlipPositionRepo, mock_pool: AsyncMock) -> None:
        """Returns empty list when no orphans."""
        mock_pool.fetch.return_value = []
        assert await repo.get_orphaned_positions() == []
