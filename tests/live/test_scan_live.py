"""Live scan integration tests.

Verifies that the ingestion pipeline works end-to-end against real APIs.
Requires LIVE_TESTS=1 (and ANTHROPIC_API_KEY for full pipeline tests).
"""

from __future__ import annotations

import asyncio

import pytest

from arb_scanner.ingestion.kalshi import KalshiClient
from arb_scanner.ingestion.polymarket import PolymarketClient
from arb_scanner.models.config import KalshiVenueConfig, PolymarketVenueConfig
from arb_scanner.models.market import Market, Venue

from tests.live.conftest import requires_live


@pytest.mark.live
class TestScanLive:
    """Live integration tests for the scan pipeline components."""

    @requires_live
    @pytest.mark.asyncio
    async def test_dry_run_scan_works(self) -> None:
        """Verify dry-run scan completes without live APIs.

        This is a sanity check that the fixture-based path still works.
        """
        from arb_scanner.cli._helpers import load_config_safe
        from arb_scanner.cli.orchestrator import run_scan

        config = load_config_safe(dry_run=True)
        result = await run_scan(config, dry_run=True)
        assert isinstance(result, dict)
        assert "scan_id" in result
        assert "polymarket_count" in result or "opportunities" in result

    @requires_live
    @pytest.mark.asyncio
    async def test_both_venues_fetch_markets_concurrently(self) -> None:
        """Both Polymarket and Kalshi clients can fetch Market objects together.

        Proves end-to-end ingestion works for both venues without running
        the full matching/calculation pipeline (which needs Claude + DB).
        """
        poly_config = PolymarketVenueConfig()
        kalshi_config = KalshiVenueConfig()

        async def fetch_poly() -> list[Market]:
            async with PolymarketClient(poly_config) as client:
                return await client.fetch_markets()

        async def fetch_kalshi() -> list[Market]:
            async with KalshiClient(kalshi_config) as client:
                return await client.fetch_markets()

        results = await asyncio.gather(
            fetch_poly(),
            fetch_kalshi(),
            return_exceptions=True,
        )

        poly_result = results[0]
        kalshi_result = results[1]

        # Both should succeed (not be exceptions)
        assert not isinstance(poly_result, BaseException), f"Polymarket fetch failed: {poly_result}"
        assert not isinstance(kalshi_result, BaseException), f"Kalshi fetch failed: {kalshi_result}"

        assert len(poly_result) > 0, "Polymarket returned 0 markets"
        assert len(kalshi_result) > 0, "Kalshi returned 0 markets"

        # Verify venue tagging is correct
        for m in poly_result[:5]:
            assert m.venue == Venue.POLYMARKET
        for m in kalshi_result[:5]:
            assert m.venue == Venue.KALSHI
