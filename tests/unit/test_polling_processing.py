"""Tests for polling mode market_id handling."""

from __future__ import annotations

from decimal import Decimal

from arb_scanner.flippening.ws_parser import parse_orderbook


class TestPollingMarketId:
    """Verify polling mode passes market_id through."""

    def test_parse_orderbook_with_market_id(self) -> None:
        """parse_orderbook passes market_id to PriceUpdate."""
        data = {
            "bids": [{"price": "0.45", "size": "100"}],
            "asks": [{"price": "0.55", "size": "100"}],
        }
        result = parse_orderbook("tok-1", data, market_id="mkt-1")
        assert result is not None
        assert result.market_id == "mkt-1"
        assert result.token_id == "tok-1"

    def test_parse_orderbook_default_empty_market_id(self) -> None:
        """parse_orderbook defaults to empty market_id."""
        data = {
            "bids": [{"price": "0.45", "size": "100"}],
            "asks": [{"price": "0.55", "size": "100"}],
        }
        result = parse_orderbook("tok-1", data)
        assert result is not None
        assert result.market_id == ""

    def test_parse_orderbook_prices_correct(self) -> None:
        """parse_orderbook extracts correct bid/ask prices."""
        data = {
            "bids": [{"price": "0.40", "size": "50"}, {"price": "0.45", "size": "100"}],
            "asks": [{"price": "0.55", "size": "100"}, {"price": "0.60", "size": "50"}],
        }
        result = parse_orderbook("tok-1", data, market_id="mkt-1")
        assert result is not None
        assert result.yes_bid == Decimal("0.45")
        assert result.yes_ask == Decimal("0.55")
