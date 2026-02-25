"""Async rate limiter using a sliding-window token approach."""

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class RateLimiter:
    """Enforces a maximum number of requests per second via sliding window.

    Tracks timestamps of recent requests in a deque and sleeps when
    the window is full, preventing the initial-burst problem that
    semaphore-based limiters have.
    """

    def __init__(self, requests_per_second: int) -> None:
        """Initialize the rate limiter.

        Args:
            requests_per_second: Maximum allowed requests per second.
        """
        self._rate = requests_per_second
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """Acquire a rate-limited slot.

        Waits until the sliding 1-second window has capacity before
        recording the request timestamp and yielding.

        Yields:
            None once the slot is acquired.
        """
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            while self._timestamps and now - self._timestamps[0] >= 1.0:
                self._timestamps.popleft()
            if len(self._timestamps) >= self._rate:
                sleep_for = 1.0 - (now - self._timestamps[0])
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                self._timestamps.popleft()
            self._timestamps.append(loop.time())
        yield
