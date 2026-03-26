"""Unit tests for CLI scan orchestration helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from arb_scanner.cli.orchestrator import _fetch_live_markets
from arb_scanner.models.market import Market, Venue


def _make_market(event_id: str, venue: Venue) -> Market:
    """Build a minimal market fixture for orchestration tests."""
    return Market(
        venue=venue,
        event_id=event_id,
        title=f"Market {event_id}",
        description="",
        resolution_criteria="",
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.45"),
        no_bid=Decimal("0.55"),
        no_ask=Decimal("0.60"),
        volume_24h=Decimal("100"),
        fees_pct=Decimal("0.00"),
        fee_model="on_winnings" if venue is Venue.POLYMARKET else "per_contract",
        last_updated=datetime.now(tz=UTC),
    )


class _ClientContext:
    """Minimal async context manager wrapper for mocked clients."""

    def __init__(self, client: object) -> None:
        self._client = client

    async def __aenter__(self) -> object:
        return self._client

    async def __aexit__(self, *_args: object) -> None:
        return None


class TestFetchLiveMarkets:
    """Verify Kalshi event fanout remains bounded."""

    @pytest.mark.asyncio()
    async def test_uses_dedicated_kalshi_event_cap(self) -> None:
        """Event ranking should use max_relevant_events, not max_markets."""
        poly_market = _make_market("poly-1", Venue.POLYMARKET)
        kalshi_market = _make_market("kalshi-1", Venue.KALSHI)
        poly_client = SimpleNamespace(fetch_markets=AsyncMock(return_value=[poly_market]))
        kalshi_client = SimpleNamespace(
            fetch_events=AsyncMock(
                return_value=[
                    {"event_ticker": "evt-1", "title": "Event 1", "category": "sports"},
                ],
            ),
            fetch_markets_for_events=AsyncMock(return_value=[kalshi_market]),
        )
        config = SimpleNamespace(
            venues=SimpleNamespace(
                polymarket=SimpleNamespace(),
                kalshi=SimpleNamespace(max_markets=500, max_relevant_events=75),
            ),
        )
        errors: list[str] = []

        with (
            patch(
                "arb_scanner.ingestion.polymarket.PolymarketClient",
                return_value=_ClientContext(poly_client),
            ),
            patch(
                "arb_scanner.ingestion.kalshi.KalshiClient",
                return_value=_ClientContext(kalshi_client),
            ),
            patch(
                "arb_scanner.cli.orchestrator.rank_events",
                new=AsyncMock(return_value=["evt-1"]),
            ) as mock_rank_events,
        ):
            poly, kalshi = await _fetch_live_markets(config, errors)

        assert errors == []
        assert poly == [poly_market]
        assert kalshi == [kalshi_market]
        mock_rank_events.assert_awaited_once_with(
            [poly_market],
            [{"event_ticker": "evt-1", "title": "Event 1", "category": "sports"}],
            75,
        )
        kalshi_client.fetch_markets_for_events.assert_awaited_once_with(
            ["evt-1"],
            max_markets=500,
        )
