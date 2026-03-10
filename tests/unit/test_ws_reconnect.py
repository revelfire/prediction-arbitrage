"""Tests for WebSocket stall detection triggering reconnect."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from arb_scanner.flippening._orch_telemetry import (
    STALL_THRESHOLD,
    _handle_stall,
)
from arb_scanner.flippening.ws_telemetry import WsTelemetry


class TestStallReconnect:
    """Verify stall detection triggers reconnect with backoff."""

    @pytest.mark.asyncio
    async def test_reconnect_called_on_stall_threshold(self) -> None:
        """Stream reconnect is called when stall count reaches threshold."""
        telemetry = WsTelemetry()
        stream = MagicMock()
        stream.reconnect = AsyncMock()

        stall_count = STALL_THRESHOLD - 1
        last_received = telemetry.cum_received

        stall_count, _, last_reconnect = await _handle_stall(
            telemetry, stall_count, last_received, stream, 0.0, 0.0
        )
        stream.reconnect.assert_called_once()
        assert stall_count == 0
        assert last_reconnect > 0

    @pytest.mark.asyncio
    async def test_no_reconnect_before_threshold(self) -> None:
        """No reconnect when stall count is below threshold."""
        telemetry = WsTelemetry()
        stream = MagicMock()
        stream.reconnect = AsyncMock()

        stall_count, _, last_reconnect = await _handle_stall(
            telemetry, 0, telemetry.cum_received, stream, 0.0, 0.0
        )
        stream.reconnect.assert_not_called()
        assert stall_count == 1

    @pytest.mark.asyncio
    async def test_backoff_prevents_rapid_reconnect(self) -> None:
        """Reconnect is skipped when within cooldown interval."""
        telemetry = WsTelemetry()
        stream = MagicMock()
        stream.reconnect = AsyncMock()
        now = asyncio.get_event_loop().time()

        stall_count, _, last_reconnect = await _handle_stall(
            telemetry,
            STALL_THRESHOLD - 1,
            telemetry.cum_received,
            stream,
            now,  # Last reconnect = now → within cooldown
            60.0,
        )
        stream.reconnect.assert_not_called()
        assert stall_count == 0

    @pytest.mark.asyncio
    async def test_stall_resets_on_new_messages(self) -> None:
        """Stall count resets when new messages arrive."""
        telemetry = WsTelemetry()
        telemetry.record_parsed()  # Increment cum_received
        stream = MagicMock()
        stream.reconnect = AsyncMock()

        stall_count, new_received, _ = await _handle_stall(telemetry, 5, 0, stream, 0.0, 0.0)
        stream.reconnect.assert_not_called()
        assert stall_count == 0
        assert new_received == telemetry.cum_received

    @pytest.mark.asyncio
    async def test_handles_stream_without_reconnect(self) -> None:
        """Gracefully handles streams without reconnect method."""
        telemetry = WsTelemetry()
        stream = object()  # No reconnect method

        stall_count, _, _ = await _handle_stall(
            telemetry,
            STALL_THRESHOLD - 1,
            telemetry.cum_received,
            stream,
            0.0,
            0.0,
        )
        assert stall_count == 0  # Still resets


class TestWebSocketReconnect:
    """Test WebSocketPriceStream.reconnect() method."""

    @pytest.mark.asyncio
    async def test_reconnect_restarts_reader(self) -> None:
        """Reconnect cancels old reader and starts new one."""
        from arb_scanner.flippening.ws_client import WebSocketPriceStream

        stream = WebSocketPriceStream(ws_url="wss://test.example.com")
        # Simulate a running reader
        stream._reader_task = asyncio.create_task(asyncio.sleep(999))

        await stream.reconnect()

        assert stream._reader_task is not None
        assert not stream._reader_task.done()
        # Clean up
        stream._closed = True
        stream._reader_task.cancel()
        try:
            await stream._reader_task
        except (asyncio.CancelledError, Exception):
            pass
