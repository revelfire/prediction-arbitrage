"""Demand-driven Kalshi market selection using Polymarket titles as signal.

Ranks Kalshi markets by BM25 relevance to the Polymarket title corpus,
ensuring the candidate pool fed to the matching pipeline contains markets
most likely to have cross-venue equivalents.
"""

from __future__ import annotations

from typing import Any

import bm25s  # type: ignore[import-untyped]
import numpy as np
import structlog

from arb_scanner.models.market import Market

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="matching.demand_filter",
)

_HITS_PER_QUERY = 10


async def rank_events(
    poly_markets: list[Market],
    events: list[dict[str, str]],
    max_events: int = 200,
) -> list[str]:
    """Rank Kalshi events by Poly title relevance, return top event tickers.

    Args:
        poly_markets: Polymarket demand signal.
        events: Kalshi events with ``event_ticker`` and ``title`` keys.
        max_events: Maximum number of event tickers to return.

    Returns:
        Event tickers for the most Poly-relevant Kalshi events.
    """
    if not poly_markets or not events:
        return [e["event_ticker"] for e in events[:max_events]]

    corpus = [e["title"] for e in events]
    queries = [m.title for m in poly_markets]
    best = _score_relevance(corpus, queries)

    ranked = sorted(events, key=lambda e: best.get(e["title"], 0.0), reverse=True)
    selected = ranked[:max_events]
    n_relevant = sum(1 for e in selected if best.get(e["title"], 0.0) > 0)
    logger.info(
        "demand_filter.events",
        events_in=len(events),
        events_out=len(selected),
        with_relevance=n_relevant,
    )
    return [e["event_ticker"] for e in selected]


async def demand_rank(
    poly_markets: list[Market],
    kalshi_markets: list[Market],
    max_markets: int = 500,
) -> list[Market]:
    """Select the most Poly-relevant Kalshi markets via BM25 scoring.

    For each Polymarket title, retrieves the top-k most similar Kalshi
    titles.  Each Kalshi market's best score across all Poly queries
    is used to rank; the top *max_markets* are returned.

    Args:
        poly_markets: Polymarket demand signal.
        kalshi_markets: Full pool of Kalshi candidates.
        max_markets: Number of Kalshi markets to keep.

    Returns:
        Kalshi markets ranked by demand relevance, capped at *max_markets*.
    """
    if not poly_markets or not kalshi_markets:
        return kalshi_markets[:max_markets] if max_markets else kalshi_markets

    corpus = [m.title for m in kalshi_markets]
    queries = [m.title for m in poly_markets]
    best = _score_relevance(corpus, queries)

    ranked = sorted(
        kalshi_markets,
        key=lambda m: best.get(m.title, 0.0),
        reverse=True,
    )
    selected = ranked[:max_markets] if max_markets else ranked
    n_relevant = sum(1 for m in selected if best.get(m.title, 0.0) > 0)
    logger.info(
        "demand_filter.complete",
        kalshi_in=len(kalshi_markets),
        kalshi_out=len(selected),
        with_relevance=n_relevant,
    )
    return selected


def _score_relevance(
    corpus: list[str],
    queries: list[str],
) -> dict[str, float]:
    """Compute best BM25 score for each corpus title across all queries.

    Args:
        corpus: Kalshi market titles.
        queries: Polymarket titles used as search signal.

    Returns:
        Mapping of corpus title to best BM25 score from any query.
    """
    retriever: Any = bm25s.BM25(method="bm25+")
    tokenized = bm25s.tokenize(corpus, stopwords="en", show_progress=False)
    retriever.index(tokenized, show_progress=False)

    query_tokens = bm25s.tokenize(queries, stopwords="en", show_progress=False)
    per_k = min(_HITS_PER_QUERY, len(corpus))
    results: np.ndarray[Any, np.dtype[np.str_]]
    scores: np.ndarray[Any, np.dtype[np.floating[Any]]]
    results, scores = retriever.retrieve(
        query_tokens,
        corpus=corpus,
        k=per_k,
        show_progress=False,
    )

    best: dict[str, float] = {}
    for qi in range(len(queries)):
        for ki in range(results.shape[1]):
            title = str(results[qi][ki])
            score = float(scores[qi][ki])
            if score > best.get(title, 0.0):
                best[title] = score
    return best
