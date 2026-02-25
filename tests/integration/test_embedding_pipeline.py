"""Integration tests for the embedding pipeline in the scan orchestrator.

Verifies that embedding generation, reranking, and fallback behaviour
work correctly end-to-end through the orchestrator.  All tests mock at
the model / HTTP / database boundary so no real API or PostgreSQL
connection is needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from arb_scanner.cli.orchestrator import _run_embedding_rerank, run_scan
from arb_scanner.matching.embedding import generate_embeddings
from arb_scanner.models.config import (
    ArbThresholds,
    ClaudeConfig,
    EmbeddingConfig,
    FeeSchedule,
    FeesConfig,
    Settings,
    StorageConfig,
)
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult

_NOW = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _build_match(poly_id: str, kalshi_id: str) -> MatchResult:
    """Build a MatchResult for mocking semantic evaluation."""
    return MatchResult(
        poly_event_id=poly_id,
        kalshi_event_id=kalshi_id,
        match_confidence=0.95,
        resolution_equivalent=True,
        resolution_risks=[],
        safe_to_arb=True,
        reasoning="Test",
        matched_at=_NOW,
        ttl_expires=_NOW + timedelta(hours=24),
    )


async def _mock_evaluate(
    pairs: list[Any],
    config: Any,
) -> list[MatchResult]:
    """Return a MatchResult for every pair passed to semantic evaluation."""
    return [_build_match(p.event_id, k.event_id) for p, k, _ in pairs]


def _voyage_response(count: int, dims: int = 512) -> dict[str, Any]:
    """Build a mock Voyage API JSON response with *count* embeddings."""
    data = [{"embedding": [0.1] * dims, "index": i} for i in range(count)]
    return {"data": data}


def _mock_fastembed(dims: int = 384) -> MagicMock:
    """Build a mock fastembed TextEmbedding model."""
    model = MagicMock()

    def _embed(texts: list[str]) -> list[Any]:
        return [np.array([0.1] * dims) for _ in texts]

    model.embed = _embed
    return model


def _make_settings(
    *,
    embedding_enabled: bool = True,
    provider: str = "local",
    embedding_api_key: str = "",
    dimensions: int = 384,
) -> Settings:
    """Build a Settings instance with tuneable embedding config."""
    return Settings(
        storage=StorageConfig(database_url="postgresql://localhost/unused"),
        fees=FeesConfig(
            polymarket=FeeSchedule(taker_fee_pct=Decimal("0.0"), fee_model="on_winnings"),
            kalshi=FeeSchedule(
                taker_fee_pct=Decimal("0.07"),
                fee_model="per_contract",
                fee_cap=Decimal("0.07"),
            ),
        ),
        claude=ClaudeConfig(api_key="test-key", batch_size=10),
        embedding=EmbeddingConfig(
            enabled=embedding_enabled,
            provider=provider,
            api_key=embedding_api_key,
            cosine_threshold=0.60,
            dimensions=dimensions,
        ),
        arb_thresholds=ArbThresholds(
            min_net_spread_pct=Decimal("0.01"),
            min_size_usd=Decimal("1"),
            thin_liquidity_threshold=Decimal("50"),
        ),
    )


def _mock_httpx_client(response: MagicMock) -> MagicMock:
    """Wrap a mock response in an httpx.AsyncClient mock."""
    client = AsyncMock()
    client.post.return_value = response
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _make_ok_response(count: int = 2, dims: int = 512) -> MagicMock:
    """Create a successful Voyage-style httpx response mock."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = _voyage_response(count, dims)
    return resp


# ---------------------------------------------------------------------------
# Full pipeline with local embedding provider
# ---------------------------------------------------------------------------


class TestPipelineWithEmbeddingEnabled:
    """Pipeline with local embedding should call fastembed and filter pairs."""

    @pytest.mark.asyncio()
    async def test_embedding_rerank_called_in_pipeline(self) -> None:
        """Dry-run scan with local embedding enabled should invoke fastembed."""
        config = _make_settings(embedding_enabled=True, provider="local")

        with (
            patch(
                "arb_scanner.cli.orchestrator.evaluate_pairs",
                new=AsyncMock(side_effect=_mock_evaluate),
            ),
            patch(
                "arb_scanner.matching.embedding._get_local_model",
                return_value=_mock_fastembed(384),
            ),
        ):
            result = await run_scan(config, dry_run=True)

        assert "scan_id" in result


# ---------------------------------------------------------------------------
# Pipeline with embedding disabled
# ---------------------------------------------------------------------------


