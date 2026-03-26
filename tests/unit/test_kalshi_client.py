"""Unit tests for Kalshi scan-pressure controls."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from arb_scanner.ingestion.kalshi import KalshiClient
from arb_scanner.models.config import KalshiVenueConfig
from arb_scanner.models.market import Market, Venue

_KALSHI_BASE = "http://kalshi-test"


def _make_market(event_id: str, *, volume: str = "100") -> Market:
    """Build a minimal Kalshi market for test doubles."""
    return Market(
        venue=Venue.KALSHI,
        event_id=event_id,
        title=f"Market {event_id}",
        description="",
        resolution_criteria="",
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.45"),
        no_bid=Decimal("0.55"),
        no_ask=Decimal("0.60"),
        volume_24h=Decimal(volume),
        fees_pct=Decimal("0.07"),
        fee_model="per_contract",
        last_updated=datetime.now(tz=UTC),
    )


class TestFetchMarketsForEvents:
    """Verify event-driven fetch uses bounded collection."""

    @pytest.mark.asyncio()
    async def test_stops_fetching_new_events_after_collect_target(self) -> None:
        """Stop requesting extra event tickers once enough markets are collected."""
        client = KalshiClient(KalshiVenueConfig(base_url=_KALSHI_BASE, max_markets=2))
        raw_by_event = {
            "evt-1": [{"ticker": "m1"}, {"ticker": "m2"}],
            "evt-2": [{"ticker": "m3"}, {"ticker": "m4"}],
            "evt-3": [{"ticker": "m5"}, {"ticker": "m6"}],
        }

        async def fake_fetch(event_ticker: str) -> list[dict[str, str]]:
            return raw_by_event[event_ticker]

        def fake_parse(raw: dict[str, str]) -> Market:
            return _make_market(raw["ticker"])

        with (
            patch.object(
                client, "_fetch_event_markets", new=AsyncMock(side_effect=fake_fetch)
            ) as mock_fetch,
            patch("arb_scanner.ingestion.kalshi.parse_market", side_effect=fake_parse),
        ):
            markets = await client.fetch_markets_for_events(
                ["evt-1", "evt-2", "evt-3"],
                max_markets=2,
            )

        assert len(markets) == 4
        assert mock_fetch.await_count == 2


class TestSharedCooldown:
    """Verify venue-level 429 backoff is shared across requests."""

    @pytest.mark.asyncio()
    async def test_429_retry_after_sets_and_waits_for_shared_cooldown(self) -> None:
        """A Kalshi 429 should extend a shared cooldown window for later requests."""
        request = httpx.Request("GET", f"{_KALSHI_BASE}/events")
        response = httpx.Response(
            429,
            headers={"Retry-After": "3"},
            json={"error": "rate_limited"},
            request=request,
        )
        exc = httpx.HTTPStatusError(
            "rate limited",
            request=request,
            response=response,
        )
        client = KalshiClient(KalshiVenueConfig(base_url=_KALSHI_BASE))
        kalshi_sleep = AsyncMock()

        with patch("arb_scanner.ingestion.kalshi.time.monotonic", return_value=100.0):
            client._apply_rate_limit_cooldown(exc)

        with (
            patch("arb_scanner.ingestion.kalshi.asyncio.sleep", kalshi_sleep),
            patch("arb_scanner.ingestion.kalshi.time.monotonic", return_value=101.5),
        ):
            await client._wait_for_rate_limit_cooldown()

        assert client._rate_limit_cooldown_until == pytest.approx(103.0)
        kalshi_sleep.assert_awaited_once()
        assert kalshi_sleep.await_args.args[0] == pytest.approx(1.5)
