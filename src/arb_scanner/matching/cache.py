"""PostgreSQL-backed match cache for cross-venue contract matches.

Wraps the Repository layer to provide TTL-aware caching of MatchResult
objects, keyed by (poly_event_id, kalshi_event_id) composite key.
"""

import structlog

from arb_scanner.models.matching import MatchResult
from arb_scanner.storage.repository import Repository

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class MatchCache:
    """TTL-aware cache for MatchResult objects backed by PostgreSQL.

    Uses the Repository class for all database operations. Cache entries
    expire based on a configurable TTL; expired entries are treated as
    cache misses.
    """

    def __init__(self, repository: Repository, ttl_hours: int = 24) -> None:
        """Initialize the match cache.

        Args:
            repository: Repository instance for database operations.
            ttl_hours: Time-to-live in hours for cached entries.
        """
        self._repository = repository
        self._ttl_hours = ttl_hours

    @property
    def ttl_hours(self) -> int:
        """Return the configured TTL in hours.

        Returns:
            Cache TTL in hours.
        """
        return self._ttl_hours

    async def get(self, poly_event_id: str, kalshi_event_id: str) -> MatchResult | None:
        """Retrieve a cached match result if it exists and has not expired.

        Args:
            poly_event_id: Polymarket event identifier.
            kalshi_event_id: Kalshi event identifier.

        Returns:
            The cached MatchResult, or None if missing or expired.
        """
        result = await self._repository.get_cached_match(poly_event_id, kalshi_event_id)
        if result is not None:
            logger.debug(
                "cache.hit",
                poly_event_id=poly_event_id,
                kalshi_event_id=kalshi_event_id,
            )
        return result

    async def set(self, match_result: MatchResult) -> None:
        """Insert or update a match result in the cache.

        Delegates to Repository.upsert_match_result which handles
        the ON CONFLICT upsert semantics.

        Args:
            match_result: The MatchResult to cache.
        """
        await self._repository.upsert_match_result(match_result)
        logger.debug(
            "cache.set",
            poly_event_id=match_result.poly_event_id,
            kalshi_event_id=match_result.kalshi_event_id,
            ttl_expires=match_result.ttl_expires.isoformat(),
        )