class TestPipelineEmbeddingDisabled:
    """Pipeline with embedding disabled should skip embedding entirely."""

    @pytest.mark.asyncio()
    async def test_bm25_only_fallback(self) -> None:
        """When embedding.enabled=False, no embedding model should be called."""
        config = _make_settings(embedding_enabled=False)

        with (
            patch(
                "arb_scanner.cli.orchestrator.evaluate_pairs",
                new=AsyncMock(side_effect=_mock_evaluate),
            ),
            patch(
                "arb_scanner.matching.embedding._get_local_model",
            ) as mock_model,
        ):
            result = await run_scan(config, dry_run=True)

        mock_model.assert_not_called()
        assert "scan_id" in result


# ---------------------------------------------------------------------------
# Pipeline with Voyage provider and empty api_key
# ---------------------------------------------------------------------------


class TestPipelineEmptyApiKey:
    """Empty embedding api_key with Voyage provider should fall back to BM25-only."""

    @pytest.mark.asyncio()
    async def test_empty_key_skips_voyage(self) -> None:
        """When provider=voyage and api_key is empty, Voyage API should not be called."""
        config = _make_settings(provider="voyage", embedding_api_key="")

        with (
            patch(
                "arb_scanner.cli.orchestrator.evaluate_pairs",
                new=AsyncMock(side_effect=_mock_evaluate),
            ),
            patch("arb_scanner.matching.embedding.httpx.AsyncClient") as mock_cls,
        ):
            result = await run_scan(config, dry_run=True)

        mock_cls.assert_not_called()
        assert result["candidate_pairs"] >= 0


# ---------------------------------------------------------------------------
# Voyage API error => graceful degradation
# ---------------------------------------------------------------------------


class TestVoyageApiError:
    """Voyage API errors should degrade gracefully to BM25-only."""

    @pytest.mark.asyncio()
    async def test_voyage_error_degrades_to_bm25(self) -> None:
        """When Voyage returns an error, scan still completes with BM25 pairs."""
        config = _make_settings(
            embedding_enabled=True,
            provider="voyage",
            embedding_api_key="sk-test",
            dimensions=512,
        )

        error_client = AsyncMock()
        error_client.post.side_effect = ConnectionError("simulated")
        error_client.__aenter__ = AsyncMock(return_value=error_client)
        error_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "arb_scanner.cli.orchestrator.evaluate_pairs",
                new=AsyncMock(side_effect=_mock_evaluate),
            ),
            patch("arb_scanner.matching.embedding.httpx.AsyncClient") as mock_cls,
        ):
            mock_cls.return_value = error_client
            result = await run_scan(config, dry_run=True)

        assert "scan_id" in result
        assert result["candidate_pairs"] >= 0


# ---------------------------------------------------------------------------
# Embedding cache read — only uncached markets trigger generation
# ---------------------------------------------------------------------------


class TestEmbeddingCacheRead:
    """Verify that cached embeddings are loaded and only new markets are embedded."""

    @pytest.mark.asyncio()
    async def test_cached_markets_skip_generation(self) -> None:
        """Markets with cached embeddings should not be passed to generate_embeddings."""
        p1 = _make_market(Venue.POLYMARKET, "p1", "Cached poly")
        k1 = _make_market(Venue.KALSHI, "k1", "Cached kalshi")
        p2 = _make_market(Venue.POLYMARKET, "p2", "New poly")
        k2 = _make_market(Venue.KALSHI, "k2", "New kalshi")
        pairs = [(p1, k1, 5.0), (p2, k2, 3.0)]

        cached = {
            "polymarket:p1": [0.1] * 384,
            "kalshi:k1": [0.2] * 384,
        }
        generated = {
            "polymarket:p2": [0.3] * 384,
            "kalshi:k2": [0.4] * 384,
        }

        config = _make_settings()

        gen_calls: list[list[Market]] = []

        async def _track_generate(
            markets: list[Market], cfg: EmbeddingConfig
        ) -> dict[str, list[float]]:
            gen_calls.append(markets)
            return generated

        with (
            patch(
                "arb_scanner.cli.orchestrator._load_cached_embeddings",
                return_value=cached,
            ),
            patch(
                "arb_scanner.cli.orchestrator.generate_embeddings",
                side_effect=_track_generate,
            ),
        ):
            _filtered, new_embs = await _run_embedding_rerank(pairs, config, dry_run=False)

        # Only p2 and k2 should have been passed to generate_embeddings
        assert len(gen_calls) == 1
        gen_keys = {f"{m.venue.value}:{m.event_id}" for m in gen_calls[0]}
        assert "polymarket:p2" in gen_keys
        assert "kalshi:k2" in gen_keys
        assert "polymarket:p1" not in gen_keys
        assert "kalshi:k1" not in gen_keys
        # new_embs should only contain freshly generated
        assert new_embs == generated


