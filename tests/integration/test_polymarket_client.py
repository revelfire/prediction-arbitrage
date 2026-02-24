"""T025 - Integration tests for the Polymarket venue client.

Uses httpx.MockTransport to simulate Gamma API and CLOB API responses
without making real network calls. Fixtures loaded from
tests/fixtures/polymarket_markets.json and polymarket_orderbook.json.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from arb_scanner.ingestion.polymarket import PolymarketClient
from arb_scanner.models.config import PolymarketVenueConfig
from arb_scanner.models.market import Venue

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_GAMMA_BASE = "http://gamma-test"
_CLOB_BASE = "http://clob-test"


def _load_fixture(name: str) -> object:
    """Load a JSON fixture file by name."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Mock transport helpers
# ---------------------------------------------------------------------------


def _gamma_transport_single_page(fixture: list[dict[str, object]]) -> httpx.MockTransport:
    """Build a Gamma mock transport that returns one page then empty."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Return fixture on first page, empty list on subsequent."""
        offset = int(request.url.params.get("offset", "0"))
        if offset == 0:
            return httpx.Response(200, json=fixture)
        return httpx.Response(200, json=[])

    return httpx.MockTransport(handler)


def _gamma_transport_multi_page(
    page1: list[dict[str, object]],
    page2: list[dict[str, object]],
) -> httpx.MockTransport:
    """Build a Gamma mock transport that returns two pages."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Return page1 at offset 0, page2 at offset 100, empty after."""
        offset = int(request.url.params.get("offset", "0"))
        if offset == 0:
            return httpx.Response(200, json=page1)
        if offset == 100:
            return httpx.Response(200, json=page2)
        return httpx.Response(200, json=[])

    return httpx.MockTransport(handler)


def _clob_transport(orderbook: dict[str, object]) -> httpx.MockTransport:
    """Build a CLOB mock transport that returns a fixed orderbook."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Return the orderbook fixture for any /book request."""
        return httpx.Response(200, json=orderbook)

    return httpx.MockTransport(handler)


def _make_client(
    gamma_transport: httpx.MockTransport,
    clob_transport: httpx.MockTransport,
) -> PolymarketClient:
    """Create a PolymarketClient with mock transports and base_url set."""
    config = PolymarketVenueConfig(
        gamma_base_url=_GAMMA_BASE,
        clob_base_url=_CLOB_BASE,
    )
    client = PolymarketClient(config=config)
    client._client = httpx.AsyncClient(transport=gamma_transport, base_url=_GAMMA_BASE)
    client._clob_client = httpx.AsyncClient(transport=clob_transport, base_url=_CLOB_BASE)
    return client


# ---------------------------------------------------------------------------
# Market model mapping from Gamma API response
# ---------------------------------------------------------------------------


class TestMarketMapping:
    """Verify Gamma API response dicts are parsed into Market models."""

    @pytest.mark.asyncio()
    async def test_parses_market_fields(self) -> None:
        """Verify core Market fields are populated from Gamma fixture."""
        fixture = _load_fixture("polymarket_markets.json")
        assert isinstance(fixture, list)

        client = _make_client(
            _gamma_transport_single_page(fixture),
            _clob_transport({}),
        )
        markets = await client.fetch_markets()
        assert len(markets) > 0

        btc_market = markets[0]
        assert btc_market.venue == Venue.POLYMARKET
        assert "Bitcoin" in btc_market.title
        assert btc_market.event_id == fixture[0]["id"]

    @pytest.mark.asyncio()
    async def test_all_fixture_markets_parsed(self) -> None:
        """Verify every valid fixture entry produces a Market."""
        fixture = _load_fixture("polymarket_markets.json")
        assert isinstance(fixture, list)

        client = _make_client(
            _gamma_transport_single_page(fixture),
            _clob_transport({}),
        )
        markets = await client.fetch_markets()
        assert len(markets) == len(fixture)

    @pytest.mark.asyncio()
    async def test_expiry_parsed_from_endDate(self) -> None:
        """Verify the endDate field is parsed into Market.expiry."""
        fixture = _load_fixture("polymarket_markets.json")
        assert isinstance(fixture, list)

        client = _make_client(
            _gamma_transport_single_page(fixture),
            _clob_transport({}),
        )
        markets = await client.fetch_markets()
        assert markets[0].expiry is not None
        assert markets[0].expiry.year == 2026


