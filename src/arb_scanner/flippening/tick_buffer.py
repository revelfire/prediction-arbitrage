"""Non-blocking tick buffer for batched persistence of PriceUpdate data."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from arb_scanner.models.config import FlippeningConfig
    from arb_scanner.models.flippening import PriceUpdate
    from arb_scanner.storage.tick_repository import TickRepository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.tick_buffer",
)


class TickBuffer:
    """Buffer PriceUpdate objects and flush them to the DB in batches.

    Designed to be non-blocking: ``append()`` is synchronous and O(1).
    ``flush()`` is async but swallows all exceptions to avoid disrupting
    the live engine.
    """

    def __init__(
        self,
        repo: TickRepository | None,
        config: FlippeningConfig,
    ) -> None:
        """Initialise the tick buffer.

        Args:
            repo: TickRepository instance (None in dry_run mode).
            config: Flippening config with buffer settings.
        """
        self._repo = repo
        self._buffer: list[tuple[Any, ...]] = []
        self._max_size = config.tick_buffer_size
        self._enabled = config.capture_ticks and repo is not None

    @property
    def pending(self) -> int:
        """Number of ticks awaiting flush."""
        return len(self._buffer)

    def append(self, update: PriceUpdate) -> bool:
        """Add a tick to the buffer.

        Non-blocking. Returns True if buffer is full and needs flushing.

        Args:
            update: Price update to buffer.

        Returns:
            True if buffer reached capacity and should be flushed.
        """
        if not self._enabled:
            return False
        self._buffer.append(_to_row(update))
        return len(self._buffer) >= self._max_size

    async def flush(self) -> int:
        """Flush buffered ticks to the database.

        Swallows all exceptions — failures MUST NOT disrupt the live
        engine (EC-005).

        Returns:
            Number of ticks flushed (0 if nothing to flush or on error).
        """
        if not self._buffer or not self._repo:
            return 0
        batch = self._buffer
        self._buffer = []
        try:
            await self._repo.insert_ticks_batch(batch)
            return len(batch)
        except Exception:
            logger.warning("tick_flush_failed", dropped=len(batch))
            return 0


def _to_row(update: PriceUpdate) -> tuple[Any, ...]:
    """Convert a PriceUpdate to a row tuple for batch insert.

    Args:
        update: Price update model.

    Returns:
        Tuple matching INSERT_TICK column order.
    """
    return (
        update.market_id,
        update.token_id,
        update.yes_bid,
        update.yes_ask,
        update.no_bid,
        update.no_ask,
        update.timestamp,
        update.synthetic_spread,
        update.book_depth_bids,
        update.book_depth_asks,
    )
