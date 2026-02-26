"""Abstract base class for async venue clients."""

from __future__ import annotations

import abc
from types import TracebackType
from typing import Self

import httpx
import structlog

from arb_scanner.models.market import Market
from arb_scanner.utils.rate_limiter import RateLimiter

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="ingestion.base")


class BaseVenueClient(abc.ABC):
    """Abstract async venue client with rate limiting and httpx lifecycle.

    Subclasses must implement :meth:`fetch_markets` to return normalised
    :class:`Market` objects from the venue's API.
    """

    def __init__(
        self,
        *,
        base_url: str,
        rate_limit_per_sec: int,
        timeout: float = 30.0,
    ) -> None:
        """Initialise the client.

        Args:
            base_url: Root URL for the venue API.
            rate_limit_per_sec: Max requests per second for this client.
            timeout: HTTP request timeout in seconds.
        """
        self._base_url = base_url
        self._rate_limiter = RateLimiter(rate_limit_per_sec)
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Open the underlying ``httpx.AsyncClient``."""
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        )
        logger.info("client_opened", base_url=self._base_url)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the underlying ``httpx.AsyncClient``."""
        if self._client is not None:
            await self._client.aclose()
            logger.info("client_closed", base_url=self._base_url)
            self._client = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def client(self) -> httpx.AsyncClient:
        """Return the active ``httpx.AsyncClient``, raising if not open."""
        if self._client is None:
            raise RuntimeError("Client not opened; use 'async with' context manager")
        return self._client

    @property
    def rate_limiter(self) -> RateLimiter:
        """Return the rate limiter for this client."""
        return self._rate_limiter

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def fetch_markets(self) -> list[Market]:
        """Fetch all active markets from the venue.

        Returns:
            A list of normalised :class:`Market` objects.
        """
