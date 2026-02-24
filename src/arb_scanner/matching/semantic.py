"""Claude-powered semantic matching for cross-venue market pairs.

Evaluates candidate pairs from the BM25 pre-filter using Claude to
determine contract equivalence, resolution risks, and arb safety.
"""

import json
from datetime import datetime, timedelta, timezone

from anthropic import AsyncAnthropic
import structlog

from arb_scanner.models.config import ClaudeConfig
from arb_scanner.models.market import Market
from arb_scanner.models.matching import MatchResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

_SYSTEM_PROMPT = """You are an expert prediction market analyst. Your task is to evaluate
whether pairs of contracts from different venues (Polymarket and Kalshi) refer to the
SAME underlying event and would resolve identically.

For each pair, return a JSON object with these fields:
- poly_event_id (str): The Polymarket event ID
- kalshi_event_id (str): The Kalshi event ID
- match_confidence (float 0-1): How confident you are they match
- resolution_equivalent (bool): Whether they will resolve the same way
- resolution_risks (list[str]): Risks that could cause different resolution
- safe_to_arb (bool): Whether it is safe to arbitrage (must be false if not equivalent)
- reasoning (str): Brief explanation of your assessment

CRITICAL DISTINCTIONS:
- "Equivalent" means IDENTICAL resolution criteria, not merely similar topics
- Watch for different time horizons, thresholds, or measurement sources
- Watch for "by end of" vs "at any point during" wording differences
- If resolution criteria differ in ANY material way, mark resolution_equivalent=false

Return a JSON array of objects, one per pair. Return ONLY valid JSON, no markdown."""


def _format_pair(poly: Market, kalshi: Market, bm25_score: float) -> str:
    """Format a market pair for the Claude prompt.

    Args:
        poly: Polymarket market.
        kalshi: Kalshi market.
        bm25_score: Pre-filter similarity score.

    Returns:
        Formatted string describing the pair.
    """
    return (
        f"--- Pair ---\n"
        f"Polymarket ID: {poly.event_id}\n"
        f"Polymarket Title: {poly.title}\n"
        f"Polymarket Resolution: {poly.resolution_criteria}\n"
        f"Kalshi ID: {kalshi.event_id}\n"
        f"Kalshi Title: {kalshi.title}\n"
        f"Kalshi Resolution: {kalshi.resolution_criteria}\n"
        f"BM25 Score: {bm25_score:.4f}\n"
    )


def _build_user_prompt(pairs: list[tuple[Market, Market, float]]) -> str:
    """Build the user prompt containing all pairs for evaluation.

    Args:
        pairs: List of (poly_market, kalshi_market, bm25_score) tuples.

    Returns:
        Formatted user prompt string.
    """
    sections = [_format_pair(p, k, s) for p, k, s in pairs]
    count = len(pairs)
    header = f"Evaluate these {count} prediction market pair(s):\n\n"
    return header + "\n".join(sections)


def _parse_match_results(
    raw_text: str,
    pairs: list[tuple[Market, Market, float]],
    ttl_hours: int,
) -> list[MatchResult]:
    """Parse Claude's JSON response into MatchResult models.

    Args:
        raw_text: Raw text response from Claude.
        pairs: Original pairs for fallback ID extraction.
        ttl_hours: Cache TTL in hours for setting expiry timestamps.

    Returns:
        List of parsed MatchResult objects.
    """
    now = datetime.now(tz=timezone.utc)
    expires = now + timedelta(hours=ttl_hours)
    try:
        data = json.loads(raw_text)
        items: list[object] = data if isinstance(data, list) else [data]
        return [_dict_to_match_result(d, now, expires) for d in items if isinstance(d, dict)]
    except (json.JSONDecodeError, KeyError, ValueError):
        logger.warning("semantic.parse_failed", raw_text=raw_text[:200])
        return [_fallback_result(p, k, now, expires) for p, k, _ in pairs]


