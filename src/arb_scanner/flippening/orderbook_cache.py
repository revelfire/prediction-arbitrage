"""Order book cache for enriching synthetic WS spreads with real bid/ask."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
import structlog

from arb_scanner.models.flippening import PriceUpdate
from arb_scanner.utils.rate_limiter import RateLimiter

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.orderbook_cache",
)


@dataclass
class CacheEntry:
    """Cached order book snapshot for a single token."""

    yes_bid: Decimal
    yes_ask: Decimal
    no_bid: Decimal
    no_ask: Decimal
    depth_bids: int
    depth_asks: int
    fetched_at: datetime


class OrderBookCache:
    """LRU cache for CLOB order book data with async background refresh."""

    def __init__(
        self,
        max_size: int = 200,
        ttl_seconds: float = 10.0,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        """Initialise cache.

        Args:
            max_size: Maximum cached entries before LRU eviction.
            ttl_seconds: Seconds before a cache entry is stale.
            rate_limiter: Optional rate limiter for API calls.
        """
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._limiter = rate_limiter or RateLimiter(5)
        self._cache: dict[str, CacheEntry] = {}
        self._pending: set[str] = set()
        self._access_order: list[str] = []
        self.hits: int = 0
        self.misses: int = 0

    async def enrich(
        self,
        update: PriceUpdate,
        client: httpx.AsyncClient,
    ) -> PriceUpdate:
        """Enrich a PriceUpdate with real order book data if cached.

        Args:
            update: Incoming price update (possibly synthetic).
            client: HTTP client for book fetches.

        Returns:
            Enriched PriceUpdate (or original if cache miss).
        """
        entry = self._cache.get(update.token_id)
        now = datetime.now(tz=UTC)

        if entry is not None:
            age = (now - entry.fetched_at).total_seconds()
            if age <= self._ttl:
                self.hits += 1
                self._touch(update.token_id)
                return self._apply_entry(update, entry)
            # Stale: use data but schedule refresh
            self.hits += 1
            self._touch(update.token_id)
            self._schedule_fetch(update.token_id, client)
            return self._apply_entry(update, entry)

        self.misses += 1
        self._schedule_fetch(update.token_id, client)
        return update

    @property
    def cache_hit_rate(self) -> float:
        """Ratio of hits to total lookups."""
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total

    def _apply_entry(self, update: PriceUpdate, entry: CacheEntry) -> PriceUpdate:
        """Apply cached book data to a PriceUpdate.

        Args:
            update: Original price update.
            entry: Cached order book entry.

        Returns:
            New PriceUpdate with real spread data.
        """
        return update.model_copy(
            update={
                "yes_bid": entry.yes_bid,
                "yes_ask": entry.yes_ask,
                "no_bid": entry.no_bid,
                "no_ask": entry.no_ask,
                "synthetic_spread": False,
                "book_depth_bids": entry.depth_bids,
                "book_depth_asks": entry.depth_asks,
            },
        )

    def _touch(self, token_id: str) -> None:
        """Move token_id to end of access order (most recent).

        Args:
            token_id: Token to mark as recently accessed.
        """
        if token_id in self._access_order:
            self._access_order.remove(token_id)
        self._access_order.append(token_id)

    def _schedule_fetch(self, token_id: str, client: httpx.AsyncClient) -> None:
        """Schedule a background book fetch if not already pending.

        Args:
            token_id: Token to fetch.
            client: HTTP client.
        """
        if token_id not in self._pending:
            self._pending.add(token_id)
            asyncio.create_task(self._fetch_book(token_id, client))

    async def _fetch_book(
        self,
        token_id: str,
        client: httpx.AsyncClient,
    ) -> None:
        """Fetch order book from CLOB API and update cache.

        Args:
            token_id: CLOB token identifier.
            client: HTTP client.
        """
        try:
            async with self._limiter.acquire():
                resp = await client.get(
                    "/book",
                    params={"token_id": token_id},
                )
                resp.raise_for_status()
                data = resp.json()
            entry = self._parse_book(data)
            if entry is not None:
                self._update_cache(token_id, entry)
        except Exception:
            logger.warning("orderbook_fetch_failed", token_id=token_id)
        finally:
            self._pending.discard(token_id)

    def _parse_book(self, data: dict[str, object]) -> CacheEntry | None:
        """Parse CLOB /book response into a CacheEntry.

        Args:
            data: Raw JSON response.

        Returns:
            CacheEntry or None on failure.
        """
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if not isinstance(bids, list) or not isinstance(asks, list):
            return None

        yes_bid = Decimal("0")
        yes_ask = Decimal("1")
        if bids:
            top_bid = bids[-1]
            if isinstance(top_bid, dict):
                yes_bid = self._safe_dec(top_bid.get("price")) or Decimal("0")
        if asks:
            top_ask = asks[0]
            if isinstance(top_ask, dict):
                yes_ask = self._safe_dec(top_ask.get("price")) or Decimal("1")

        return CacheEntry(
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=max(Decimal("1") - yes_ask, Decimal("0")),
            no_ask=min(Decimal("1") - yes_bid, Decimal("1")),
            depth_bids=len(bids),
            depth_asks=len(asks),
            fetched_at=datetime.now(tz=UTC),
        )

    def _update_cache(self, token_id: str, entry: CacheEntry) -> None:
        """Insert or update a cache entry with LRU eviction.

        Args:
            token_id: Token identifier.
            entry: New cache entry.
        """
        self._cache[token_id] = entry
        self._touch(token_id)
        while len(self._cache) > self._max_size and self._access_order:
            evict_key = self._access_order.pop(0)
            self._cache.pop(evict_key, None)

    @staticmethod
    def _safe_dec(value: object) -> Decimal | None:
        """Safely convert to Decimal.

        Args:
            value: Raw value.

        Returns:
            Decimal or None.
        """
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
