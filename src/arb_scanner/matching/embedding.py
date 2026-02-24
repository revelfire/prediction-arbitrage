"""Voyage AI embedding client for vector pre-filtering.

Generates text embeddings via the Voyage AI API, batching requests
to stay within per-call limits. Used to pre-filter market pairs by
cosine similarity before expensive LLM semantic matching.
"""

from typing import Any

import httpx
import structlog

from arb_scanner.models.config import EmbeddingConfig
from arb_scanner.models.market import Market

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

_VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
_MAX_BATCH_SIZE = 128


def _market_key(market: Market) -> str:
    """Build a unique string key for a market.

    Args:
        market: The market to key.

    Returns:
        A string in ``"venue:event_id"`` format.
    """
    return f"{market.venue.value}:{market.event_id}"


def _market_text(market: Market) -> str:
    """Build the text representation used for embedding.

    Args:
        market: The market to represent.

    Returns:
        Combined title and resolution criteria.
    """
    return f"{market.title}. {market.resolution_criteria}"


async def _call_voyage(
    texts: list[str],
    config: EmbeddingConfig,
    client: httpx.AsyncClient,
) -> list[list[float]]:
    """Send a single batch request to the Voyage AI API.

    Args:
        texts: Input strings to embed (max 128).
        config: Embedding configuration with model and API key.
        client: Shared async HTTP client.

    Returns:
        List of embedding vectors in input order.

    Raises:
        httpx.HTTPStatusError: On non-200 responses.
    """
    payload: dict[str, Any] = {
        "model": config.model,
        "input": texts,
        "output_dimension": config.dimensions,
    }
    headers = {"Authorization": f"Bearer {config.api_key}"}
    resp = await client.post(_VOYAGE_API_URL, json=payload, headers=headers)
    resp.raise_for_status()
    body = resp.json()
    items = sorted(body["data"], key=lambda d: d["index"])
    return [item["embedding"] for item in items]


async def generate_embeddings(
    markets: list[Market],
    config: EmbeddingConfig,
) -> dict[str, list[float]]:
    """Generate embeddings for a list of markets via Voyage AI.

    Batches requests to respect API limits. On any error the function
    logs a warning and returns an empty dict so callers can fall back
    to non-vector matching.

    Args:
        markets: Markets to embed.
        config: Embedding configuration.

    Returns:
        Dict mapping ``"venue:event_id"`` keys to float vectors.
    """
    if not config.enabled:
        logger.info("embedding.skip", reason="disabled")
        return {}

    if not config.api_key:
        logger.info("embedding.skip", reason="empty_api_key")
        return {}

    if not markets:
        return {}

    texts = [_market_text(m) for m in markets]
    keys = [_market_key(m) for m in markets]
    result: dict[str, list[float]] = {}

    try:
        async with httpx.AsyncClient() as client:
            for start in range(0, len(texts), _MAX_BATCH_SIZE):
                batch = texts[start : start + _MAX_BATCH_SIZE]
                vectors = await _call_voyage(batch, config, client)
                for i, vec in enumerate(vectors):
                    result[keys[start + i]] = vec
    except Exception:
        logger.warning(
            "embedding.error",
            market_count=len(markets),
            exc_info=True,
        )
        return {}

    logger.info(
        "embedding.complete",
        market_count=len(markets),
        vectors_returned=len(result),
    )
    return result
