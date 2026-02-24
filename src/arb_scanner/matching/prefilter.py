"""BM25 pre-filter for candidate market pair selection.

Uses bm25s with BM25+ scoring to efficiently narrow down cross-venue
market pairs before expensive LLM semantic matching.
"""

from typing import Any

import bm25s  # type: ignore[import-untyped]
import numpy as np
import structlog

from arb_scanner.models.market import Market

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _build_corpus(kalshi_markets: list[Market]) -> list[str]:
    """Extract titles from Kalshi markets for BM25 indexing.

    Args:
        kalshi_markets: List of Kalshi Market objects.

    Returns:
        List of market title strings.
    """
    return [m.title for m in kalshi_markets]


def _build_index(corpus_texts: list[str]) -> Any:
    """Build a BM25+ index from corpus texts.

    Args:
        corpus_texts: List of document strings to index.

    Returns:
        A fitted BM25 retriever instance.
    """
    retriever = bm25s.BM25(method="bm25+")
    tokenized = bm25s.tokenize(corpus_texts, stopwords="en", show_progress=False)
    retriever.index(tokenized, show_progress=False)
    return retriever


def _collect_pairs(
    poly_markets: list[Market],
    kalshi_markets: list[Market],
    results: np.ndarray[Any, np.dtype[np.str_]],
    scores: np.ndarray[Any, np.dtype[np.floating[Any]]],
    corpus_texts: list[str],
) -> list[tuple[Market, Market, float]]:
    """Collect scored candidate pairs from BM25 retrieval results.

    Args:
        poly_markets: List of Polymarket Market objects (query sources).
        kalshi_markets: List of Kalshi Market objects (corpus).
        results: BM25 retrieval results array of shape (n_queries, k).
        scores: BM25 score array of shape (n_queries, k).
        corpus_texts: Original corpus titles for index lookup.

    Returns:
        List of (poly_market, kalshi_market, bm25_score) tuples with score > 0.
    """
    title_to_market = {m.title: m for m in kalshi_markets}
    pairs: list[tuple[Market, Market, float]] = []
    for qi in range(len(poly_markets)):
        for ki in range(results.shape[1]):
            score = float(scores[qi][ki])
            if score <= 0.0:
                continue
            title = str(results[qi][ki])
            kalshi_market = title_to_market.get(title)
            if kalshi_market is not None:
                pairs.append((poly_markets[qi], kalshi_market, score))
    return pairs


async def prefilter_candidates(
    poly_markets: list[Market],
    kalshi_markets: list[Market],
    top_k: int = 20,
) -> list[tuple[Market, Market, float]]:
    """Pre-filter candidate market pairs using BM25+ text similarity.

    Builds a BM25+ index from Kalshi market titles and queries each
    Polymarket title against it, returning scored candidate pairs
    above zero for downstream semantic evaluation.

    Args:
        poly_markets: Polymarket markets to match from.
        kalshi_markets: Kalshi markets to match against.
        top_k: Maximum candidates per Polymarket query.

    Returns:
        List of (poly_market, kalshi_market, bm25_score) tuples sorted
        by descending score.
    """
    if not poly_markets or not kalshi_markets:
        logger.info("prefilter.skip", reason="empty_input")
        return []

    corpus_texts = _build_corpus(kalshi_markets)
    retriever = _build_index(corpus_texts)

    query_texts = [m.title for m in poly_markets]
    query_tokens = bm25s.tokenize(query_texts, stopwords="en", show_progress=False)
    effective_k = min(top_k, len(kalshi_markets))
    results, scores = retriever.retrieve(
        query_tokens, corpus=corpus_texts, k=effective_k, show_progress=False
    )

    pairs = _collect_pairs(poly_markets, kalshi_markets, results, scores, corpus_texts)
    pairs.sort(key=lambda t: t[2], reverse=True)

    _log_summary(poly_markets, kalshi_markets, pairs)
    return pairs


def _log_summary(
    poly_markets: list[Market],
    kalshi_markets: list[Market],
    pairs: list[tuple[Market, Market, float]],
) -> None:
    """Log a summary of prefilter results.

    Args:
        poly_markets: Input Polymarket markets.
        kalshi_markets: Input Kalshi markets.
        pairs: Resulting candidate pairs.
    """
    poly_ids = {p[0].event_id for p in pairs}
    logger.info(
        "prefilter.complete",
        poly_count=len(poly_markets),
        kalshi_count=len(kalshi_markets),
        candidate_pairs=len(pairs),
        unique_poly_matched=len(poly_ids),
    )
