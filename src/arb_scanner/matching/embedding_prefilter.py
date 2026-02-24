"""Embedding-based re-ranking for BM25 candidate pairs.

Computes cosine similarity between market embedding vectors and
filters pairs below the configured threshold, tightening precision
after the BM25 recall stage.
"""

import numpy as np
import structlog

from arb_scanner.matching.embedding import _market_key
from arb_scanner.models.config import EmbeddingConfig
from arb_scanner.models.market import Market

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First embedding vector.
        b: Second embedding vector.

    Returns:
        Cosine similarity in [-1.0, 1.0].
    """
    va = np.array(a)
    vb = np.array(b)
    dot = float(np.dot(va, vb))
    norm_a = float(np.linalg.norm(va))
    norm_b = float(np.linalg.norm(vb))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def embedding_rerank(
    pairs: list[tuple[Market, Market, float]],
    embeddings: dict[str, list[float]],
    config: EmbeddingConfig,
) -> list[tuple[Market, Market, float]]:
    """Re-rank candidate pairs by cosine similarity of their embeddings.

    For each pair, looks up both markets' embeddings. If either is
    missing the pair is kept with its original BM25 score. Otherwise
    pairs below ``config.cosine_threshold`` are dropped and the
    remaining pairs are sorted by cosine similarity descending.

    Args:
        pairs: BM25 candidate pairs ``(poly, kalshi, bm25_score)``.
        embeddings: Mapping of ``"venue:event_id"`` to float vectors.
        config: Embedding configuration with cosine threshold.

    Returns:
        Filtered and re-scored pairs sorted by cosine similarity descending.
    """
    if not pairs:
        return []

    kept: list[tuple[Market, Market, float]] = []
    dropped = 0

    for poly, kalshi, bm25_score in pairs:
        emb_poly = embeddings.get(_market_key(poly))
        emb_kalshi = embeddings.get(_market_key(kalshi))

        if emb_poly is None or emb_kalshi is None:
            kept.append((poly, kalshi, bm25_score))
            continue

        sim = _cosine_similarity(emb_poly, emb_kalshi)
        if sim < config.cosine_threshold:
            dropped += 1
            continue

        kept.append((poly, kalshi, sim))

    kept.sort(key=lambda t: t[2], reverse=True)

    logger.info(
        "embedding.rerank",
        pairs_in=len(pairs),
        pairs_out=len(kept),
        dropped=dropped,
    )
    return kept
