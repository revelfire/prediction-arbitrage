"""Tests for the TickBuffer non-blocking batch writer."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from arb_scanner.flippening.tick_buffer import TickBuffer
from arb_scanner.models.config import FlippeningConfig
from arb_scanner.models.flippening import PriceUpdate


def _make_update(market_id: str = "m1", token_id: str = "t1") -> PriceUpdate:
    return PriceUpdate(
        market_id=market_id,
        token_id=token_id,
        yes_bid=Decimal("0.55"),
        yes_ask=Decimal("0.57"),
        no_bid=Decimal("0.43"),
        no_ask=Decimal("0.45"),
        timestamp=datetime.now(tz=UTC),
    )


def _make_config(**overrides: object) -> FlippeningConfig:
    defaults = {
        "capture_ticks": True,
        "tick_buffer_size": 5,
        "tick_flush_interval_seconds": 1.0,
    }
    defaults.update(overrides)
    return FlippeningConfig(**defaults)


def _make_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.insert_ticks_batch = AsyncMock()
    return repo


class TestTickBufferAppend:
    """Tests for TickBuffer.append()."""

    def test_append_increments_pending(self) -> None:
        buf = TickBuffer(_make_repo(), _make_config())
        assert buf.pending == 0
        buf.append(_make_update())
        assert buf.pending == 1

    def test_append_returns_false_below_capacity(self) -> None:
        buf = TickBuffer(_make_repo(), _make_config(tick_buffer_size=10))
        assert buf.append(_make_update()) is False

    def test_append_returns_true_at_capacity(self) -> None:
        buf = TickBuffer(_make_repo(), _make_config(tick_buffer_size=3))
        buf.append(_make_update())
        buf.append(_make_update())
        result = buf.append(_make_update())
        assert result is True

    def test_disabled_buffer_noop(self) -> None:
        buf = TickBuffer(_make_repo(), _make_config(capture_ticks=False))
        result = buf.append(_make_update())
        assert result is False
        assert buf.pending == 0

    def test_none_repo_noop(self) -> None:
        buf = TickBuffer(None, _make_config())
        result = buf.append(_make_update())
        assert result is False
        assert buf.pending == 0


class TestTickBufferFlush:
    """Tests for TickBuffer.flush()."""

    @pytest.mark.asyncio
    async def test_flush_sends_batch_to_repo(self) -> None:
        repo = _make_repo()
        buf = TickBuffer(repo, _make_config())
        buf.append(_make_update())
        buf.append(_make_update())

        count = await buf.flush()

        assert count == 2
        assert buf.pending == 0
        repo.insert_ticks_batch.assert_awaited_once()
        batch = repo.insert_ticks_batch.call_args[0][0]
        assert len(batch) == 2

    @pytest.mark.asyncio
    async def test_flush_empty_buffer_returns_zero(self) -> None:
        buf = TickBuffer(_make_repo(), _make_config())
        assert await buf.flush() == 0

    @pytest.mark.asyncio
    async def test_flush_clears_buffer(self) -> None:
        buf = TickBuffer(_make_repo(), _make_config())
        buf.append(_make_update())
        await buf.flush()
        assert buf.pending == 0

    @pytest.mark.asyncio
    async def test_flush_failure_drops_buffer(self) -> None:
        repo = _make_repo()
        repo.insert_ticks_batch.side_effect = RuntimeError("DB gone")
        buf = TickBuffer(repo, _make_config())
        buf.append(_make_update())
        buf.append(_make_update())

        count = await buf.flush()

        assert count == 0
        assert buf.pending == 0  # buffer dropped, not retained

    @pytest.mark.asyncio
    async def test_flush_with_none_repo_returns_zero(self) -> None:
        buf = TickBuffer(None, _make_config())
        # Force-enable for this edge case
        buf._enabled = True
        buf._buffer = [("x",)]
        assert await buf.flush() == 0

    @pytest.mark.asyncio
    async def test_row_tuple_has_correct_fields(self) -> None:
        repo = _make_repo()
        buf = TickBuffer(repo, _make_config())
        update = _make_update(market_id="mkt_abc", token_id="tok_123")
        buf.append(update)

        await buf.flush()

        batch = repo.insert_ticks_batch.call_args[0][0]
        row = batch[0]
        assert row[0] == "mkt_abc"
        assert row[1] == "tok_123"
        assert row[2] == Decimal("0.55")  # yes_bid
        assert row[3] == Decimal("0.57")  # yes_ask
        assert row[7] is False  # synthetic_spread
