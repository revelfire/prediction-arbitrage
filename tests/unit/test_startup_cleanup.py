"""Tests for startup cleanup of expired tickets and abandoned positions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture()
def mock_app() -> MagicMock:
    """Create a mock FastAPI app with state."""
    app = MagicMock()
    app.state.db = MagicMock()
    app.state.db.pool = AsyncMock()
    app.state.flip_position_repo = None
    app.state.auto_exec_repo = None
    return app


@pytest.fixture()
def mock_config() -> MagicMock:
    """Create a mock config."""
    config = MagicMock()
    config.ticket_lifecycle.max_pending_hours = 24
    return config


def _patch_ticket_repo(mock_repo: AsyncMock):  # type: ignore[no-untyped-def]
    """Patch TicketRepository constructor to return the mock."""
    return patch(
        "arb_scanner.storage.ticket_repository.TicketRepository.__new__",
        lambda cls, *a, **kw: mock_repo,
    )


class TestStartupCleanup:
    """Tests for _startup_cleanup in api/app.py."""

    @pytest.mark.asyncio
    async def test_expires_stale_tickets(
        self,
        mock_app: MagicMock,
        mock_config: MagicMock,
    ) -> None:
        """Startup expires pending tickets older than max_pending_hours."""
        from arb_scanner.api.app import _startup_cleanup

        mock_app.state.db.pool.fetch = AsyncMock(
            return_value=[{"arb_id": "a1"}, {"arb_id": "a2"}],
        )
        await _startup_cleanup(mock_app, mock_config)
        # Verify pool.fetch was called (auto_expire uses pool.fetch)
        mock_app.state.db.pool.fetch.assert_awaited()

    @pytest.mark.asyncio
    async def test_does_not_abandon_flip_positions_at_startup(
        self,
        mock_app: MagicMock,
        mock_config: MagicMock,
    ) -> None:
        """Startup must NOT abandon flip positions (periodic sweep handles it)."""
        from arb_scanner.api.app import _startup_cleanup

        flip_repo = AsyncMock()
        mock_app.state.flip_position_repo = flip_repo
        mock_app.state.db.pool.fetch = AsyncMock(return_value=[])

        await _startup_cleanup(mock_app, mock_config)
        flip_repo.abandon_expired.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_abandons_expired_arb_positions(
        self,
        mock_app: MagicMock,
        mock_config: MagicMock,
    ) -> None:
        """Startup abandons arb positions past max_hold_minutes."""
        from arb_scanner.api.app import _startup_cleanup

        auto_repo = AsyncMock()
        auto_repo.abandon_expired.return_value = [
            {
                "id": "p2",
                "arb_id": "a2",
                "poly_market_id": "pm1",
                "kalshi_ticker": "KX1",
                "max_hold_minutes": 60,
            },
        ]
        mock_app.state.auto_exec_repo = auto_repo
        mock_app.state.db.pool.fetch = AsyncMock(return_value=[])

        await _startup_cleanup(mock_app, mock_config)
        auto_repo.abandon_expired.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_no_pool(
        self,
        mock_config: MagicMock,
    ) -> None:
        """Startup cleanup exits gracefully when DB pool is unavailable."""
        from arb_scanner.api.app import _startup_cleanup

        app = MagicMock()
        type(app.state.db).pool = property(
            fget=lambda _: (_ for _ in ()).throw(RuntimeError("no pool")),
        )
        # Should not raise
        await _startup_cleanup(app, mock_config)

    @pytest.mark.asyncio
    async def test_continues_on_partial_failure(
        self,
        mock_app: MagicMock,
        mock_config: MagicMock,
    ) -> None:
        """If ticket expiry fails, remaining cleanup still runs."""
        from arb_scanner.api.app import _startup_cleanup

        auto_repo = AsyncMock()
        auto_repo.abandon_expired.return_value = []
        mock_app.state.auto_exec_repo = auto_repo
        mock_app.state.db.pool.fetch = AsyncMock(
            side_effect=RuntimeError("db error"),
        )

        await _startup_cleanup(mock_app, mock_config)
        # Arb abandon still ran despite ticket expiry error
        auto_repo.abandon_expired.assert_awaited_once()
