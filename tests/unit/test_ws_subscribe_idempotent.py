"""Tests for WebSocket subscribe idempotency."""

from __future__ import annotations

import asyncio

import pytest

from arb_scanner.flippening.ws_client import PollingPriceStream, WebSocketPriceStream


class TestWsSubscribeIdempotent:
    """Verify no duplicate reader tasks on re-subscribe."""

    @pytest.mark.asyncio
    async def test_ws_subscribe_no_duplicate_reader(self) -> None:
        """Second subscribe() does not spawn a second reader task."""
        stream = WebSocketPriceStream(ws_url="wss://test.example.com")
        # Simulate a running reader
        stream._reader_task = asyncio.create_task(asyncio.sleep(999))

        first_task = stream._reader_task
        await stream.subscribe(["new_token"])

        assert stream._reader_task is first_task
        assert "new_token" in stream._subscribed_tokens

        stream._closed = True
        first_task.cancel()
        try:
            await first_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_ws_subscribe_merges_tokens(self) -> None:
        """Subscribe merges new tokens with existing ones."""
        stream = WebSocketPriceStream(ws_url="wss://test.example.com")
        stream._reader_task = asyncio.create_task(asyncio.sleep(999))
        stream._subscribed_tokens = ["tok_a", "tok_b"]

        await stream.subscribe(["tok_b", "tok_c"])

        assert set(stream._subscribed_tokens) == {"tok_a", "tok_b", "tok_c"}

        stream._closed = True
        stream._reader_task.cancel()
        try:
            await stream._reader_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_ws_subscribe_starts_reader_if_none(self) -> None:
        """First subscribe starts a reader task."""
        stream = WebSocketPriceStream(ws_url="wss://test.example.com")
        assert stream._reader_task is None

        await stream.subscribe(["tok_1"])

        assert stream._reader_task is not None
        assert not stream._reader_task.done()

        stream._closed = True
        stream._reader_task.cancel()
        try:
            await stream._reader_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_polling_subscribe_no_duplicate_task(self) -> None:
        """Second polling subscribe() does not spawn a second poll task."""
        stream = PollingPriceStream("https://example.com", interval_seconds=60)
        stream._polling_task = asyncio.create_task(asyncio.sleep(999))
        stream._subscribed_tokens = ["tok_a"]
        first_task = stream._polling_task

        await stream.subscribe(["tok_b"])

        assert stream._polling_task is first_task
        assert set(stream._subscribed_tokens) == {"tok_a", "tok_b"}

        stream._closed = True
        first_task.cancel()
        try:
            await first_task
        except asyncio.CancelledError:
            pass
