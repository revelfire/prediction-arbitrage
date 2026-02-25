"""Scan orchestrator -- ties all pipeline stages into a single async scan cycle."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from arb_scanner.cli.fixtures import build_output, build_scan_log, load_fixture_markets
from arb_scanner.engine.calculator import calculate_arbs
from arb_scanner.engine.tickets import generate_ticket
from arb_scanner.matching.embedding import _market_key, generate_embeddings
from arb_scanner.matching.embedding_prefilter import embedding_rerank
from arb_scanner.matching.prefilter import prefilter_candidates
from arb_scanner.matching.semantic import evaluate_pairs
from arb_scanner.models.arbitrage import ArbOpportunity, ExecutionTicket
from arb_scanner.models.config import Settings
from arb_scanner.models.market import Market
from arb_scanner.models.matching import MatchResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="cli.orchestrator")


async def run_scan(
    config: Settings,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute a single scan cycle.

    Args:
        config: Application settings with venue, fee, and threshold config.
        dry_run: When True, load fixture data instead of calling live APIs.

    Returns:
        Result dict matching the CLI JSON output schema.
    """
    scan_id = str(uuid.uuid4())
    started_at = datetime.now(tz=UTC)
    errors: list[str] = []

    poly_markets, kalshi_markets = await _fetch_markets(config, dry_run, errors)
    candidates = await _match_candidates(
        poly_markets,
        kalshi_markets,
        config,
        dry_run,
        errors,
    )
    opps, tickets = _calculate_and_ticket(candidates, config)
    completed_at = datetime.now(tz=UTC)

    n_poly, n_kalshi, n_cand = len(poly_markets), len(kalshi_markets), len(candidates)
    scan_log = build_scan_log(
        scan_id,
        started_at,
        completed_at,
        n_poly,
        n_kalshi,
        n_cand,
        len(opps),
        errors,
    )
    logger.info("scan_complete", scan_id=scan_id, opportunities=len(opps))

    if not dry_run:
        await _persist_results(config, scan_log, opps, tickets)
        await _record_snapshots(config, poly_markets, kalshi_markets)

    output = build_output(scan_id, started_at, n_poly, n_kalshi, n_cand, opps)
    output["_raw_opps"] = opps
    return output


async def _fetch_markets(
    config: Settings,
    dry_run: bool,
    errors: list[str],
) -> tuple[list[Market], list[Market]]:
    """Fetch markets from both venues, or load fixtures for dry-run."""
    if dry_run:
        return load_fixture_markets()
    return await _fetch_live_markets(config, errors)


async def _fetch_live_markets(
    config: Settings,
    errors: list[str],
) -> tuple[list[Market], list[Market]]:
    """Fetch markets from live venue APIs concurrently."""
    from arb_scanner.ingestion.kalshi import KalshiClient
    from arb_scanner.ingestion.polymarket import PolymarketClient

    async def fetch_poly() -> list[Market]:
        async with PolymarketClient(config.venues.polymarket) as client:
            return await client.fetch_markets()

    async def fetch_kalshi() -> list[Market]:
        async with KalshiClient(config.venues.kalshi) as client:
            return await client.fetch_markets()

    results = await asyncio.gather(
        fetch_poly(),
        fetch_kalshi(),
        return_exceptions=True,
    )
    poly = _handle_venue_result(results[0], "polymarket", errors)
    kalshi = _handle_venue_result(results[1], "kalshi", errors)
    return poly, kalshi


def _handle_venue_result(
    result: list[Market] | BaseException,
    venue: str,
    errors: list[str],
) -> list[Market]:
    """Extract market list from a gather result, logging exceptions."""
    if isinstance(result, BaseException):
        msg = f"{venue} fetch failed: {result}"
        logger.error("venue_fetch_error", venue=venue, error=str(result))
        errors.append(msg)
        return []
    return result


