"""In-memory event feed for real-time auto-execution pipeline activity."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from typing import Any

_MAX_HISTORY = 200
_MAX_QUEUE = 100

_history: deque[dict[str, Any]] = deque(maxlen=_MAX_HISTORY)
_subscribers: list[asyncio.Queue[dict[str, Any] | None]] = []


def push_activity(
    event_type: str,
    arb_id: str,
    *,
    pipeline: str = "unknown",
    **fields: Any,
) -> None:
    """Push a pipeline activity event to the feed (sync, non-blocking).

    Args:
        event_type: Pipeline stage identifier.
        arb_id: Opportunity/ticket ID.
        pipeline: Pipeline label ("arb" or "flip").
        **fields: Extra event fields (title, reasons, size, etc.).
    """
    event: dict[str, Any] = {
        "type": event_type,
        "arb_id": arb_id,
        "pipeline": pipeline,
        "ts": datetime.now(tz=UTC).isoformat(),
        **fields,
    }
    _history.append(event)
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # slow consumer — drop


def get_history(limit: int = 60) -> list[dict[str, Any]]:
    """Return the most recent activity events, oldest first.

    Args:
        limit: Max events to return.

    Returns:
        List of event dicts.
    """
    items = list(_history)
    return items[-limit:]


def subscribe() -> asyncio.Queue[dict[str, Any] | None]:
    """Register a new SSE subscriber queue.

    Returns:
        Asyncio queue that receives new events as they arrive.
    """
    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=_MAX_QUEUE)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue[dict[str, Any] | None]) -> None:
    """Deregister a subscriber queue when the SSE connection closes.

    Args:
        q: Queue previously returned by subscribe().
    """
    try:
        _subscribers.remove(q)
    except ValueError:
        pass
