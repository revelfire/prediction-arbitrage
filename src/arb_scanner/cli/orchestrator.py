"""Scan orchestrator -- ties all pipeline stages into a single async scan cycle.

Persists data progressively at each stage so that a downstream failure
(e.g. Claude API timeout) never loses upstream data (fetched markets,
price snapshots, embeddings).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from arb_scanner.cli._persist import (
    build_scan_log_partial,
    persist_embeddings,
    persist_markets,
    persist_opportunities,
    persist_scan_log,
)
from arb_scanner.cli.fixtures import build_output, build_scan_log, load_fixture_markets
from arb_scanner.engine.calculator import calculate_arbs
from arb_scanner.engine.tickets import generate_ticket
from arb_scanner.matching.demand_filter import demand_rank, rank_events
from arb_scanner.matching.embedding import _market_key, generate_embeddings
from arb_scanner.matching.embedding_prefilter import embedding_rerank
from arb_scanner.matching.prefilter import prefilter_candidates
from arb_scanner.matching.semantic import evaluate_pairs
from arb_scanner.models.arbitrage import ArbOpportunity, ExecutionTicket
from arb_scanner.models.config import Settings
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="cli.orchestrator")


async def run_scan(
    config: Settings,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute a single scan cycle with progressive persistence.

    Args:
        config: Application settings with venue, fee, and threshold config.
        dry_run: When True, load fixture data instead of calling live APIs.

    Returns:
        Result dict matching the CLI JSON output schema.
    """
    scan_id = str(uuid.uuid4())
    started_at = datetime.now(tz=UTC)
    errors: list[str] = []

    # --- Stage 1: Fetch markets ---
    poly_markets, kalshi_raw = await _fetch_markets(config, dry_run, errors)

    # --- Stage 1b: Demand-filter Kalshi by Poly relevance ---
    kalshi_markets = await _demand_filter(
        poly_markets,
        kalshi_raw,
        config,
        dry_run,
    )
    logger.info(
        "markets_fetched",
        polymarket=len(poly_markets),
        kalshi_raw=len(kalshi_raw),
        kalshi=len(kalshi_markets),
        errors=len(errors),
    )

    # Persist immediately: markets, snapshots, and initial scan log
    if not dry_run:
        await persist_markets(config, poly_markets, kalshi_markets)
        partial_log = build_scan_log_partial(
            scan_id,
            started_at,
            len(poly_markets),
            len(kalshi_markets),
            errors,
        )
        await persist_scan_log(config, partial_log)

    # --- Stage 2: Match candidates ---
    candidates: list[tuple[Market, Market, MatchResult]] = []
    try:
        candidates = await _match_candidates(
            poly_markets,
            kalshi_markets,
            config,
            dry_run,
            errors,
        )
    except Exception as exc:
        msg = f"matching stage failed: {exc}"
        logger.error("matching_stage_failed", error=str(exc), exc_info=True)
        errors.append(msg)

    # --- Stage 2b: Enrich Polymarket quotes with real CLOB book ---
    if candidates and not dry_run:
        candidates = await _enrich_poly_quotes(candidates, config)

    # --- Stage 3: Calculate arbs ---
    opps: list[ArbOpportunity] = []
    tickets: list[ExecutionTicket] = []
    if candidates:
        try:
            opps, tickets = _calculate_and_ticket(candidates, config)
        except Exception as exc:
            msg = f"arb calculation failed: {exc}"
            logger.error("arb_calc_failed", error=str(exc), exc_info=True)
            errors.append(msg)

    # Persist opportunities + tickets
    if not dry_run and opps:
        await persist_opportunities(config, opps, tickets)

    # --- Stage 4: Finalize scan log (always runs) ---
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
    logger.info(
        "scan_complete",
        scan_id=scan_id,
        opportunities=len(opps),
        errors=len(errors),
    )

    if not dry_run:
        await persist_scan_log(config, scan_log)

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
    """Fetch markets using event-driven Kalshi strategy.

    Fetches Polymarket concurrently with Kalshi event discovery.
    Then demand-ranks events by Poly relevance and fetches markets
    for the top events only, bypassing Kalshi's parlay-dominated
    ``/markets`` pagination.
    """
    from arb_scanner.ingestion.kalshi import KalshiClient
    from arb_scanner.ingestion.polymarket import PolymarketClient

    poly: list[Market] = []
    kalshi: list[Market] = []
    try:
        async with (
            PolymarketClient(config.venues.polymarket) as poly_client,
            KalshiClient(config.venues.kalshi) as kalshi_client,
        ):
            # Phase 1: Poly markets + Kalshi events in parallel
            poly_result, events_result = await asyncio.gather(
                poly_client.fetch_markets(),
                kalshi_client.fetch_events(),
                return_exceptions=True,
            )
            poly = _handle_venue_result(poly_result, "polymarket", errors)
            if isinstance(events_result, BaseException):
                errors.append(f"kalshi events failed: {events_result}")
                return poly, []

            events: list[dict[str, str]] = events_result
            # Phase 2: Demand-rank events by Poly relevance
            # Cap events (not markets) to limit API calls; each event
            # yields ~3-10 markets, so 100 events ≈ 500 markets.
            max_events = min(config.venues.kalshi.max_markets or 200, 100)
            top_tickers = await rank_events(poly, events, max_events)

            # Phase 3: Fetch markets for top events
            kalshi = await kalshi_client.fetch_markets_for_events(top_tickers)
    except Exception as exc:
        errors.append(f"fetch failed: {exc}")
        logger.error("fetch_error", error=str(exc), exc_info=True)
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


