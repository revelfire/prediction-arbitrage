"""SSE endpoint for streaming live price updates to the dashboard."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from arb_scanner.flippening.price_ring_buffer import (
    PriceTick,
    get_shared_buffer,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="api.price_stream",
)
router = APIRouter()

_POLL_INTERVAL_SECONDS = 1.0
_HEARTBEAT_INTERVAL_SECONDS = 15.0


def _tick_to_dict(tick: PriceTick) -> dict[str, object]:
    """Serialise a PriceTick to a JSON-friendly dict.

    Args:
        tick: The price tick to convert.

    Returns:
        Dict with string-safe values.
    """
    return {
        "market_id": tick.market_id,
        "market_title": tick.market_title,
        "category": tick.category,
        "category_type": tick.category_type,
        "yes_mid": str(tick.yes_mid),
        "baseline_yes": str(tick.baseline_yes) if tick.baseline_yes is not None else None,
        "deviation_pct": tick.deviation_pct,
        "spread": str(tick.spread),
        "timestamp": tick.timestamp.isoformat(),
        "book_depth_bids": tick.book_depth_bids,
        "book_depth_asks": tick.book_depth_asks,
    }


def _build_snapshot(
    latest: dict[str, PriceTick],
) -> str:
    """Build a JSON snapshot string from the latest ticks.

    Args:
        latest: Dict of market_id to latest PriceTick.

    Returns:
        JSON-encoded string of the snapshot payload.
    """
    markets = [_tick_to_dict(t) for t in latest.values()]
    return json.dumps({"markets": markets, "ts": _now_iso()})


def _now_iso() -> str:
    """Return the current UTC time as ISO 8601.

    Returns:
        ISO 8601 timestamp string.
    """
    return datetime.now(tz=UTC).isoformat()


def _format_sse(event: str, data: str) -> str:
    """Format an SSE message.

    Args:
        event: The SSE event name.
        data: The data payload string.

    Returns:
        Formatted SSE message string.
    """
    return f"event: {event}\ndata: {data}\n\n"


async def _stream_prices() -> AsyncGenerator[str, None]:
    """Generate SSE events from the shared price ring buffer.

    Sends heartbeat events when no data changes to keep the
    connection alive and let the client know the stream is healthy.

    Yields:
        Formatted SSE message strings.
    """
    prev_ts: dict[str, str] = {}
    last_send = 0.0

    while True:
        now = asyncio.get_event_loop().time()
        buf = get_shared_buffer()
        if buf is None or buf.market_count() == 0:
            yield _format_sse("status", json.dumps({"status": "idle"}))
            last_send = now
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            continue

        latest = buf.get_latest()
        changed = _detect_changes(latest, prev_ts)

        if changed:
            yield _format_sse("snapshot", _build_snapshot(latest))
            prev_ts = {mid: t.timestamp.isoformat() for mid, t in latest.items()}
            last_send = now
        elif now - last_send >= _HEARTBEAT_INTERVAL_SECONDS:
            yield _format_sse(
                "heartbeat",
                json.dumps({"ts": _now_iso()}),
            )
            last_send = now

        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def _detect_changes(
    latest: dict[str, PriceTick],
    prev_ts: dict[str, str],
) -> bool:
    """Check whether any market has a newer timestamp than previously sent.

    Args:
        latest: Current latest ticks per market.
        prev_ts: Previously sent timestamp ISO strings per market.

    Returns:
        True if any market has new data.
    """
    if len(latest) != len(prev_ts):
        return True
    for mid, tick in latest.items():
        old = prev_ts.get(mid)
        if old is None or tick.timestamp.isoformat() != old:
            return True
    return False


@router.get("/api/flippenings/price-stream")
async def price_stream() -> StreamingResponse:
    """SSE endpoint streaming live price updates.

    Returns:
        StreamingResponse with text/event-stream media type.
    """
    return StreamingResponse(
        _stream_prices(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