async def _match_candidates(
    poly_markets: list[Market],
    kalshi_markets: list[Market],
    config: Settings,
    dry_run: bool,
    errors: list[str],
) -> list[tuple[Market, Market, MatchResult]]:
    """Run BM25 prefilter, embedding rerank, cache lookup, and semantic matching."""
    bm25_pairs = await prefilter_candidates(poly_markets, kalshi_markets)
    if not bm25_pairs:
        return []

    filtered_pairs, embeddings = await _run_embedding_rerank(bm25_pairs, config, dry_run)

    if not dry_run and embeddings:
        await _persist_embeddings(embeddings, config)

    uncached = filtered_pairs if dry_run else await _filter_cached(filtered_pairs, config)
    match_results = await _run_semantic(uncached, config, errors)

    if not dry_run:
        await _cache_results(match_results, config)

    return _zip_safe_matches(filtered_pairs, match_results)


async def _run_embedding_rerank(
    bm25_pairs: list[tuple[Market, Market, float]],
    config: Settings,
    dry_run: bool = False,
) -> tuple[list[tuple[Market, Market, float]], dict[str, list[float]]]:
    """Run embedding-based reranking if enabled, otherwise pass through.

    Loads cached embeddings from pgvector first, only generates
    embeddings for markets not already in the database.

    Returns:
        Tuple of (filtered_pairs, new_embeddings_dict). The new
        embeddings dict contains only freshly generated vectors
        (not cached ones).
    """
    if not config.embedding.enabled:
        logger.info("embedding.skip", reason="disabled")
        return bm25_pairs, {}

    if config.embedding.provider == "voyage" and not config.embedding.api_key:
        logger.info("embedding.skip", reason="voyage_no_api_key")
        return bm25_pairs, {}

    seen: dict[str, Market] = {}
    for poly, kalshi, _ in bm25_pairs:
        seen[_market_key(poly)] = poly
        seen[_market_key(kalshi)] = kalshi

    cached = await _load_cached_embeddings(list(seen.values()), config, dry_run)
    uncached_markets = [m for k, m in seen.items() if k not in cached]

    new_embeddings = await generate_embeddings(uncached_markets, config.embedding)
    all_embeddings = {**cached, **new_embeddings}

    logger.info(
        "embedding.cache",
        cached=len(cached),
        generated=len(new_embeddings),
        total=len(all_embeddings),
    )

    filtered_pairs = await embedding_rerank(bm25_pairs, all_embeddings, config.embedding)
    logger.info(
        "embedding.filtered",
        before=len(bm25_pairs),
        after=len(filtered_pairs),
    )
    return filtered_pairs, new_embeddings


async def _load_cached_embeddings(
    markets: list[Market],
    config: Settings,
    dry_run: bool,
) -> dict[str, list[float]]:
    """Load previously persisted embeddings from pgvector.

    Args:
        markets: Markets to look up.
        config: Application settings for database access.
        dry_run: Skip DB access in dry-run mode.

    Returns:
        Dict mapping ``"venue:event_id"`` to float vectors.
    """
    if dry_run or not markets:
        return {}

    from arb_scanner.storage.db import Database
    from arb_scanner.storage.repository import Repository

    try:
        pairs = [(m.venue.value, m.event_id) for m in markets]
        async with Database(config.storage.database_url) as db:
            repo = Repository(db.pool)
            return await repo.get_cached_embeddings(pairs, config.embedding.dimensions)
    except Exception:
        logger.warning("embedding.cache_load_failed", exc_info=True)
        return {}


async def _filter_cached(
    pairs: list[tuple[Market, Market, float]],
    config: Settings,
) -> list[tuple[Market, Market, float]]:
    """Remove pairs already in the match cache."""
    from arb_scanner.matching.cache import MatchCache
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.repository import Repository

    uncached: list[tuple[Market, Market, float]] = []
    async with Database(config.storage.database_url) as db:
        repo = Repository(db.pool)
        cache = MatchCache(repo, ttl_hours=config.claude.match_cache_ttl_hours)
        for poly, kalshi, score in pairs:
            hit = await cache.get(poly.event_id, kalshi.event_id)
            if hit is None:
                uncached.append((poly, kalshi, score))
    return uncached


