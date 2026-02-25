"""Embedding generation for vector pre-filtering.

Supports two providers:
- ``local`` (default): ONNX-based model via fastembed (no API key needed).
- ``voyage``: Voyage AI HTTP API (requires api_key).

Both produce ``dict[str, list[float]]`` keyed by ``"venue:event_id"``.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from arb_scanner.models.config import EmbeddingConfig
from arb_scanner.models.market import Market

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

_VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
_VOYAGE_MAX_BATCH = 128

# Lazy-loaded fastembed model singleton.
_local_model: Any = None
_local_model_name: str | None = None


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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def generate_embeddings(
    markets: list[Market],
    config: EmbeddingConfig,
) -> dict[str, list[float]]:
    """Generate embeddings for a list of markets.

    Dispatches to the configured provider (local or voyage).
    On any error, logs a warning and returns an empty dict so
    callers can fall back to non-vector matching.

    Args:
        markets: Markets to embed.
        config: Embedding configuration.

    Returns:
        Dict mapping ``"venue:event_id"`` keys to float vectors.
    """
    if not config.enabled:
        logger.info("embedding.skip", reason="disabled")
        return {}

    if not markets:
        return {}

    if config.provider == "local":
        return await _generate_local(markets, config)
    if config.provider == "voyage":
        return await _generate_voyage(markets, config)

    msg = f"Unknown embedding provider: {config.provider!r}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Local provider (fastembed / ONNX)
# ---------------------------------------------------------------------------


def _get_local_model(model_name: str) -> Any:
    """Lazy-load the fastembed TextEmbedding model (singleton).

    Args:
        model_name: HuggingFace model identifier.

    Returns:
        A fastembed TextEmbedding instance.
    """
    global _local_model, _local_model_name  # noqa: PLW0603

    if _local_model is not None and _local_model_name == model_name:
        return _local_model

    from fastembed import TextEmbedding

    logger.info("embedding.loading_model", model=model_name)
    _local_model = TextEmbedding(model_name=model_name)
    _local_model_name = model_name
    return _local_model


async def _generate_local(
    markets: list[Market],
    config: EmbeddingConfig,
) -> dict[str, list[float]]:
    """Generate embeddings using a local ONNX model via fastembed.

    Args:
        markets: Markets to embed.
        config: Embedding configuration.

    Returns:
        Dict mapping ``"venue:event_id"`` keys to float vectors.
    """
    texts = [_market_text(m) for m in markets]
    keys = [_market_key(m) for m in markets]

    try:
        model = _get_local_model(config.model)
        vectors = list(model.embed(texts))
        result: dict[str, list[float]] = {}
        for key, vec in zip(keys, vectors):
            result[key] = vec.tolist()
    except Exception:
        logger.warning(
            "embedding.local_error",
            market_count=len(markets),
            exc_info=True,
        )
        return {}

    logger.info(
        "embedding.complete",
        provider="local",
        market_count=len(markets),
        vectors_returned=len(result),
    )
    return result


# ---------------------------------------------------------------------------
# Voyage AI provider (HTTP API)
# ---------------------------------------------------------------------------


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


async def _generate_voyage(
    markets: list[Market],
    config: EmbeddingConfig,
) -> dict[str, list[float]]:
    """Generate embeddings via the Voyage AI HTTP API.

    Args:
        markets: Markets to embed.
        config: Embedding configuration.

    Returns:
        Dict mapping ``"venue:event_id"`` keys to float vectors.
    """
    if not config.api_key:
        logger.info("embedding.skip", reason="empty_api_key")
        return {}

    texts = [_market_text(m) for m in markets]
    keys = [_market_key(m) for m in markets]
    result: dict[str, list[float]] = {}

    try:
        async with httpx.AsyncClient() as client:
            for start in range(0, len(texts), _VOYAGE_MAX_BATCH):
                batch = texts[start : start + _VOYAGE_MAX_BATCH]
                vectors = await _call_voyage(batch, config, client)
                for i, vec in enumerate(vectors):
                    result[keys[start + i]] = vec
    except Exception:
        logger.warning(
            "embedding.voyage_error",
            market_count=len(markets),
            exc_info=True,
        )
        return {}

    logger.info(
        "embedding.complete",
        provider="voyage",
        market_count=len(markets),
        vectors_returned=len(result),
    )
    return result