async def _demand_filter(
    poly_markets: list[Market],
    kalshi_markets: list[Market],
    config: Settings,
    dry_run: bool,
) -> list[Market]:
    """Narrow Kalshi markets to those most relevant to Polymarket."""
    if dry_run or not poly_markets or not kalshi_markets:
        return kalshi_markets
    max_k = config.venues.kalshi.max_markets or len(kalshi_markets)
    return await demand_rank(poly_markets, kalshi_markets, max_k)


async def _match_candidates(
    poly_markets: list[Market],
    kalshi_markets: list[Market],
    config: Settings,
    dry_run: bool,
    errors: list[str],
) -> list[tuple[Market, Market, MatchResult]]:
    """Run BM25 prefilter, embedding rerank, cache lookup, and semantic matching."""
    bm25_pairs = await prefilter_candidates(poly_markets, kalshi_markets)
    logger.info(
        "bm25_prefilter",
        poly_count=len(poly_markets),
        kalshi_count=len(kalshi_markets),
        candidate_pairs=len(bm25_pairs),
    )
    if not bm25_pairs:
        return []

    filtered_pairs, embeddings = await _run_embedding_rerank(
        bm25_pairs,
        config,
        dry_run,
    )

    if not dry_run and embeddings:
        await persist_embeddings(embeddings, config)

    if dry_run:
        uncached = filtered_pairs
        cached_results: list[MatchResult] = []
    else:
        uncached, cached_results = await _filter_cached(filtered_pairs, config)

    # Cap semantic evaluation to avoid excessive Claude API calls.
    # Pairs are sorted by BM25 score descending; top-N are best matches.
    max_pairs = config.claude.max_semantic_pairs
    if max_pairs and len(uncached) > max_pairs:
        logger.info(
            "semantic.capped",
            before=len(uncached),
            after=max_pairs,
        )
        uncached = uncached[:max_pairs]

    new_results = await _run_semantic(uncached, config, errors)

    if not dry_run:
        await _cache_results(new_results, config)

    all_results = cached_results + new_results
    return _zip_safe_matches(filtered_pairs, all_results)


async def _enrich_poly_quotes(
    candidates: list[tuple[Market, Market, MatchResult]],
    config: Settings,
) -> list[tuple[Market, Market, MatchResult]]:
    """Replace synthetic Polymarket quotes with real CLOB top-of-book."""
    from arb_scanner.ingestion.polymarket import PolymarketClient

    try:
        async with PolymarketClient(config.venues.polymarket) as poly:
            for pm, km, mr in candidates:
                if pm.venue == Venue.POLYMARKET:
                    await poly.enrich_with_book(pm)
        logger.info("clob_enrichment_done", candidates=len(candidates))
    except Exception:
        logger.warning("clob_enrichment_failed", exc_info=True)
    return candidates


async def _run_embedding_rerank(
    bm25_pairs: list[tuple[Market, Market, float]],
    config: Settings,
    dry_run: bool = False,
) -> tuple[list[tuple[Market, Market, float]], dict[str, list[float]]]:
    """Run embedding-based reranking if enabled, otherwise pass through.

    Returns:
        Tuple of (filtered_pairs, new_embeddings_dict).
    """
    if not config.embedding.enabled:
        logger.warning("embedding.skip", reason="disabled")
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
    logger.info("embedding.filtered", before=len(bm25_pairs), after=len(filtered_pairs))
    return filtered_pairs, new_embeddings


async def _load_cached_embeddings(
    markets: list[Market],
    config: Settings,
    dry_run: bool,
) -> dict[str, list[float]]:
    """Load previously persisted embeddings from pgvector."""
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
) -> tuple[list[tuple[Market, Market, float]], list[MatchResult]]:
    """Separate pairs into uncached (need eval) and cached hits."""
    from arb_scanner.matching.cache import MatchCache
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.repository import Repository

    uncached: list[tuple[Market, Market, float]] = []
    cached_hits: list[MatchResult] = []
    async with Database(config.storage.database_url) as db:
        repo = Repository(db.pool)
        cache = MatchCache(repo, ttl_hours=config.claude.match_cache_ttl_hours)
        for poly, kalshi, score in pairs:
            hit = await cache.get(poly.event_id, kalshi.event_id)
            if hit is None:
                uncached.append((poly, kalshi, score))
            else:
                cached_hits.append(hit)
    logger.info("cache.split", cached=len(cached_hits), uncached=len(uncached))
    return uncached, cached_hits


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
    tickets = [t for opp in opps if (t := generate_ticket(opp)) is not None]
    return opps, tickets
