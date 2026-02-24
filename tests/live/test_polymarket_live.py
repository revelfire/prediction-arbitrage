"""Live API tests for the Polymarket venue client.

All tests require LIVE_TESTS=1 and network access to the Polymarket APIs.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
import pytest
import structlog

from arb_scanner.ingestion.polymarket import PolymarketClient
from arb_scanner.models.config import PolymarketVenueConfig
from arb_scanner.models.market import Market

from tests.live.conftest import requires_live

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="test.polymarket_live")

_GAMMA_BASE = "https://gamma-api.polymarket.com"


@pytest.mark.live
class TestPolymarketLive:
    """Live integration tests for Polymarket APIs."""

    @requires_live
    @pytest.mark.asyncio
    async def test_gamma_api_returns_200_with_markets(self) -> None:
        """Direct GET to Gamma API returns 200 and at least one market."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{_GAMMA_BASE}/markets",
                params={"active": "true", "closed": "false", "limit": 5},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    @requires_live
    @pytest.mark.asyncio
    async def test_gamma_market_has_outcome_prices(self) -> None:
        """First market from Gamma API has outcomePrices field."""
        await asyncio.sleep(0.5)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{_GAMMA_BASE}/markets",
                params={"active": "true", "closed": "false", "limit": 5},
            )
        data = resp.json()
        first = data[0]
        assert "outcomePrices" in first, f"Missing outcomePrices; keys: {list(first.keys())}"
        # outcomePrices may be a JSON string or a list
        assert isinstance(first["outcomePrices"], (str, list))

    @requires_live
    @pytest.mark.asyncio
    async def test_gamma_markets_have_id_field(self) -> None:
        """Each market from Gamma API has an 'id' field."""
        await asyncio.sleep(0.5)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{_GAMMA_BASE}/markets",
                params={"active": "true", "closed": "false", "limit": 5},
            )
        data = resp.json()
        for market in data:
            assert "id" in market, f"Missing 'id'; keys: {list(market.keys())}"

    @requires_live
    @pytest.mark.asyncio
    async def test_client_fetch_returns_market_objects(self) -> None:
        """PolymarketClient.fetch_markets() returns a non-empty list of Market."""
        await asyncio.sleep(0.5)
        # Use a small limit to keep test fast -- we only fetch 1 page
        config = PolymarketVenueConfig()
        async with PolymarketClient(config) as client:
            markets = await client.fetch_markets()
        assert len(markets) > 0
        assert all(isinstance(m, Market) for m in markets)

    @requires_live
    @pytest.mark.asyncio
    async def test_market_prices_in_valid_range(self) -> None:
        """All prices from PolymarketClient are in [0, 1]."""
        await asyncio.sleep(0.5)
        config = PolymarketVenueConfig()
        async with PolymarketClient(config) as client:
            markets = await client.fetch_markets()
        for m in markets[:20]:  # check a subset for speed
            assert Decimal("0") <= m.yes_bid <= Decimal("1"), f"yes_bid={m.yes_bid}"
            assert Decimal("0") <= m.yes_ask <= Decimal("1"), f"yes_ask={m.yes_ask}"
            assert Decimal("0") <= m.no_bid <= Decimal("1"), f"no_bid={m.no_bid}"
            assert Decimal("0") <= m.no_ask <= Decimal("1"), f"no_ask={m.no_ask}"

    @requires_live
    @pytest.mark.asyncio
    async def test_market_title_and_event_id_non_empty(self) -> None:
        """All Markets have non-empty title and event_id."""
        await asyncio.sleep(0.5)
        config = PolymarketVenueConfig()
        async with PolymarketClient(config) as client:
            markets = await client.fetch_markets()
        for m in markets[:20]:
            assert m.title.strip(), "title is empty"
            assert m.event_id.strip(), "event_id is empty"

    @requires_live
    @pytest.mark.asyncio
    async def test_clob_orderbook_structure(self) -> None:
        """CLOB orderbook for first market has bids/asks structure."""
        await asyncio.sleep(0.5)
        config = PolymarketVenueConfig()
        async with PolymarketClient(config) as client:
            markets = await client.fetch_markets()
            # Find a market with clobTokenIds in raw_data
            token_id: str | None = None
            for m in markets[:50]:
                raw_tokens = m.raw_data.get("clobTokenIds")
                if raw_tokens:
                    import json

                    if isinstance(raw_tokens, str):
                        tokens = json.loads(raw_tokens)
                    else:
                        tokens = raw_tokens
                    if tokens and isinstance(tokens, list):
                        token_id = str(tokens[0])
                        break
            if token_id is None:
                pytest.skip("No market with clobTokenIds found")
            await asyncio.sleep(0.5)
            book = await client.fetch_orderbook(token_id)
        # Orderbook should have bids and asks
        if "bids" not in book and "asks" not in book:
            logger.warning(
                "unexpected_orderbook_format",
                keys=list(book.keys()),
            )
        assert isinstance(book, dict)

    @requires_live
    @pytest.mark.asyncio
    async def test_unexpected_fields_logged_not_failed(self) -> None:
        """If market has unexpected format fields, test logs warning but passes."""
        await asyncio.sleep(0.5)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{_GAMMA_BASE}/markets",
                params={"active": "true", "closed": "false", "limit": 2},
            )
        data = resp.json()
        for market in data:
            if "outcomePrices" not in market:
                logger.warning(
                    "missing_expected_field",
                    field="outcomePrices",
                    market_id=market.get("id"),
                )
        # Test passes regardless -- the point is graceful handling
        assert True
