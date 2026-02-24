"""T031 - Unit tests for BM25 pre-filter candidate pair selection.

Tests the BM25+ index construction, scoring of matching/unrelated titles,
and candidate pair reduction from cross-product.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arb_scanner.matching.prefilter import prefilter_candidates
from arb_scanner.models.market import Market, Venue

_NOW = datetime.now(tz=timezone.utc)


def _make_market(venue: Venue, event_id: str, title: str) -> Market:
    """Build a Market with a specific venue, event_id, and title."""
    return Market(
        venue=venue,
        event_id=event_id,
        title=title,
        description="Test market",
        resolution_criteria="Test criteria",
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
# BM25 index construction
# ---------------------------------------------------------------------------


class TestBM25IndexConstruction:
    """Verify the BM25 index can be built from market titles."""

    @pytest.mark.asyncio()
    async def test_empty_inputs_return_empty(self) -> None:
        """Verify empty market lists produce no candidate pairs."""
        result = await prefilter_candidates([], [])
        assert result == []

    @pytest.mark.asyncio()
    async def test_empty_poly_returns_empty(self) -> None:
        """Verify empty Polymarket list produces no candidates."""
        kalshi = [_make_market(Venue.KALSHI, "k1", "Bitcoin price")]
        result = await prefilter_candidates([], kalshi)
        assert result == []

    @pytest.mark.asyncio()
    async def test_empty_kalshi_returns_empty(self) -> None:
        """Verify empty Kalshi list produces no candidates."""
        poly = [_make_market(Venue.POLYMARKET, "p1", "Bitcoin price")]
        result = await prefilter_candidates(poly, [])
        assert result == []

    @pytest.mark.asyncio()
    async def test_basic_index_works(self) -> None:
        """Verify the index can be constructed and queried."""
        poly = [_make_market(Venue.POLYMARKET, "p1", "Bitcoin above 100k")]
        kalshi = [_make_market(Venue.KALSHI, "k1", "Bitcoin above 100k")]

        result = await prefilter_candidates(poly, kalshi)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Known-matching titles score above threshold
# ---------------------------------------------------------------------------


class TestMatchingTitles:
    """Verify similar titles produce positive BM25 scores."""

    @pytest.mark.asyncio()
    async def test_bitcoin_titles_match(self) -> None:
        """Verify 'Bitcoin above 100k' matches 'BTC exceeds $100k'."""
        poly = [
            _make_market(Venue.POLYMARKET, "p1", "Will Bitcoin exceed $100k by December 2026?"),
        ]
        kalshi = [
            _make_market(Venue.KALSHI, "k1", "Bitcoin above $100k by year-end 2026"),
        ]

        result = await prefilter_candidates(poly, kalshi)
        assert len(result) > 0
        _, _, score = result[0]
        assert score > 0.0

    @pytest.mark.asyncio()
    async def test_fed_rate_titles_match(self) -> None:
        """Verify Fed rate cut titles from different venues match."""
        poly = [
            _make_market(Venue.POLYMARKET, "p1", "Will the Fed cut rates in March 2026?"),
        ]
        kalshi = [
            _make_market(Venue.KALSHI, "k1", "Fed rate cut March 2026"),
        ]

        result = await prefilter_candidates(poly, kalshi)
        assert len(result) > 0
        _, _, score = result[0]
        assert score > 0.0

    @pytest.mark.asyncio()
    async def test_identical_titles_high_score(self) -> None:
        """Verify identical titles produce a high BM25 score."""
        title = "Will Bitcoin exceed $100k by December 2026?"
        poly = [_make_market(Venue.POLYMARKET, "p1", title)]
        kalshi = [_make_market(Venue.KALSHI, "k1", title)]

        result = await prefilter_candidates(poly, kalshi)
        assert len(result) == 1
        _, _, score = result[0]
        assert score > 1.0  # Identical titles should score well above zero


# ---------------------------------------------------------------------------
# Unrelated titles score below threshold
# ---------------------------------------------------------------------------


class TestUnrelatedTitles:
    """Verify unrelated titles produce zero or near-zero scores."""

    @pytest.mark.asyncio()
    async def test_unrelated_titles_low_score(self) -> None:
        """Verify unrelated titles have zero-score entries filtered out."""
        poly = [
            _make_market(Venue.POLYMARKET, "p1", "Will Apple release a foldable iPhone in 2026?"),
        ]
        kalshi = [
            _make_market(Venue.KALSHI, "k1", "Super Bowl LXI winner Kansas City Chiefs"),
        ]

        result = await prefilter_candidates(poly, kalshi)
        # Should be empty or have very low scores (filtered at score > 0)
        for _, _, score in result:
            assert score < 1.0


# ---------------------------------------------------------------------------
# Candidate pair reduction
# ---------------------------------------------------------------------------


class TestCandidatePairReduction:
    """Verify output is much smaller than the full cross-product."""

    @pytest.mark.asyncio()
    async def test_reduction_from_cross_product(self) -> None:
        """Verify candidate pairs are reduced from the full cross-product."""
        poly_titles = [
            "Will Bitcoin exceed $100k by December 2026?",
            "Will the Fed cut rates in March 2026?",
            "Will Apple release a foldable iPhone in 2026?",
            "Will SpaceX complete a Starship orbital flight by July 2026?",
            "Will US GDP growth exceed 3% in Q1 2026?",
        ]
        kalshi_titles = [
            "Bitcoin above $100k by year-end 2026",
            "Fed rate cut March 2026",
            "S&P 500 above 6,000 on March 31, 2026",
            "US government shutdown before April 2026",
            "Ethereum above $5,000 by end of June 2026",
            "Trump approval rating above 50% in March 2026",
            "OpenAI releases GPT-5 before July 2026",
            "Super Bowl LXI winner: Kansas City Chiefs",
        ]

        poly = [_make_market(Venue.POLYMARKET, f"p{i}", t) for i, t in enumerate(poly_titles)]
        kalshi = [_make_market(Venue.KALSHI, f"k{i}", t) for i, t in enumerate(kalshi_titles)]

        cross_product_size = len(poly) * len(kalshi)  # 40
        result = await prefilter_candidates(poly, kalshi, top_k=3)

        assert len(result) < cross_product_size
        # At least some matches should be found
        assert len(result) > 0

    @pytest.mark.asyncio()
    async def test_results_sorted_by_score_descending(self) -> None:
        """Verify results are sorted by BM25 score in descending order."""
        poly = [
            _make_market(Venue.POLYMARKET, "p1", "Bitcoin price prediction 2026"),
            _make_market(Venue.POLYMARKET, "p2", "Fed interest rate decision"),
        ]
        kalshi = [
            _make_market(Venue.KALSHI, "k1", "Bitcoin price prediction 2026"),
            _make_market(Venue.KALSHI, "k2", "Federal Reserve rate decision March"),
        ]

        result = await prefilter_candidates(poly, kalshi)
        scores = [s for _, _, s in result]
        assert scores == sorted(scores, reverse=True)
