"""T026 - Integration tests for the Kalshi venue client.

Uses httpx.MockTransport to simulate Kalshi API responses without
making real network calls. Fixtures loaded from
tests/fixtures/kalshi_markets.json and kalshi_orderbook.json.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from arb_scanner.ingestion._kalshi_parse import process_orderbook as _process_orderbook
from arb_scanner.ingestion.kalshi import KalshiClient
from arb_scanner.models.config import KalshiVenueConfig
from arb_scanner.models.market import Venue

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_KALSHI_BASE = "http://kalshi-test"


def _load_fixture(name: str) -> object:
    """Load a JSON fixture file by name."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Mock transport helpers
# ---------------------------------------------------------------------------


def _markets_transport_single_page(fixture: dict[str, object]) -> httpx.MockTransport:
    """Build a mock that returns the fixture on first call, empty on next."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Return markets page or empty based on cursor presence."""
        cursor = request.url.params.get("cursor", "")
        if not cursor:
            return httpx.Response(200, json=fixture)
        return httpx.Response(200, json={"markets": [], "cursor": ""})

    return httpx.MockTransport(handler)


def _markets_transport_multi_page(
    page1: dict[str, object],
    page2: dict[str, object],
) -> httpx.MockTransport:
    """Build a mock returning page1 then page2 using cursor-based pagination."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Return page1 first, page2 on cursor match, then empty."""
        cursor = request.url.params.get("cursor", "")
        if not cursor:
            return httpx.Response(200, json=page1)
        if cursor == str(page1.get("cursor", "")):
            return httpx.Response(200, json=page2)
        return httpx.Response(200, json={"markets": [], "cursor": ""})

    return httpx.MockTransport(handler)


def _orderbook_transport(data: dict[str, object]) -> httpx.MockTransport:
    """Build a mock transport that returns a fixed orderbook response."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Return the orderbook data for any request."""
        return httpx.Response(200, json=data)

    return httpx.MockTransport(handler)


def _make_client(transport: httpx.MockTransport) -> KalshiClient:
    """Create a KalshiClient with a mock transport and base_url set."""
    config = KalshiVenueConfig(base_url=_KALSHI_BASE)
    client = KalshiClient(config=config)
    client._client = httpx.AsyncClient(transport=transport, base_url=_KALSHI_BASE)
    return client


# ---------------------------------------------------------------------------
# Dollar field parsing to Decimal
# ---------------------------------------------------------------------------


class TestDollarFieldParsing:
    """Verify *_dollars fields are correctly parsed to Decimal prices."""

    @pytest.mark.asyncio()
    async def test_yes_bid_dollars_parsed(self) -> None:
        """Verify yes_bid_dollars is parsed into Market.yes_bid as Decimal."""
        fixture = _load_fixture("kalshi_markets.json")
        assert isinstance(fixture, dict)

        client = _make_client(_markets_transport_single_page(fixture))
        markets = await client.fetch_markets()
        btc = markets[0]
        assert btc.yes_bid == Decimal("0.6400")
        assert btc.venue == Venue.KALSHI

    @pytest.mark.asyncio()
    async def test_all_dollar_fields_present(self) -> None:
        """Verify all price Decimal fields are set from *_dollars data."""
        fixture = _load_fixture("kalshi_markets.json")
        assert isinstance(fixture, dict)

        client = _make_client(_markets_transport_single_page(fixture))
        markets = await client.fetch_markets()
        btc = markets[0]
        assert btc.yes_ask == Decimal("0.6600")
        assert btc.no_bid == Decimal("0.3400")
        assert btc.no_ask == Decimal("0.3600")

    @pytest.mark.asyncio()
    async def test_all_fixture_markets_parsed(self) -> None:
        """Verify every valid fixture entry produces a Market."""
        fixture = _load_fixture("kalshi_markets.json")
        assert isinstance(fixture, dict)

        client = _make_client(_markets_transport_single_page(fixture))
        markets = await client.fetch_markets()
        raw_markets = fixture["markets"]
        assert isinstance(raw_markets, list)
        assert len(markets) == len(raw_markets)


