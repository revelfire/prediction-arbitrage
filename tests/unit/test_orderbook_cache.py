"""Tests for the order book cache."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.flippening.orderbook_cache import CacheEntry, OrderBookCache
from arb_scanner.models.flippening import PriceUpdate


def _make_update(token_id: str = "tok-1", price: str = "0.60") -> PriceUpdate:
    p = Decimal(price)
    return PriceUpdate(
        market_id="m1",
        token_id=token_id,
        yes_bid=max(p - Decimal("0.01"), Decimal("0")),
        yes_ask=min(p + Decimal("0.01"), Decimal("1")),
        no_bid=max(Decimal("1") - p - Decimal("0.01"), Decimal("0")),
        no_ask=min(Decimal("1") - p + Decimal("0.01"), Decimal("1")),
        timestamp=datetime.now(tz=UTC),
        synthetic_spread=True,
    )


def _make_entry(
    yes_bid: str = "0.58",
    yes_ask: str = "0.62",
    stale: bool = False,
) -> CacheEntry:
    fetched = datetime.now(tz=UTC)
    if stale:
        fetched = fetched - timedelta(seconds=30)
    return CacheEntry(
        yes_bid=Decimal(yes_bid),
        yes_ask=Decimal(yes_ask),
        no_bid=max(Decimal("1") - Decimal(yes_ask), Decimal("0")),
        no_ask=min(Decimal("1") - Decimal(yes_bid), Decimal("1")),
        depth_bids=5,
        depth_asks=3,
        fetched_at=fetched,
    )


class TestOrderBookCache:
    """Tests for OrderBookCache."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_real_spread(self) -> None:
        cache = OrderBookCache(ttl_seconds=60.0)
        cache._cache["tok-1"] = _make_entry()
        cache._access_order.append("tok-1")
        update = _make_update()
        client = AsyncMock()

        result = await cache.enrich(update, client)
        assert result.synthetic_spread is False
        assert result.yes_bid == Decimal("0.58")
        assert result.yes_ask == Decimal("0.62")
        assert result.book_depth_bids == 5
        assert cache.hits == 1

    @pytest.mark.asyncio
    async def test_cache_miss_keeps_synthetic(self) -> None:
        cache = OrderBookCache(ttl_seconds=60.0)
        update = _make_update()
        client = AsyncMock()

        with patch.object(cache, "_schedule_fetch"):
            result = await cache.enrich(update, client)
        assert result.synthetic_spread is True
        assert cache.misses == 1

    @pytest.mark.asyncio
    async def test_stale_entry_still_used(self) -> None:
        cache = OrderBookCache(ttl_seconds=5.0)
        cache._cache["tok-1"] = _make_entry(stale=True)
        cache._access_order.append("tok-1")
        update = _make_update()
        client = AsyncMock()

        with patch.object(cache, "_schedule_fetch"):
            result = await cache.enrich(update, client)
        assert result.synthetic_spread is False
        assert cache.hits == 1

    @pytest.mark.asyncio
    async def test_lru_eviction(self) -> None:
        cache = OrderBookCache(max_size=2, ttl_seconds=60.0)
        cache._update_cache("tok-1", _make_entry())
        cache._update_cache("tok-2", _make_entry())
        cache._update_cache("tok-3", _make_entry())
        assert "tok-1" not in cache._cache
        assert "tok-2" in cache._cache
        assert "tok-3" in cache._cache

    @pytest.mark.asyncio
    async def test_cache_hit_rate(self) -> None:
        cache = OrderBookCache()
        cache.hits = 3
        cache.misses = 1
        assert cache.cache_hit_rate == 0.75

    @pytest.mark.asyncio
    async def test_cache_hit_rate_zero(self) -> None:
        cache = OrderBookCache()
        assert cache.cache_hit_rate == 0.0

    @pytest.mark.asyncio
    async def test_fetch_book_updates_cache(self) -> None:
        cache = OrderBookCache(ttl_seconds=60.0)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "bids": [{"price": "0.55", "size": "100"}, {"price": "0.57", "size": "50"}],
            "asks": [{"price": "0.60", "size": "80"}],
        }
        mock_resp.raise_for_status = MagicMock()
        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_resp)

        cache._pending.add("tok-1")
        await cache._fetch_book("tok-1", client)
        assert "tok-1" in cache._cache
        assert cache._cache["tok-1"].yes_bid == Decimal("0.57")
        assert cache._cache["tok-1"].yes_ask == Decimal("0.60")
        assert cache._cache["tok-1"].depth_bids == 2
        assert "tok-1" not in cache._pending

    @pytest.mark.asyncio
    async def test_fetch_book_error_handled(self) -> None:
        cache = OrderBookCache(ttl_seconds=60.0)
        client = AsyncMock()
        client.get = AsyncMock(side_effect=Exception("network error"))

        cache._pending.add("tok-1")
        await cache._fetch_book("tok-1", client)
        assert "tok-1" not in cache._cache
        assert "tok-1" not in cache._pending

    @pytest.mark.asyncio
    async def test_enrich_sets_correct_fields(self) -> None:
        cache = OrderBookCache(ttl_seconds=60.0)
        entry = _make_entry(yes_bid="0.50", yes_ask="0.55")
        cache._cache["tok-1"] = entry
        cache._access_order.append("tok-1")
        update = _make_update()
        client = AsyncMock()

        result = await cache.enrich(update, client)
        assert result.market_id == "m1"
        assert result.token_id == "tok-1"
        assert result.yes_bid == Decimal("0.50")
        assert result.yes_ask == Decimal("0.55")
        assert result.no_bid == max(Decimal("1") - Decimal("0.55"), Decimal("0"))
        assert result.book_depth_asks == 3