# ---------------------------------------------------------------------------
# JSON-string field parsing
# ---------------------------------------------------------------------------


class TestJsonStringParsing:
    """Verify outcomePrices and clobTokenIds JSON-string fields are parsed."""

    @pytest.mark.asyncio()
    async def test_outcome_prices_used_for_pricing(self) -> None:
        """Verify outcomePrices JSON string drives yes/no price calculation."""
        fixture = _load_fixture("polymarket_markets.json")
        assert isinstance(fixture, list)

        client = _make_client(
            _gamma_transport_single_page(fixture),
            _clob_transport({}),
        )
        markets = await client.fetch_markets()
        btc = markets[0]
        # outcomePrices = '["0.62","0.38"]'
        # yes_ask = min(0.62 + 0.01, 1) = 0.63
        assert btc.yes_ask == Decimal("0.63")
        assert btc.no_ask == Decimal("0.39")

    @pytest.mark.asyncio()
    async def test_clobTokenIds_are_valid_json_strings(self) -> None:
        """Verify clobTokenIds in the fixture are parseable JSON strings."""
        fixture = _load_fixture("polymarket_markets.json")
        assert isinstance(fixture, list)
        first = fixture[0]
        tokens = json.loads(str(first["clobTokenIds"]))
        assert isinstance(tokens, list)
        assert len(tokens) == 2


# ---------------------------------------------------------------------------
# Offset pagination handling
# ---------------------------------------------------------------------------


class TestPagination:
    """Verify offset pagination iterates through all pages."""

    @pytest.mark.asyncio()
    async def test_multi_page_fetches_all(self) -> None:
        """Verify client fetches markets from multiple pages."""
        fixture = _load_fixture("polymarket_markets.json")
        assert isinstance(fixture, list)
        # 100 items on page 1 triggers page 2 fetch (PAGE_LIMIT = 100)
        page1 = fixture * 13  # 8 * 13 = 104 items, but we need exactly 100
        page1 = page1[:100]
        page2 = fixture[:3]

        client = _make_client(
            _gamma_transport_multi_page(page1, page2),
            _clob_transport({}),
        )
        markets = await client.fetch_markets()
        assert len(markets) == 103

    @pytest.mark.asyncio()
    async def test_single_short_page_stops(self) -> None:
        """Verify pagination stops when page has fewer than PAGE_LIMIT items."""
        fixture = _load_fixture("polymarket_markets.json")
        assert isinstance(fixture, list)

        client = _make_client(
            _gamma_transport_single_page(fixture),
            _clob_transport({}),
        )
        markets = await client.fetch_markets()
        # fixture has 8 items < 100, so only one page
        assert len(markets) == 8


# ---------------------------------------------------------------------------
# Order book fetch and parsing
# ---------------------------------------------------------------------------


class TestOrderBook:
    """Verify order book fetch and parsing from the CLOB API."""

    @pytest.mark.asyncio()
    async def test_fetch_orderbook_returns_data(self) -> None:
        """Verify fetch_orderbook returns bid/ask data from CLOB."""
        orderbook = _load_fixture("polymarket_orderbook.json")

        client = _make_client(
            _gamma_transport_single_page([]),
            _clob_transport(orderbook),  # type: ignore[arg-type]
        )
        result = await client.fetch_orderbook("test-token-id")
        assert "bids" in result
        assert "asks" in result

    @pytest.mark.asyncio()
    async def test_orderbook_bids_sorted(self) -> None:
        """Verify bids array is present and ordered in the response."""
        orderbook = _load_fixture("polymarket_orderbook.json")

        client = _make_client(
            _gamma_transport_single_page([]),
            _clob_transport(orderbook),  # type: ignore[arg-type]
        )
        result = await client.fetch_orderbook("test-token-id")
        bids = result["bids"]
        assert isinstance(bids, list)
        assert len(bids) == 5