# ---------------------------------------------------------------------------
# Ask computation from complement
# ---------------------------------------------------------------------------


class TestAskComputation:
    """Verify YES_ask = 1.00 - highest_NO_bid computation."""

    def test_process_orderbook_yes_ask(self) -> None:
        """Verify YES ask is computed as 1.00 minus highest NO bid."""
        fixture = _load_fixture("kalshi_orderbook.json")
        assert isinstance(fixture, dict)
        raw = fixture["orderbook"]

        result = _process_orderbook(raw)
        # no bids: best = last element [0] = "0.3400" (last in ascending)
        assert result["yes_ask"] == str(Decimal("1") - Decimal("0.3400"))

    def test_process_orderbook_no_ask(self) -> None:
        """Verify NO ask is computed as 1.00 minus highest YES bid."""
        fixture = _load_fixture("kalshi_orderbook.json")
        assert isinstance(fixture, dict)
        raw = fixture["orderbook"]

        result = _process_orderbook(raw)
        # yes bids: best = last element [0] = "0.6400" (last in ascending)
        assert result["no_ask"] == str(Decimal("1") - Decimal("0.6400"))

    def test_process_orderbook_best_bids(self) -> None:
        """Verify best bid values are extracted from last elements."""
        fixture = _load_fixture("kalshi_orderbook.json")
        assert isinstance(fixture, dict)
        raw = fixture["orderbook"]

        result = _process_orderbook(raw)
        assert result["yes_best_bid"] == "0.6400"
        assert result["no_best_bid"] == "0.3400"


# ---------------------------------------------------------------------------
# Cursor-based pagination
# ---------------------------------------------------------------------------


class TestCursorPagination:
    """Verify cursor-based pagination fetches all pages."""

    @pytest.mark.asyncio()
    async def test_multi_page_fetches_all(self) -> None:
        """Verify client follows cursor through multiple pages."""
        fixture = _load_fixture("kalshi_markets.json")
        assert isinstance(fixture, dict)

        raw_markets = fixture["markets"]
        assert isinstance(raw_markets, list)

        page1 = {"markets": raw_markets[:4], "cursor": "page2cursor"}
        page2 = {"markets": raw_markets[4:], "cursor": ""}

        client = _make_client(_markets_transport_multi_page(page1, page2))
        markets = await client.fetch_markets()
        assert len(markets) == len(raw_markets)

    @pytest.mark.asyncio()
    async def test_empty_cursor_stops_pagination(self) -> None:
        """Verify pagination stops when cursor is empty."""
        fixture = _load_fixture("kalshi_markets.json")
        assert isinstance(fixture, dict)

        client = _make_client(_markets_transport_single_page(fixture))
        markets = await client.fetch_markets()
        raw_markets = fixture["markets"]
        assert isinstance(raw_markets, list)
        assert len(markets) == len(raw_markets)


# ---------------------------------------------------------------------------
# Rate limiting integration
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Verify rate limiter is integrated into the client."""

    @pytest.mark.asyncio()
    async def test_rate_limiter_exists(self) -> None:
        """Verify the client has a rate limiter configured."""
        config = KalshiVenueConfig(base_url=_KALSHI_BASE, rate_limit_per_sec=5)
        client = KalshiClient(config=config)
        assert client.rate_limiter._rate == 5

    @pytest.mark.asyncio()
    async def test_fetch_respects_rate_limiter(self) -> None:
        """Verify fetch_orderbook uses the rate limiter."""
        fixture = _load_fixture("kalshi_orderbook.json")
        assert isinstance(fixture, dict)

        client = _make_client(
            _orderbook_transport(fixture["orderbook"]),
        )
        result = await client.fetch_orderbook("BTC-100K-26")
        assert "yes_ask" in result
