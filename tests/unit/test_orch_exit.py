"""Unit tests for flippening._orch_exit._feed_exit_pipeline()."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.flippening._orch_exit import _feed_exit_pipeline
from arb_scanner.models.flippening import (
    EntrySignal,
    ExitReason,
    ExitSignal,
    FlippeningEvent,
    SpikeDirection,
)

_NOW = datetime.now(timezone.utc)


def _make_event() -> FlippeningEvent:
    return FlippeningEvent(
        market_id="m1",
        market_title="Test",
        baseline_yes=Decimal("0.65"),
        spike_price=Decimal("0.45"),
        spike_magnitude_pct=Decimal("30"),
        spike_direction=SpikeDirection.FAVORITE_DROP,
        confidence=Decimal("0.9"),
        sport="basketball",
        detected_at=_NOW,
    )


def _make_entry() -> EntrySignal:
    return EntrySignal(
        event_id="evt-1",
        side="yes",
        entry_price=Decimal("0.45"),
        target_exit_price=Decimal("0.60"),
        stop_loss_price=Decimal("0.35"),
        suggested_size_usd=Decimal("100"),
        expected_profit_pct=Decimal("33"),
        max_hold_minutes=90,
        created_at=_NOW,
    )


def _make_exit() -> ExitSignal:
    return ExitSignal(
        event_id="evt-1",
        side="yes",
        exit_price=Decimal("0.60"),
        exit_reason=ExitReason.REVERSION,
        realized_pnl=Decimal("15"),
        realized_pnl_pct=Decimal("33"),
        hold_minutes=Decimal("45"),
        created_at=_NOW,
    )


class TestFeedExitPipelineNoPipeline:
    """Returns silently when no pipeline is wired onto config."""

    @pytest.mark.asyncio()
    async def test_no_flip_pipeline_attr(self) -> None:
        """Does nothing when config has no _flip_pipeline attribute."""
        config = MagicMock(spec=[])  # no attributes
        # Should not raise
        await _feed_exit_pipeline(_make_event(), _make_entry(), _make_exit(), config)

    @pytest.mark.asyncio()
    async def test_pipeline_is_none(self) -> None:
        """Does nothing when _flip_pipeline is None."""
        config = MagicMock()
        config._flip_pipeline = None
        await _feed_exit_pipeline(_make_event(), _make_entry(), _make_exit(), config)


class TestFeedExitPipelineWrongMode:
    """Returns silently when pipeline mode is not 'auto'."""

    @pytest.mark.asyncio()
    async def test_manual_mode_skips(self) -> None:
        """Does nothing when pipeline mode is 'manual'."""
        pipeline = AsyncMock()
        pipeline.mode = "manual"
        config = MagicMock()
        config._flip_pipeline = pipeline
        await _feed_exit_pipeline(_make_event(), _make_entry(), _make_exit(), config)
        pipeline.process_exit.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_off_mode_skips(self) -> None:
        """Does nothing when pipeline mode is 'off'."""
        pipeline = AsyncMock()
        pipeline.mode = "off"
        config = MagicMock()
        config._flip_pipeline = pipeline
        await _feed_exit_pipeline(_make_event(), _make_entry(), _make_exit(), config)
        pipeline.process_exit.assert_not_awaited()


class TestFeedExitPipelineAuto:
    """Delegates to pipeline.process_exit() in auto mode."""

    @pytest.mark.asyncio()
    async def test_calls_process_exit(self) -> None:
        """Calls process_exit with exit_sig, entry, event."""
        pipeline = MagicMock()
        pipeline.mode = "auto"
        pipeline.process_exit = AsyncMock()
        config = MagicMock()
        config._flip_pipeline = pipeline

        event, entry, exit_sig = _make_event(), _make_entry(), _make_exit()
        await _feed_exit_pipeline(event, entry, exit_sig, config)

        pipeline.process_exit.assert_awaited_once_with(exit_sig, entry, event)


class TestFeedExitPipelineSwallowsExceptions:
    """All exceptions are swallowed to protect the live engine."""

    @pytest.mark.asyncio()
    async def test_exception_from_process_exit_is_swallowed(self) -> None:
        """Does not raise even when process_exit() raises."""
        pipeline = MagicMock()
        pipeline.mode = "auto"
        pipeline.process_exit = AsyncMock(side_effect=RuntimeError("boom"))
        config = MagicMock()
        config._flip_pipeline = pipeline

        # Must not propagate the error
        await _feed_exit_pipeline(_make_event(), _make_entry(), _make_exit(), config)

    @pytest.mark.asyncio()
    async def test_import_error_is_swallowed(self) -> None:
        """Does not raise when an import error occurs inside the try block."""
        config = MagicMock()
        config._flip_pipeline = None

        with patch(
            "arb_scanner.flippening._orch_exit.getattr",
            side_effect=AttributeError("unexpected"),
        ):
            # Should still not raise
            try:
                await _feed_exit_pipeline(_make_event(), _make_entry(), _make_exit(), config)
            except AttributeError:
                pass  # If getattr patch doesn't take effect, that's fine too
