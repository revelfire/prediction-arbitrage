"""Tests for the embedding-based re-ranking prefilter.

Uses 3-dimensional vectors for simplicity since cosine similarity
works identically regardless of dimensionality.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arb_scanner.matching.embedding_prefilter import embedding_rerank
from arb_scanner.models.config import EmbeddingConfig
from arb_scanner.models.market import Market, Venue

_NOW = datetime.now(tz=timezone.utc)


def _make_market(venue: Venue, event_id: str, title: str = "Test") -> Market:
    """Build a minimal Market for testing."""
    return Market(
        venue=venue,
        event_id=event_id,
        title=title,
        description="desc",
        resolution_criteria="criteria",
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.45"),
        no_bid=Decimal("0.50"),
        no_ask=Decimal("0.55"),
        volume_24h=Decimal("1000"),
        fees_pct=Decimal("0.00"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )


def _cfg(threshold: float = 0.60) -> EmbeddingConfig:
    """Build an EmbeddingConfig with 3-d vectors and the given threshold."""
    return EmbeddingConfig(enabled=True, cosine_threshold=threshold, dimensions=3)


# ---------------------------------------------------------------------------
# Identical vectors
# ---------------------------------------------------------------------------


class TestIdenticalVectors:
    """Identical vectors should have cosine sim = 1.0 and be kept."""

    @pytest.mark.asyncio()
    async def test_identical_vectors_kept(self) -> None:
        """Two markets with identical embeddings should survive filtering."""
        poly = _make_market(Venue.POLYMARKET, "p1")
        kalshi = _make_market(Venue.KALSHI, "k1")
        pairs = [(poly, kalshi, 5.0)]
        embeddings = {
            "polymarket:p1": [1.0, 0.0, 0.0],
            "kalshi:k1": [1.0, 0.0, 0.0],
        }
        result = await embedding_rerank(pairs, embeddings, _cfg())
        assert len(result) == 1
        assert result[0][2] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Orthogonal vectors
# ---------------------------------------------------------------------------


class TestOrthogonalVectors:
    """Orthogonal vectors should have cosine sim = 0.0 and be dropped."""

    @pytest.mark.asyncio()
    async def test_orthogonal_vectors_dropped(self) -> None:
        """Two orthogonal embeddings produce sim=0 which is below 0.60."""
        poly = _make_market(Venue.POLYMARKET, "p1")
        kalshi = _make_market(Venue.KALSHI, "k1")
        pairs = [(poly, kalshi, 5.0)]
        embeddings = {
            "polymarket:p1": [1.0, 0.0, 0.0],
            "kalshi:k1": [0.0, 1.0, 0.0],
        }
        result = await embedding_rerank(pairs, embeddings, _cfg())
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Similar vectors (cosine ~0.8)
# ---------------------------------------------------------------------------


class TestSimilarVectors:
    """Similar vectors with cosine ~0.8 should be kept (above 0.60)."""

    @pytest.mark.asyncio()
    async def test_similar_vectors_kept(self) -> None:
        """Vectors with cosine ~0.8 should survive the default 0.60 threshold."""
        poly = _make_market(Venue.POLYMARKET, "p1")
        kalshi = _make_market(Venue.KALSHI, "k1")
        pairs = [(poly, kalshi, 3.0)]
        # cos([1,1,0], [1,0.5,0]) = 1.5 / (sqrt(2) * sqrt(1.25)) ~ 0.949
        # Use vectors that give ~0.8: [1,1,0] vs [1,0,1]
        # cos = 1 / (sqrt(2)*sqrt(2)) = 0.5 -- too low
        # Use [3,4,0] and [4,3,0]: cos = 24/(5*5) = 0.96 -- too high
        # Use [1,0,0] and [0.8,0.6,0]: cos = 0.8/(1*1) = 0.8
        embeddings = {
            "polymarket:p1": [1.0, 0.0, 0.0],
            "kalshi:k1": [0.8, 0.6, 0.0],
        }
        result = await embedding_rerank(pairs, embeddings, _cfg())
        assert len(result) == 1
        assert result[0][2] == pytest.approx(0.8, abs=0.01)


# ---------------------------------------------------------------------------
# Dissimilar vectors (cosine ~0.3)
# ---------------------------------------------------------------------------


class TestDissimilarVectors:
    """Dissimilar vectors with cosine ~0.3 should be dropped."""

    @pytest.mark.asyncio()
    async def test_dissimilar_vectors_dropped(self) -> None:
        """Vectors with cosine ~0.3 should be filtered below the 0.60 threshold."""
        poly = _make_market(Venue.POLYMARKET, "p1")
        kalshi = _make_market(Venue.KALSHI, "k1")
        pairs = [(poly, kalshi, 2.0)]
        # cos([1,0,0], [0.3,0.95,0]) = 0.3 / (1 * ~1.0) ~ 0.3
        embeddings = {
            "polymarket:p1": [1.0, 0.0, 0.0],
            "kalshi:k1": [0.3, 0.95, 0.0],
        }
        result = await embedding_rerank(pairs, embeddings, _cfg())
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Missing embedding for one market
# ---------------------------------------------------------------------------


class TestMissingOneEmbedding:
    """Missing embedding for one market should keep the pair with BM25 score."""

    @pytest.mark.asyncio()
    async def test_missing_one_embedding_kept(self) -> None:
        """If one market has no embedding, pair is kept with original score."""
        poly = _make_market(Venue.POLYMARKET, "p1")
        kalshi = _make_market(Venue.KALSHI, "k1")
        pairs = [(poly, kalshi, 7.5)]
        embeddings = {"polymarket:p1": [1.0, 0.0, 0.0]}
        result = await embedding_rerank(pairs, embeddings, _cfg())
        assert len(result) == 1
        assert result[0][2] == pytest.approx(7.5)


# ---------------------------------------------------------------------------
# Missing embedding for both markets
# ---------------------------------------------------------------------------


class TestMissingBothEmbeddings:
    """Missing embeddings for both markets should keep the pair with BM25 score."""

    @pytest.mark.asyncio()
    async def test_missing_both_embeddings_kept(self) -> None:
        """If neither market has an embedding, pair is kept with original score."""
        poly = _make_market(Venue.POLYMARKET, "p1")
        kalshi = _make_market(Venue.KALSHI, "k1")
        pairs = [(poly, kalshi, 4.2)]
        embeddings: dict[str, list[float]] = {}
        result = await embedding_rerank(pairs, embeddings, _cfg())
        assert len(result) == 1
        assert result[0][2] == pytest.approx(4.2)


# ---------------------------------------------------------------------------
# Empty pairs list
# ---------------------------------------------------------------------------


class TestEmptyPairs:
    """Empty input should return empty output."""

    @pytest.mark.asyncio()
    async def test_empty_pairs_returns_empty(self) -> None:
        """An empty pairs list returns an empty result."""
        result = await embedding_rerank([], {}, _cfg())
        assert result == []


# ---------------------------------------------------------------------------
# All pairs below threshold
# ---------------------------------------------------------------------------


class TestAllBelowThreshold:
    """All pairs below threshold should return empty list."""

    @pytest.mark.asyncio()
    async def test_all_below_threshold_returns_empty(self) -> None:
        """When every pair is below the cosine threshold, result is empty."""
        p1 = _make_market(Venue.POLYMARKET, "p1")
        k1 = _make_market(Venue.KALSHI, "k1")
        p2 = _make_market(Venue.POLYMARKET, "p2")
        k2 = _make_market(Venue.KALSHI, "k2")
        pairs = [(p1, k1, 5.0), (p2, k2, 3.0)]
        embeddings = {
            "polymarket:p1": [1.0, 0.0, 0.0],
            "kalshi:k1": [0.0, 1.0, 0.0],
            "polymarket:p2": [0.0, 0.0, 1.0],
            "kalshi:k2": [0.0, 1.0, 0.0],
        }
        result = await embedding_rerank(pairs, embeddings, _cfg())
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Sorted by cosine similarity descending
# ---------------------------------------------------------------------------


class TestSortOrder:
    """Output must be sorted by cosine similarity descending."""

    @pytest.mark.asyncio()
    async def test_sorted_descending(self) -> None:
        """Pairs should be returned in descending cosine similarity order."""
        p1 = _make_market(Venue.POLYMARKET, "p1")
        k1 = _make_market(Venue.KALSHI, "k1")
        p2 = _make_market(Venue.POLYMARKET, "p2")
        k2 = _make_market(Venue.KALSHI, "k2")
        pairs = [(p1, k1, 1.0), (p2, k2, 2.0)]
        # p1-k1 cos = 0.8, p2-k2 cos ~ 1.0 (identical)
        embeddings = {
            "polymarket:p1": [1.0, 0.0, 0.0],
            "kalshi:k1": [0.8, 0.6, 0.0],
            "polymarket:p2": [0.0, 1.0, 0.0],
            "kalshi:k2": [0.0, 1.0, 0.0],
        }
        result = await embedding_rerank(pairs, embeddings, _cfg())
        assert len(result) == 2
        scores = [r[2] for r in result]
        assert scores[0] >= scores[1]
        assert scores[0] == pytest.approx(1.0)
        assert scores[1] == pytest.approx(0.8, abs=0.01)


# ---------------------------------------------------------------------------
# Custom threshold (0.9)
# ---------------------------------------------------------------------------


class TestCustomThreshold:
    """A stricter threshold should filter more aggressively."""

    @pytest.mark.asyncio()
    async def test_strict_threshold_filters_moderate_similarity(self) -> None:
        """With threshold=0.9, a pair at cosine=0.8 should be dropped."""
        poly = _make_market(Venue.POLYMARKET, "p1")
        kalshi = _make_market(Venue.KALSHI, "k1")
        poly2 = _make_market(Venue.POLYMARKET, "p2")
        kalshi2 = _make_market(Venue.KALSHI, "k2")
        pairs = [(poly, kalshi, 5.0), (poly2, kalshi2, 3.0)]
        embeddings = {
            # cos = 0.8 -- should be dropped at threshold 0.9
            "polymarket:p1": [1.0, 0.0, 0.0],
            "kalshi:k1": [0.8, 0.6, 0.0],
            # cos = 1.0 -- should survive
            "polymarket:p2": [0.0, 1.0, 0.0],
            "kalshi:k2": [0.0, 1.0, 0.0],
        }
        result = await embedding_rerank(pairs, embeddings, _cfg(threshold=0.9))
        assert len(result) == 1
        assert result[0][0].event_id == "p2"
        assert result[0][2] == pytest.approx(1.0)