async def _run_semantic(
    pairs: list[tuple[Market, Market, float]],
    config: Settings,
    errors: list[str],
) -> list[MatchResult]:
    """Evaluate uncached pairs via Claude semantic matcher."""
    if not pairs:
        return []
    try:
        return await evaluate_pairs(pairs, config.claude)
    except Exception as exc:
        msg = f"semantic matching failed: {exc}"
        logger.error("semantic_error", error=str(exc))
        errors.append(msg)
        return []


async def _cache_results(results: list[MatchResult], config: Settings) -> None:
    """Persist new match results to the cache."""
    from arb_scanner.matching.cache import MatchCache
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.repository import Repository

    async with Database(config.storage.database_url) as db:
        repo = Repository(db.pool)
        cache = MatchCache(repo, ttl_hours=config.claude.match_cache_ttl_hours)
        for result in results:
            await cache.set(result)


def _zip_safe_matches(
    bm25_pairs: list[tuple[Market, Market, float]],
    match_results: list[MatchResult],
) -> list[tuple[Market, Market, MatchResult]]:
    """Combine BM25 pairs with their semantic match results."""
    lookup: dict[tuple[str, str], MatchResult] = {
        (m.poly_event_id, m.kalshi_event_id): m for m in match_results
    }
    matched: list[tuple[Market, Market, MatchResult]] = []
    for poly, kalshi, _score in bm25_pairs:
        mr = lookup.get((poly.event_id, kalshi.event_id))
        if mr is not None:
            matched.append((poly, kalshi, mr))
    return matched


def _calculate_and_ticket(
    candidates: list[tuple[Market, Market, MatchResult]],
    config: Settings,
) -> tuple[list[ArbOpportunity], list[ExecutionTicket]]:
    """Run arb calculation and ticket generation."""
    opps = calculate_arbs(candidates, config.fees, config.arb_thresholds)
    tickets = [generate_ticket(opp) for opp in opps]
    return opps, tickets


async def _persist_results(
    config: Settings,
    scan_log: Any,
    opps: list[ArbOpportunity],
    tickets: list[ExecutionTicket],
) -> None:
    """Write scan results to the database."""
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.repository import Repository

    try:
        async with Database(config.storage.database_url) as db:
            repo = Repository(db.pool)
            await repo.insert_scan_log(scan_log)
            for opp in opps:
                await repo.insert_opportunity(opp)
            for ticket in tickets:
                await repo.insert_ticket(ticket)
    except Exception:
        logger.exception("persist_results_failed")


async def _persist_embeddings(
    embeddings: dict[str, list[float]],
    config: Settings,
) -> None:
    """Write embedding vectors to the markets table (fire-and-forget).

    Writes to the column matching the configured dimensions (384 or 512).
    Errors are logged but never propagated so a persistence failure
    cannot break the scan pipeline.

    Args:
        embeddings: Mapping of ``"venue:event_id"`` to float vectors.
        config: Application settings for database access.
    """
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.repository import Repository

    use_384 = config.embedding.dimensions == 384

    try:
        async with Database(config.storage.database_url) as db:
            repo = Repository(db.pool)
            for key, vector in embeddings.items():
                venue, event_id = key.split(":", 1)
                if use_384:
                    await repo.update_market_embedding_384(venue, event_id, vector)
                else:
                    await repo.update_market_embedding(venue, event_id, vector)
        logger.info("embedding.persisted", count=len(embeddings))
    except Exception:
        logger.exception("embedding.persist_failed")


async def _record_snapshots(
    config: Settings,
    poly_markets: list[Market],
    kalshi_markets: list[Market],
) -> None:
    """Record price snapshots for all fetched markets."""
    from arb_scanner.storage.analytics_repository import AnalyticsRepository
    from arb_scanner.storage.db import Database

    all_markets = [*poly_markets, *kalshi_markets]
    if not all_markets:
        return

    try:
        async with Database(config.storage.database_url) as db:
            repo = AnalyticsRepository(db.pool)
            for market in all_markets:
                await repo.insert_market_snapshot(market)
        logger.info("snapshots.recorded", count=len(all_markets))
    except Exception:
        logger.exception("snapshots_record_failed")
