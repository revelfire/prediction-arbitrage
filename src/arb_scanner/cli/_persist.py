"""Persistence helpers for the scan orchestrator.

Each function wraps a database write in try/except so that a storage
failure never aborts the scan pipeline.  Data that was successfully
fetched or computed is always saved before the next stage begins.
"""

from __future__ import annotations

from typing import Any

import structlog

from arb_scanner.models.arbitrage import ArbOpportunity, ExecutionTicket
from arb_scanner.models.config import Settings
from arb_scanner.models.market import Market
from arb_scanner.models.scan_log import ScanLog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="cli.persist")


async def persist_markets(
    config: Settings,
    poly_markets: list[Market],
    kalshi_markets: list[Market],
) -> None:
    """Upsert fetched markets and record price snapshots.

    Args:
        config: Application settings.
        poly_markets: Polymarket markets to persist.
        kalshi_markets: Kalshi markets to persist.
    """
    from arb_scanner.storage.analytics_repository import AnalyticsRepository
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.repository import Repository

    all_markets = [*poly_markets, *kalshi_markets]
    if not all_markets:
        return

    try:
        async with Database(config.storage.database_url) as db:
            repo = Repository(db.pool)
            analytics = AnalyticsRepository(db.pool)
            for market in all_markets:
                await repo.upsert_market(market)
                await analytics.insert_market_snapshot(market)
        logger.info(
            "markets_persisted",
            polymarket=len(poly_markets),
            kalshi=len(kalshi_markets),
        )
    except Exception:
        logger.exception("persist_markets_failed")


async def persist_scan_log(config: Settings, scan_log: ScanLog) -> None:
    """Upsert a scan log record (insert-or-update).

    Args:
        config: Application settings.
        scan_log: The scan log to write.
    """
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.repository import Repository

    try:
        async with Database(config.storage.database_url) as db:
            repo = Repository(db.pool)
            await repo.upsert_scan_log(scan_log)
    except Exception:
        logger.exception("persist_scan_log_failed")


async def persist_opportunities(
    config: Settings,
    opps: list[ArbOpportunity],
    tickets: list[ExecutionTicket],
) -> None:
    """Write arb opportunities and execution tickets.

    Args:
        config: Application settings.
        opps: Discovered arbitrage opportunities.
        tickets: Generated execution tickets.
    """
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.repository import Repository

    if not opps:
        return

    # Build mapping from arb_id to market pair for dedup lookup
    pair_by_arb_id = {
        opp.id: (opp.poly_market.event_id, opp.kalshi_market.event_id) for opp in opps
    }

    try:
        async with Database(config.storage.database_url) as db:
            repo = Repository(db.pool)
            pending_pairs = await repo.get_pending_arb_pair_ids()
            for opp in opps:
                await repo.insert_opportunity(opp)
            skipped = 0
            for ticket in tickets:
                pair = pair_by_arb_id.get(ticket.arb_id)
                if pair and pair in pending_pairs:
                    logger.debug("ticket_dedup_skip", arb_id=ticket.arb_id)
                    skipped += 1
                    continue
                await repo.insert_ticket(ticket)
                if pair:
                    pending_pairs.add(pair)
        logger.info("opportunities_persisted", count=len(opps), tickets_skipped=skipped)
    except Exception:
        logger.exception("persist_opportunities_failed")


async def persist_embeddings(
    embeddings: dict[str, list[float]],
    config: Settings,
) -> None:
    """Write embedding vectors to the markets table (fire-and-forget).

    Args:
        embeddings: Mapping of ``"venue:event_id"`` to float vectors.
        config: Application settings.
    """
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.repository import Repository

    if not embeddings:
        return

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
        logger.info("embeddings_persisted", count=len(embeddings))
    except Exception:
        logger.exception("persist_embeddings_failed")


def build_scan_log_partial(
    scan_id: str,
    started_at: Any,
    poly_count: int,
    kalshi_count: int,
    errors: list[str],
) -> ScanLog:
    """Build an in-progress scan log (no completed_at yet).

    Args:
        scan_id: Unique scan identifier.
        started_at: Scan start time.
        poly_count: Polymarket markets fetched.
        kalshi_count: Kalshi markets fetched.
        errors: Accumulated error messages.

    Returns:
        Partial ScanLog with completed_at=None.
    """
    return ScanLog(
        id=scan_id,
        started_at=started_at,
        poly_markets_fetched=poly_count,
        kalshi_markets_fetched=kalshi_count,
        errors=errors,
    )