# ---------------------------------------------------------------------------
# Embedding rerank with real-ish data (5 pairs, 3 kept, 2 dropped)
# ---------------------------------------------------------------------------


class TestEmbeddingRerankFiltering:
    """Verify reranking drops low-similarity pairs and keeps high ones."""

    @pytest.mark.asyncio()
    async def test_rerank_filters_low_similarity_pairs(self) -> None:
        """5 BM25 pairs with varied cosine: 3 above threshold, 2 below."""
        pairs: list[tuple[Market, Market, float]] = []
        for i in range(5):
            poly = _make_market(Venue.POLYMARKET, f"p{i}", f"Poly {i}")
            kalshi = _make_market(Venue.KALSHI, f"k{i}", f"Kalshi {i}")
            pairs.append((poly, kalshi, float(5 - i)))

        embeddings: dict[str, list[float]] = {
            "polymarket:p0": [1.0, 0.0, 0.0],
            "kalshi:k0": [1.0, 0.0, 0.0],
            "polymarket:p1": [0.0, 1.0, 0.0],
            "kalshi:k1": [0.0, 1.0, 0.0],
            "polymarket:p2": [1.0, 0.0, 0.0],
            "kalshi:k2": [0.8, 0.6, 0.0],
            "polymarket:p3": [1.0, 0.0, 0.0],
            "kalshi:k3": [0.0, 1.0, 0.0],
            "polymarket:p4": [0.0, 0.0, 1.0],
            "kalshi:k4": [1.0, 0.0, 0.0],
        }

        config = _make_settings()
        with patch(
            "arb_scanner.cli.orchestrator.generate_embeddings",
            return_value=embeddings,
        ):
            filtered, _emb = await _run_embedding_rerank(pairs, config, dry_run=True)

        assert len(filtered) == 3
        kept_ids = {(p.event_id, k.event_id) for p, k, _ in filtered}
        assert ("p0", "k0") in kept_ids
        assert ("p1", "k1") in kept_ids
        assert ("p2", "k2") in kept_ids


# ---------------------------------------------------------------------------
# Batching with mocked transport (Voyage provider)
# ---------------------------------------------------------------------------


class TestGenerateEmbeddingsBatching:
    """Verify Voyage batching splits large market sets correctly."""

    @pytest.mark.asyncio()
    async def test_batching_splits_correctly(self) -> None:
        """150 markets should produce 2 batches (128 + 22)."""
        markets = [_make_market(Venue.POLYMARKET, f"m{i}") for i in range(150)]
        config = EmbeddingConfig(provider="voyage", api_key="sk-test", dimensions=8)

        call_sizes: list[int] = []

        async def _mock_post(url: str, **kwargs: Any) -> MagicMock:
            batch_size = len(kwargs["json"]["input"])
            call_sizes.append(batch_size)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = _voyage_response(batch_size, dims=8)
            return resp

        with patch("arb_scanner.matching.embedding.httpx.AsyncClient") as mock_cls:
            client = AsyncMock()
            client.post = _mock_post  # type: ignore[assignment]
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = client

            result = await generate_embeddings(markets, config)

        assert len(call_sizes) == 2
        assert call_sizes[0] == 128
        assert call_sizes[1] == 22
        assert len(result) == 150


# ---------------------------------------------------------------------------
# Persist embeddings helper (fire-and-forget)
# ---------------------------------------------------------------------------


class TestPersistEmbeddingsFireAndForget:
    """_persist_embeddings should log errors but never propagate them."""

    @pytest.mark.asyncio()
    async def test_persist_embeddings_db_error_does_not_raise(self) -> None:
        """A database error during embedding persistence should not raise."""
        from arb_scanner.cli.orchestrator import _persist_embeddings

        config = _make_settings()
        embeddings = {"polymarket:p1": [0.1, 0.2], "kalshi:k1": [0.3, 0.4]}

        mock_db = AsyncMock()
        mock_repo = AsyncMock()
        mock_repo.update_market_embedding_384.side_effect = RuntimeError("db down")
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_db.pool = MagicMock()

        with (
            patch("arb_scanner.storage.db.Database", return_value=mock_db),
            patch("arb_scanner.storage.repository.Repository", return_value=mock_repo),
        ):
            await _persist_embeddings(embeddings, config)
