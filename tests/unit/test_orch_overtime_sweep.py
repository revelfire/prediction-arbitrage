"""Tests for DB overtime sweep of active flip positions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.flippening._orch_processing import sweep_overtime_db_positions


def _position(*, status: str, opened_minutes_ago: int = 120) -> dict[str, object]:
    opened_at = datetime.now(UTC) - timedelta(minutes=opened_minutes_ago)
    return {
        "arb_id": "arb-1",
        "market_id": "mkt-1",
        "token_id": "tok-1",
        "side": "yes",
        "entry_price": Decimal("0.50"),
        "size_contracts": 100,
        "max_hold_minutes": 45,
        "opened_at": opened_at,
        "status": status,
    }


@pytest.mark.asyncio()
async def test_sweep_retries_exit_failed_positions() -> None:
    """exit_failed positions remain active and are retried by DB sweep."""
    pos_repo = AsyncMock()
    pos_repo.get_open_positions.return_value = [_position(status="exit_failed")]
    pipeline = MagicMock()
    pipeline._position_repo = pos_repo
    config = MagicMock()
    config._flip_pipeline = pipeline

    with patch(
        "arb_scanner.flippening._orch_exit._feed_exit_pipeline", new_callable=AsyncMock
    ) as fed:
        count = await sweep_overtime_db_positions(config)

    assert count == 1
    fed.assert_awaited_once()


@pytest.mark.asyncio()
async def test_sweep_uses_config_default_for_null_max_hold() -> None:
    """Positions with NULL max_hold_minutes use config default, not skipped."""
    pos = _position(status="open")
    pos["max_hold_minutes"] = None  # simulate pre-migration-029 position
    pos_repo = AsyncMock()
    pos_repo.get_open_positions.return_value = [pos]
    pipeline = MagicMock()
    pipeline._position_repo = pos_repo
    config = MagicMock()
    config._flip_pipeline = pipeline
    config.flippening.max_hold_minutes = 45

    with patch(
        "arb_scanner.flippening._orch_exit._feed_exit_pipeline", new_callable=AsyncMock
    ) as fed:
        count = await sweep_overtime_db_positions(config)

    assert count == 1
    fed.assert_awaited_once()


@pytest.mark.asyncio()
async def test_sweep_null_max_hold_not_overtime_yet() -> None:
    """NULL max_hold position opened recently is not closed yet."""
    pos = _position(status="open", opened_minutes_ago=10)
    pos["max_hold_minutes"] = None
    pos_repo = AsyncMock()
    pos_repo.get_open_positions.return_value = [pos]
    pipeline = MagicMock()
    pipeline._position_repo = pos_repo
    config = MagicMock()
    config._flip_pipeline = pipeline
    config.flippening.max_hold_minutes = 45

    with patch(
        "arb_scanner.flippening._orch_exit._feed_exit_pipeline", new_callable=AsyncMock
    ) as fed:
        count = await sweep_overtime_db_positions(config)

    assert count == 0
    fed.assert_not_awaited()


@pytest.mark.asyncio()
async def test_sweep_skips_exit_pending_positions() -> None:
    """exit_pending positions are handled by reconciliation, not overtime sweep."""
    pos_repo = AsyncMock()
    pos_repo.get_open_positions.return_value = [_position(status="exit_pending")]
    pipeline = MagicMock()
    pipeline._position_repo = pos_repo
    config = MagicMock()
    config._flip_pipeline = pipeline

    with patch(
        "arb_scanner.flippening._orch_exit._feed_exit_pipeline", new_callable=AsyncMock
    ) as fed:
        count = await sweep_overtime_db_positions(config)

    assert count == 0
    fed.assert_not_awaited()
