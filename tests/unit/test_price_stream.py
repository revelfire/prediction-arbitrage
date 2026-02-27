"""Unit tests for the SSE price stream helpers and endpoint."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest

from arb_scanner.api.routes_price_stream import (
    _build_snapshot,
    _detect_changes,
    _format_sse,
    _tick_to_dict,
)
from arb_scanner.flippening.price_ring_buffer import (
    PriceRingBuffer,
    PriceTick,
)

_NOW = datetime.now(tz=UTC)


def _make_tick(market_id: str = "m1") -> PriceTick:
    """Create a test PriceTick."""
    return PriceTick(
        market_id=market_id,
        market_title="Test Market",
        category="nba",
        category_type="sport",
        yes_mid=Decimal("0.65"),
        baseline_yes=Decimal("0.60"),
        deviation_pct=8.33,
        spread=Decimal("0.02"),
        timestamp=_NOW,
        book_depth_bids=5,
        book_depth_asks=3,
    )


def test_tick_to_dict() -> None:
    """Serialises a PriceTick to a JSON-safe dict."""
    tick = _make_tick()
    result = _tick_to_dict(tick)
    assert result["market_id"] == "m1"
    assert result["yes_mid"] == "0.65"
    assert result["baseline_yes"] == "0.60"
    assert result["deviation_pct"] == 8.33
    assert result["spread"] == "0.02"
    assert result["book_depth_bids"] == 5


def test_tick_to_dict_none_baseline() -> None:
    """Handles None baseline_yes correctly."""
    tick = PriceTick(
        market_id="m1",
        market_title="No Baseline",
        category="crypto",
        category_type="crypto",
        yes_mid=Decimal("0.50"),
        baseline_yes=None,
        deviation_pct=0.0,
        spread=Decimal("0.01"),
        timestamp=_NOW,
        book_depth_bids=0,
        book_depth_asks=0,
    )
    result = _tick_to_dict(tick)
    assert result["baseline_yes"] is None


def test_build_snapshot() -> None:
    """Builds a JSON snapshot from multiple ticks."""
    latest = {"m1": _make_tick("m1"), "m2": _make_tick("m2")}
    snapshot_str = _build_snapshot(latest)
    parsed = json.loads(snapshot_str)
    assert "markets" in parsed
    assert "ts" in parsed
    assert len(parsed["markets"]) == 2
    ids = {m["market_id"] for m in parsed["markets"]}
    assert ids == {"m1", "m2"}


def test_format_sse() -> None:
    """Formats an SSE event string correctly."""
    result = _format_sse("snapshot", '{"test": true}')
    assert result == 'event: snapshot\ndata: {"test": true}\n\n'


def test_format_sse_status() -> None:
    """Formats a status SSE event."""
    result = _format_sse("status", '{"status":"idle"}')
    assert "event: status" in result
    assert "data: " in result
    assert result.endswith("\n\n")


def test_detect_changes_new_market() -> None:
    """Detects change when a new market appears."""
    latest = {"m1": _make_tick("m1")}
    assert _detect_changes(latest, {}) is True


def test_detect_changes_no_change() -> None:
    """Returns False when timestamps are unchanged."""
    tick = _make_tick("m1")
    latest = {"m1": tick}
    prev_ts = {"m1": tick.timestamp.isoformat()}
    assert _detect_changes(latest, prev_ts) is False


def test_detect_changes_updated_timestamp() -> None:
    """Detects change when a market timestamp changes."""
    tick = _make_tick("m1")
    latest = {"m1": tick}
    prev_ts = {"m1": "2020-01-01T00:00:00"}
    assert _detect_changes(latest, prev_ts) is True


def test_detect_changes_market_count_differs() -> None:
    """Detects change when market count changes."""
    latest = {"m1": _make_tick("m1"), "m2": _make_tick("m2")}
    prev_ts = {"m1": _NOW.isoformat()}
    assert _detect_changes(latest, prev_ts) is True


@pytest.mark.asyncio()
async def test_stream_yields_idle_when_no_buffer() -> None:
    """The SSE generator yields idle when buffer is None."""
    from arb_scanner.api.routes_price_stream import _stream_prices

    with patch(
        "arb_scanner.api.routes_price_stream.get_shared_buffer",
        return_value=None,
    ):
        gen = _stream_prices()
        first = await gen.__anext__()
        assert "event: status" in first
        assert '"idle"' in first
        await gen.aclose()


@pytest.mark.asyncio()
async def test_stream_yields_snapshot_with_data() -> None:
    """The SSE generator yields a snapshot when buffer has data."""
    from arb_scanner.api.routes_price_stream import _stream_prices

    buf = PriceRingBuffer()
    buf.push(_make_tick("m1"))

    with patch(
        "arb_scanner.api.routes_price_stream.get_shared_buffer",
        return_value=buf,
    ):
        gen = _stream_prices()
        first = await gen.__anext__()
        assert "event: snapshot" in first
        assert "m1" in first
        await gen.aclose()


@pytest.mark.asyncio()
async def test_stream_yields_heartbeat_when_no_changes() -> None:
    """The SSE generator yields a heartbeat after interval with no changes."""
    from arb_scanner.api.routes_price_stream import _stream_prices

    buf = PriceRingBuffer()
    buf.push(_make_tick("m1"))

    with (
        patch(
            "arb_scanner.api.routes_price_stream.get_shared_buffer",
            return_value=buf,
        ),
        patch(
            "arb_scanner.api.routes_price_stream._HEARTBEAT_INTERVAL_SECONDS",
            0.0,
        ),
    ):
        gen = _stream_prices()
        first = await gen.__anext__()
        assert "event: snapshot" in first
        second = await gen.__anext__()
        assert "event: heartbeat" in second
        assert '"ts"' in second
        await gen.aclose()


@pytest.mark.asyncio()
async def test_stream_yields_idle_with_empty_buffer() -> None:
    """The SSE generator yields idle when buffer exists but is empty."""
    from arb_scanner.api.routes_price_stream import _stream_prices

    buf = PriceRingBuffer()

    with patch(
        "arb_scanner.api.routes_price_stream.get_shared_buffer",
        return_value=buf,
    ):
        gen = _stream_prices()
        first = await gen.__anext__()
        assert "event: status" in first
        assert '"idle"' in first
        await gen.aclose()
