"""Unit tests for TicketRepository with mocked pool."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from arb_scanner.storage import _ticket_queries as TQ
from arb_scanner.storage.ticket_repository import TicketRepository


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Create a mock asyncpg pool."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    return pool


@pytest.fixture()
def repo(mock_pool: AsyncMock) -> TicketRepository:
    """Create a TicketRepository with mocked pool."""
    return TicketRepository(mock_pool)


class TestGetTickets:
    """Tests for get_tickets()."""

    @pytest.mark.asyncio()
    async def test_default_params(self, repo: TicketRepository, mock_pool: AsyncMock) -> None:
        """Passes None defaults to the query."""
        await repo.get_tickets()
        mock_pool.fetch.assert_awaited_once_with(TQ.GET_TICKETS_FILTERED, None, None, None, 50)

    @pytest.mark.asyncio()
    async def test_with_filters(self, repo: TicketRepository, mock_pool: AsyncMock) -> None:
        """Passes filter params through."""
        await repo.get_tickets(status="pending", category="nba", ticket_type="flippening", limit=10)
        mock_pool.fetch.assert_awaited_once_with(
            TQ.GET_TICKETS_FILTERED, "pending", "nba", "flippening", 10
        )

    @pytest.mark.asyncio()
    async def test_returns_dicts(self, repo: TicketRepository, mock_pool: AsyncMock) -> None:
        """Rows are converted to dicts."""
        mock_row = MagicMock()
        mock_row.__iter__ = MagicMock(return_value=iter([("arb_id", "x")]))
        mock_row.items = MagicMock(return_value=[("arb_id", "x")])

        class FakeRecord(dict[str, Any]):
            pass

        mock_pool.fetch.return_value = [FakeRecord(arb_id="x", status="pending")]
        result = await repo.get_tickets()
        assert len(result) == 1
        assert result[0]["arb_id"] == "x"


class TestGetTicket:
    """Tests for get_ticket()."""

    @pytest.mark.asyncio()
    async def test_not_found(self, repo: TicketRepository, mock_pool: AsyncMock) -> None:
        """Returns None when ticket doesn't exist."""
        result = await repo.get_ticket("nonexistent")
        assert result is None

    @pytest.mark.asyncio()
    async def test_found(self, repo: TicketRepository, mock_pool: AsyncMock) -> None:
        """Returns dict when ticket exists."""

        class FakeRecord(dict[str, Any]):
            pass

        mock_pool.fetchrow.return_value = FakeRecord(arb_id="abc", status="pending")
        result = await repo.get_ticket("abc")
        assert result is not None
        assert result["arb_id"] == "abc"


class TestUpdateStatus:
    """Tests for update_status()."""

    @pytest.mark.asyncio()
    async def test_calls_execute(self, repo: TicketRepository, mock_pool: AsyncMock) -> None:
        """Delegates to pool.execute with correct args."""
        await repo.update_status("abc", "approved")
        mock_pool.execute.assert_awaited_once_with(TQ.UPDATE_TICKET_STATUS, "abc", "approved")


class TestInsertAction:
    """Tests for insert_action()."""

    @pytest.mark.asyncio()
    async def test_inserts_action(self, repo: TicketRepository, mock_pool: AsyncMock) -> None:
        """Inserts action with all fields."""
        await repo.insert_action(
            action_id="a1",
            ticket_id="t1",
            action="execute",
            actual_entry_price=Decimal("0.45"),
            actual_size_usd=Decimal("100"),
            slippage=Decimal("0.01"),
            notes="test",
        )
        mock_pool.execute.assert_awaited_once()
        args = mock_pool.execute.call_args[0]
        assert args[0] == TQ.INSERT_TICKET_ACTION
        assert args[1] == "a1"
        assert args[2] == "t1"
        assert args[3] == "execute"


class TestGetActions:
    """Tests for get_actions()."""

    @pytest.mark.asyncio()
    async def test_returns_list(self, repo: TicketRepository, mock_pool: AsyncMock) -> None:
        """Returns list of action dicts."""
        result = await repo.get_actions("t1")
        assert result == []
        mock_pool.fetch.assert_awaited_once_with(TQ.GET_TICKET_ACTIONS, "t1")


class TestGetSummary:
    """Tests for get_summary()."""

    @pytest.mark.asyncio()
    async def test_default_days(self, repo: TicketRepository, mock_pool: AsyncMock) -> None:
        """Default 30-day lookback."""
        await repo.get_summary()
        mock_pool.fetch.assert_awaited_once_with(TQ.GET_TICKET_SUMMARY, "30")


class TestAutoExpire:
    """Tests for auto_expire()."""

    @pytest.mark.asyncio()
    async def test_returns_expired_ids(self, repo: TicketRepository, mock_pool: AsyncMock) -> None:
        """Returns list of expired arb_ids."""

        class FakeRecord(dict[str, Any]):
            def __getitem__(self, key: str) -> Any:
                return super().__getitem__(key)

        mock_pool.fetch.return_value = [FakeRecord(arb_id="t1"), FakeRecord(arb_id="t2")]
        result = await repo.auto_expire(max_age_hours=12)
        assert result == ["t1", "t2"]
        mock_pool.fetch.assert_awaited_once_with(TQ.AUTO_EXPIRE_TICKETS, "12")
