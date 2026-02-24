"""Async rate limiter using semaphore and scheduled release."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class RateLimiter:
    """Enforces a maximum number of requests per second using asyncio primitives.

    Uses a semaphore to limit concurrent access and schedules release
    after ``1 / requests_per_second`` seconds to enforce the rate.
    """

    def __init__(self, requests_per_second: int) -> None:
        """Initialize the rate limiter.

        Args:
            requests_per_second: Maximum allowed requests per second.
        """
        self._rate = requests_per_second
        self._semaphore = asyncio.Semaphore(requests_per_second)
        self._interval = 1.0 / requests_per_second

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """Acquire a rate-limited slot.

        Async context manager that acquires the semaphore and schedules
        its release after the rate interval.

        Yields:
            None once the slot is acquired.
        """
        await self._semaphore.acquire()
        try:
            yield
        finally:
            loop = asyncio.get_running_loop()
            loop.call_later(self._interval, self._semaphore.release)
