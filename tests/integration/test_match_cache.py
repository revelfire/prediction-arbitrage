"""T033 - Integration tests for the PostgreSQL-backed match cache.

Tests cache hit, cache miss, and expired TTL behaviour via the
MatchCache class wrapping the Repository layer. Requires a live
PostgreSQL database (guarded by @requires_postgres).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from arb_scanner.matching.cache import MatchCache
from arb_scanner.models.matching import MatchResult
from arb_scanner.storage.repository import Repository

from .conftest import requires_postgres

if TYPE_CHECKING:
    import asyncpg

_NOW = datetime.now(tz=timezone.utc)
_FUTURE = _NOW + timedelta(hours=48)
_PAST = _NOW - timedelta(hours=48)


def _make_match(
    poly_id: str,
    kalshi_id: str,
    *,
    ttl_expires: datetime | None = None,
) -> MatchResult:
    """Build a test MatchResult with configurable TTL."""
    return MatchResult(
        poly_event_id=poly_id,
        kalshi_event_id=kalshi_id,
        match_confidence=0.92,
        resolution_equivalent=True,
        resolution_risks=["minor wording difference"],
        safe_to_arb=True,
        reasoning="Same underlying event.",
        matched_at=_NOW,
        ttl_expires=ttl_expires or _FUTURE,
    )


# ---------------------------------------------------------------------------
# Cache hit
# ---------------------------------------------------------------------------


@requires_postgres
class TestCacheHit:
    """Tests for cache hit behaviour."""

    @pytest.mark.asyncio()
    async def test_cache_hit_returns_match_result(
        self,
        db_pool: asyncpg.Pool[asyncpg.Record],
    ) -> None:
        """Verify a cached, non-expired entry is returned."""
        repo = Repository(db_pool)
        cache = MatchCache(repo, ttl_hours=24)

        match = _make_match("poly-ch-1", "kalshi-ch-1", ttl_expires=_FUTURE)
        await cache.set(match)

        result = await cache.get("poly-ch-1", "kalshi-ch-1")
        assert result is not None
        assert isinstance(result, MatchResult)
        assert result.poly_event_id == "poly-ch-1"
        assert result.safe_to_arb is True


# ---------------------------------------------------------------------------
# Cache miss
# ---------------------------------------------------------------------------


@requires_postgres
class TestCacheMiss:
    """Tests for cache miss behaviour."""

    @pytest.mark.asyncio()
    async def test_cache_miss_returns_none(
        self,
        db_pool: asyncpg.Pool[asyncpg.Record],
    ) -> None:
        """Verify a non-existent key returns None."""
        repo = Repository(db_pool)
        cache = MatchCache(repo, ttl_hours=24)

        result = await cache.get("nonexistent-poly", "nonexistent-kalshi")
        assert result is None


# ---------------------------------------------------------------------------
# Expired TTL
# ---------------------------------------------------------------------------


@requires_postgres
class TestExpiredTTL:
    """Tests for expired cache entry behaviour."""

    @pytest.mark.asyncio()
    async def test_expired_ttl_returns_none(
        self,
        db_pool: asyncpg.Pool[asyncpg.Record],
    ) -> None:
        """Verify a cached entry with past ttl_expires returns None."""
        repo = Repository(db_pool)
        cache = MatchCache(repo, ttl_hours=24)

        match = _make_match("poly-exp-1", "kalshi-exp-1", ttl_expires=_PAST)
        await cache.set(match)

        result = await cache.get("poly-exp-1", "kalshi-exp-1")
        assert result is None
