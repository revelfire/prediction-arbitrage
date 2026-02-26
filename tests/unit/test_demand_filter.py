"""Unit tests for demand-driven Kalshi market selection.

Tests the BM25 relevance ranking of Kalshi markets against the
Polymarket title corpus used in demand_rank().
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arb_scanner.matching.demand_filter import demand_rank
from arb_scanner.models.market import Market, Venue

_NOW = datetime.now(tz=timezone.utc)


def _make_market(venue: Venue, event_id: str, title: str) -> Market:
    """Build a Market with a specific venue, event_id, and title."""
    return Market(
        venue=venue,
        event_id=event_id,
        title=title,
        description="",
        resolution_criteria="",
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.45"),
        no_bid=Decimal("0.50"),
        no_ask=Decimal("0.55"),
        volume_24h=Decimal("1000"),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestDemandRankEdgeCases:
    """Verify demand_rank handles empty and degenerate inputs."""

    @pytest.mark.asyncio()
    async def test_empty_poly_returns_kalshi(self) -> None:
        """Empty Poly list returns Kalshi unchanged (up to max_markets)."""
        kalshi = [_make_market(Venue.KALSHI, "k1", "Bitcoin")]
        result = await demand_rank([], kalshi, max_markets=10)
        assert result == kalshi

    @pytest.mark.asyncio()
    async def test_empty_kalshi_returns_empty(self) -> None:
        """Empty Kalshi list returns empty."""
        poly = [_make_market(Venue.POLYMARKET, "p1", "Bitcoin")]
        result = await demand_rank(poly, [], max_markets=10)
        assert result == []

    @pytest.mark.asyncio()
    async def test_both_empty_returns_empty(self) -> None:
        """Both empty returns empty."""
        result = await demand_rank([], [], max_markets=10)
        assert result == []


# ---------------------------------------------------------------------------
# Relevance ranking
# ---------------------------------------------------------------------------


class TestDemandRankRelevance:
    """Verify relevant Kalshi markets rank higher than irrelevant ones."""

    @pytest.mark.asyncio()
    async def test_relevant_market_ranks_first(self) -> None:
        """A Kalshi market matching a Poly title should rank above noise."""
        poly = [
            _make_market(Venue.POLYMARKET, "p1", "Will Bitcoin exceed $100k by December?"),
        ]
        kalshi = [
            _make_market(Venue.KALSHI, "k1", "Super Bowl winner Kansas City"),
            _make_market(Venue.KALSHI, "k2", "Bitcoin above $100k by year-end"),
            _make_market(Venue.KALSHI, "k3", "Oscar Best Picture 2026"),
        ]
        result = await demand_rank(poly, kalshi, max_markets=3)
        assert result[0].event_id == "k2"

    @pytest.mark.asyncio()
    async def test_multiple_poly_titles_score_multiple_kalshi(self) -> None:
        """Multiple Poly titles should promote different Kalshi matches."""
        poly = [
            _make_market(Venue.POLYMARKET, "p1", "Fed rate cut March 2026"),
            _make_market(Venue.POLYMARKET, "p2", "Bitcoin price prediction"),
        ]
        kalshi = [
            _make_market(Venue.KALSHI, "k1", "Federal Reserve rate decision March"),
            _make_market(Venue.KALSHI, "k2", "Oscar Best Picture nominees"),
            _make_market(Venue.KALSHI, "k3", "Bitcoin price above $100k"),
        ]
        result = await demand_rank(poly, kalshi, max_markets=3)
        top_ids = {m.event_id for m in result[:2]}
        assert "k1" in top_ids
        assert "k3" in top_ids


# ---------------------------------------------------------------------------
# Capping
# ---------------------------------------------------------------------------


class TestDemandRankCapping:
    """Verify max_markets cap is respected."""

    @pytest.mark.asyncio()
    async def test_caps_output_to_max_markets(self) -> None:
        """Output should not exceed max_markets."""
        poly = [_make_market(Venue.POLYMARKET, "p1", "Bitcoin prediction")]
        kalshi = [_make_market(Venue.KALSHI, f"k{i}", f"Market {i}") for i in range(20)]
        result = await demand_rank(poly, kalshi, max_markets=5)
        assert len(result) == 5

    @pytest.mark.asyncio()
    async def test_zero_max_markets_returns_all(self) -> None:
        """max_markets=0 returns all Kalshi markets."""
        poly = [_make_market(Venue.POLYMARKET, "p1", "Bitcoin")]
        kalshi = [_make_market(Venue.KALSHI, f"k{i}", f"Market {i}") for i in range(10)]
        result = await demand_rank(poly, kalshi, max_markets=0)
        assert len(result) == 10


# ---------------------------------------------------------------------------
# Parlay-like titles rank low
# ---------------------------------------------------------------------------


class TestParlayDemotion:
    """Verify parlay-style titles rank below direct matches."""

    @pytest.mark.asyncio()
    async def test_parlay_ranks_below_direct_match(self) -> None:
        """A multi-game parlay sharing keywords should rank below a direct match."""
        poly = [
            _make_market(
                Venue.POLYMARKET,
                "p1",
                "Oklahoma City Thunder at Detroit Pistons Winner",
            ),
        ]
        kalshi = [
            _make_market(
                Venue.KALSHI,
                "k_parlay",
                "yes Thunder, yes Lakers, no Warriors over 220 points, yes Celtics",
            ),
            _make_market(
                Venue.KALSHI,
                "k_direct",
                "Oklahoma City at Detroit Winner",
            ),
        ]
        result = await demand_rank(poly, kalshi, max_markets=2)
        assert result[0].event_id == "k_direct"
