"""Tests for periodic re-feeding of active flip signals to auto-exec pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.flippening._orch_processing import retry_active_signals
from arb_scanner.flippening.game_manager import GameManager
from arb_scanner.models.flippening import (
    EntrySignal,
    FlippeningEvent,
    SpikeDirection,
)


def _make_event(market_id: str = "m1", confidence: float = 0.80) -> FlippeningEvent:
    return FlippeningEvent(
        id=f"evt-{market_id}",
        market_id=market_id,
        token_id=f"tok-{market_id}",
        market_title=f"Test {market_id}",
        baseline_yes=Decimal("0.70"),
        spike_price=Decimal("0.50"),
        spike_magnitude_pct=Decimal("0.15"),
        spike_direction=SpikeDirection.FAVORITE_DROP,
        confidence=Decimal(str(confidence)),
        sport="test",
        category="test",
        detected_at=datetime.now(UTC),
    )


def _make_entry(event_id: str = "evt-m1") -> EntrySignal:
    return EntrySignal(
        event_id=event_id,
        side="yes",
        entry_price=Decimal("0.50"),
        target_exit_price=Decimal("0.65"),
        stop_loss_price=Decimal("0.42"),
        suggested_size_usd=Decimal("50"),
        expected_profit_pct=Decimal("0.30"),
        max_hold_minutes=120,
        created_at=datetime.now(UTC),
    )


def _make_config(
    *,
    open_positions: list[dict[str, object]] | None = None,
    pipeline_mode: str = "auto",
) -> MagicMock:
    pos_repo = AsyncMock()
    pos_repo.get_open_positions.return_value = open_positions or []

    pipeline = MagicMock()
    pipeline._mode = pipeline_mode
    pipeline._position_repo = pos_repo
    pipeline.mode = pipeline_mode
    pipeline.process_opportunity = AsyncMock(return_value=None)

    config = MagicMock()
    config._flip_pipeline = pipeline
    return config


@pytest.mark.asyncio()
async def test_retries_signal_without_open_position() -> None:
    """Active signal with no open position is re-fed to the pipeline."""
    game_mgr = MagicMock(spec=GameManager)
    event = _make_event()
    entry = _make_entry()
    state = MagicMock()
    state.active_signal = entry
    state.active_event = event
    state.market_id = "m1"
    state.market_slug = "test-slug"
    game_mgr.iter_active_signals.return_value = [("m1", state)]

    config = _make_config(open_positions=[])

    with patch(
        "arb_scanner.flippening._orch_processing._feed_auto_pipeline",
        new_callable=AsyncMock,
    ) as mock_feed:
        fed = await retry_active_signals(game_mgr, config)

    assert fed == 1
    mock_feed.assert_awaited_once()
    call_args = mock_feed.await_args
    assert call_args.args[0] is event
    assert call_args.args[1] is entry


@pytest.mark.asyncio()
async def test_skips_signal_with_existing_position() -> None:
    """Signal for market that already has an open position is NOT re-fed."""
    game_mgr = MagicMock(spec=GameManager)
    event = _make_event()
    entry = _make_entry()
    state = MagicMock()
    state.active_signal = entry
    state.active_event = event
    state.market_id = "m1"
    game_mgr.iter_active_signals.return_value = [("m1", state)]

    config = _make_config(open_positions=[{"market_id": "m1", "status": "open"}])

    with patch(
        "arb_scanner.flippening._orch_processing._feed_auto_pipeline",
        new_callable=AsyncMock,
    ) as mock_feed:
        fed = await retry_active_signals(game_mgr, config)

    assert fed == 0
    mock_feed.assert_not_awaited()


@pytest.mark.asyncio()
async def test_skips_signal_without_stored_event() -> None:
    """Signal without a stored FlippeningEvent (legacy) is skipped."""
    game_mgr = MagicMock(spec=GameManager)
    state = MagicMock()
    state.active_signal = _make_entry()
    state.active_event = None  # No stored event
    state.market_id = "m1"
    game_mgr.iter_active_signals.return_value = [("m1", state)]

    config = _make_config()

    with patch(
        "arb_scanner.flippening._orch_processing._feed_auto_pipeline",
        new_callable=AsyncMock,
    ) as mock_feed:
        fed = await retry_active_signals(game_mgr, config)

    assert fed == 0
    mock_feed.assert_not_awaited()


@pytest.mark.asyncio()
async def test_returns_zero_when_pipeline_off() -> None:
    """Returns 0 when pipeline mode is not auto."""
    game_mgr = MagicMock(spec=GameManager)
    config = _make_config(pipeline_mode="off")

    fed = await retry_active_signals(game_mgr, config)

    assert fed == 0


@pytest.mark.asyncio()
async def test_returns_zero_when_no_active_signals() -> None:
    """Returns 0 when game manager has no active signals."""
    game_mgr = MagicMock(spec=GameManager)
    game_mgr.iter_active_signals.return_value = []
    config = _make_config()

    fed = await retry_active_signals(game_mgr, config)

    assert fed == 0
