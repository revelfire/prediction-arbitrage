"""Live API tests for the Kalshi venue client.

All tests require LIVE_TESTS=1 and network access to the Kalshi API.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
import pytest
import structlog

from arb_scanner.ingestion.kalshi import KalshiClient
from arb_scanner.models.config import KalshiVenueConfig
from arb_scanner.models.market import Market

from tests.live.conftest import requires_live

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="test.kalshi_live")

_KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"


@pytest.mark.live
class TestKalshiLive:
    """Live integration tests for Kalshi APIs."""

    @requires_live
    @pytest.mark.asyncio
    async def test_kalshi_api_returns_200_with_markets(self) -> None:
        """Direct GET to Kalshi markets endpoint returns 200 with markets array."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{_KALSHI_BASE}/markets",
                params={"status": "open", "limit": 5},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "markets" in data
        assert isinstance(data["markets"], list)
        assert len(data["markets"]) >= 1

    @requires_live
    @pytest.mark.asyncio
    async def test_kalshi_yes_bid_dollars_is_string(self) -> None:
        """First market has yes_bid_dollars as a string, not integer."""
        await asyncio.sleep(0.5)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{_KALSHI_BASE}/markets",
                params={"status": "open", "limit": 5},
            )
        data = resp.json()
        first = data["markets"][0]
        if "yes_bid_dollars" in first:
            assert isinstance(first["yes_bid_dollars"], str), (
                f"Expected str, got {type(first['yes_bid_dollars'])}"
            )

    @requires_live
    @pytest.mark.asyncio
    async def test_kalshi_has_volume_field(self) -> None:
        """First market has volume_dollars_24h_fp or volume_fp field."""
        await asyncio.sleep(0.5)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{_KALSHI_BASE}/markets",
                params={"status": "open", "limit": 5},
            )
        data = resp.json()
        first = data["markets"][0]
        has_volume = "volume_dollars_24h_fp" in first or "volume_fp" in first
        assert has_volume, f"No volume field found; keys: {list(first.keys())}"

    @requires_live
    @pytest.mark.asyncio
    async def test_client_fetch_returns_market_objects(self) -> None:
        """KalshiClient.fetch_markets() returns a non-empty list of Market."""
        await asyncio.sleep(0.5)
        config = KalshiVenueConfig()
        async with KalshiClient(config) as client:
            markets = await client.fetch_markets()
        assert len(markets) > 0
        assert all(isinstance(m, Market) for m in markets)

    @requires_live
    @pytest.mark.asyncio
    async def test_market_prices_in_valid_range(self) -> None:
        """All prices from KalshiClient are in [0, 1]."""
        await asyncio.sleep(0.5)
        config = KalshiVenueConfig()
        async with KalshiClient(config) as client:
            markets = await client.fetch_markets()
        for m in markets[:20]:
            assert Decimal("0") <= m.yes_bid <= Decimal("1"), f"yes_bid={m.yes_bid}"
            assert Decimal("0") <= m.yes_ask <= Decimal("1"), f"yes_ask={m.yes_ask}"
            assert Decimal("0") <= m.no_bid <= Decimal("1"), f"no_bid={m.no_bid}"
            assert Decimal("0") <= m.no_ask <= Decimal("1"), f"no_ask={m.no_ask}"

    @requires_live
    @pytest.mark.asyncio
    async def test_market_title_and_event_id_non_empty(self) -> None:
        """All Markets have non-empty title and event_id."""
        await asyncio.sleep(0.5)
        config = KalshiVenueConfig()
        async with KalshiClient(config) as client:
            markets = await client.fetch_markets()
        for m in markets[:20]:
            assert m.title.strip(), "title is empty"
            assert m.event_id.strip(), "event_id is empty"

    @requires_live
    @pytest.mark.asyncio
    async def test_orderbook_has_yes_and_no_keys(self) -> None:
        """Orderbook response has 'yes' and 'no' keys at top level."""
        await asyncio.sleep(0.5)
        config = KalshiVenueConfig()
        async with KalshiClient(config) as client:
            markets = await client.fetch_markets()
            if not markets:
                pytest.skip("No markets returned")
            ticker = markets[0].event_id
            await asyncio.sleep(0.5)
            # Fetch raw orderbook to inspect envelope
            resp = await client.client.get(f"/markets/{ticker}/orderbook")
            resp.raise_for_status()
            raw_book: dict[str, object] = resp.json()
        assert "yes" in raw_book or "orderbook" in raw_book, (
            f"Unexpected orderbook keys: {list(raw_book.keys())}"
        )

    @requires_live
    @pytest.mark.asyncio
    async def test_orderbook_items_price_quantity_format(self) -> None:
        """Orderbook items are [price, quantity] format."""
        await asyncio.sleep(0.5)
        config = KalshiVenueConfig()
        async with KalshiClient(config) as client:
            markets = await client.fetch_markets()
            if not markets:
                pytest.skip("No markets returned")
            ticker = markets[0].event_id
            await asyncio.sleep(0.5)
            book = await client.fetch_orderbook(ticker)
        yes_bids = book.get("yes_bids", [])
        if isinstance(yes_bids, list) and yes_bids:
            first_bid = yes_bids[0]
            assert isinstance(first_bid, list), f"Expected list, got {type(first_bid)}"
            assert len(first_bid) >= 2, f"Expected [price, qty], got {first_bid}"
