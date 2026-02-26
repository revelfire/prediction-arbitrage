"""Thread-safe in-memory ring buffer for live price ticks."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class PriceTick:
    """A single price observation for a market."""

    market_id: str
    market_title: str
    category: str
    category_type: str
    yes_mid: Decimal
    baseline_yes: Decimal | None
    deviation_pct: float
    spread: Decimal
    timestamp: datetime
    book_depth_bids: int
    book_depth_asks: int


class PriceRingBuffer:
    """Per-market ring buffer storing recent price ticks.

    Thread-safe via a reentrant lock. Each market keeps at most
    ``max_per_market`` ticks in a FIFO deque.
    """

    def __init__(self, max_per_market: int = 60) -> None:
        """Initialise the buffer.

        Args:
            max_per_market: Maximum ticks retained per market.
        """
        self._max: int = max_per_market
        self._data: dict[str, deque[PriceTick]] = {}
        self._lock: threading.Lock = threading.Lock()

    def push(self, tick: PriceTick) -> None:
        """Add a tick to the buffer.

        Args:
            tick: The price tick to store.
        """
        with self._lock:
            if tick.market_id not in self._data:
                self._data[tick.market_id] = deque(maxlen=self._max)
            self._data[tick.market_id].append(tick)

    def get_latest(self) -> dict[str, PriceTick]:
        """Return the most recent tick per market.

        Returns:
            Dict mapping market_id to its latest PriceTick.
        """
        with self._lock:
            return {mid: dq[-1] for mid, dq in self._data.items() if dq}

    def get_history(self, market_id: str) -> list[PriceTick]:
        """Return all ticks for a market, oldest first.

        Args:
            market_id: The market identifier.

        Returns:
            List of PriceTick in chronological order.
        """
        with self._lock:
            dq = self._data.get(market_id)
            return list(dq) if dq else []

    def market_count(self) -> int:
        """Return the number of tracked markets.

        Returns:
            Count of distinct market_ids in the buffer.
        """
        with self._lock:
            return len(self._data)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_shared_buffer: PriceRingBuffer | None = None


def get_shared_buffer() -> PriceRingBuffer | None:
    """Return the shared ring buffer instance (may be None).

    Returns:
        The shared PriceRingBuffer, or None if not yet initialised.
    """
    return _shared_buffer


def set_shared_buffer(buf: PriceRingBuffer) -> None:
    """Set the shared ring buffer instance.

    Args:
        buf: The PriceRingBuffer to share across modules.
    """
    global _shared_buffer  # noqa: PLW0603
    _shared_buffer = buf