def _dict_to_match_result(d: dict[str, object], now: datetime, expires: datetime) -> MatchResult:
    """Convert a parsed dict to a MatchResult.

    Args:
        d: Dictionary parsed from Claude's JSON response.
        now: Current UTC timestamp.
        expires: TTL expiry timestamp.

    Returns:
        A validated MatchResult model.
    """
    risks = d.get("resolution_risks", [])
    return MatchResult(
        poly_event_id=str(d["poly_event_id"]),
        kalshi_event_id=str(d["kalshi_event_id"]),
        match_confidence=float(d.get("match_confidence", 0.0)),  # type: ignore[arg-type]
        resolution_equivalent=bool(d.get("resolution_equivalent", False)),
        resolution_risks=risks if isinstance(risks, list) else [],
        safe_to_arb=bool(d.get("safe_to_arb", False)),
        reasoning=str(d.get("reasoning", "")),
        matched_at=now,
        ttl_expires=expires,
    )


def _fallback_result(poly: Market, kalshi: Market, now: datetime, expires: datetime) -> MatchResult:
    """Create a safe fallback MatchResult when parsing fails.

    Args:
        poly: Polymarket market.
        kalshi: Kalshi market.
        now: Current UTC timestamp.
        expires: TTL expiry timestamp.

    Returns:
        A conservative MatchResult with safe_to_arb=False.
    """
    return MatchResult(
        poly_event_id=poly.event_id,
        kalshi_event_id=kalshi.event_id,
        match_confidence=0.0,
        resolution_equivalent=False,
        resolution_risks=["LLM response parsing failed"],
        safe_to_arb=False,
        reasoning="Fallback: could not parse LLM response",
        matched_at=now,
        ttl_expires=expires,
    )


async def _call_claude(
    client: AsyncAnthropic,
    model: str,
    batch: list[tuple[Market, Market, float]],
) -> str:
    """Send a batch of pairs to Claude for evaluation.

    Args:
        client: Async Anthropic API client.
        model: Model identifier string.
        batch: List of (poly_market, kalshi_market, score) tuples.

    Returns:
        Raw text response from Claude.
    """
    user_prompt = _build_user_prompt(batch)
    message = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text  # type: ignore[union-attr]


async def evaluate_pairs(
    pairs: list[tuple[Market, Market, float]],
    config: ClaudeConfig,
) -> list[MatchResult]:
    """Evaluate candidate market pairs using Claude semantic matching.

    Batches pairs according to config.batch_size and calls Claude to
    assess match confidence, resolution equivalence, and arb safety.

    Args:
        pairs: Pre-filtered candidate pairs with BM25 scores.
        config: Claude API configuration including model and batch size.

    Returns:
        List of MatchResult objects, one per input pair.
    """
    if not pairs:
        return []

    client = AsyncAnthropic(api_key=config.api_key)
    all_results: list[MatchResult] = []
    success_count = 0
    failure_count = 0

    batches = _chunk(pairs, config.batch_size)
    for batch in batches:
        try:
            raw = await _call_claude(client, config.model, batch)
            results = _parse_match_results(raw, batch, config.match_cache_ttl_hours)
            all_results.extend(results)
            success_count += len(batch)
        except Exception:
            logger.exception("semantic.batch_failed", batch_size=len(batch))
            failure_count += len(batch)
            now = datetime.now(tz=timezone.utc)
            expires = now + timedelta(hours=config.match_cache_ttl_hours)
            all_results.extend([_fallback_result(p, k, now, expires) for p, k, _ in batch])

    logger.info(
        "semantic.complete",
        total_pairs=len(pairs),
        success=success_count,
        failures=failure_count,
        batches=len(batches),
    )
    return all_results


def _chunk(
    items: list[tuple[Market, Market, float]], size: int
) -> list[list[tuple[Market, Market, float]]]:
    """Split a list into fixed-size chunks.

    Args:
        items: List to split.
        size: Maximum chunk size.

    Returns:
        List of chunks.
    """
    return [items[i : i + size] for i in range(0, len(items), size)]
