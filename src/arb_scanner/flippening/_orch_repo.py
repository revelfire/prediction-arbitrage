"""Orchestrator persistence: repo creation, entry/exit persistence, discovery."""

from __future__ import annotations

from typing import Any

import structlog

from arb_scanner.models.config import Settings
from arb_scanner.models.flippening import (
    EntrySignal,
    ExitSignal,
    FlippeningEvent,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.orch_repo",
)


async def create_repo(config: Settings) -> Any:
    """Create FlippeningRepository from config.

    Args:
        config: Application settings with storage config.

    Returns:
        FlippeningRepository instance or None.
    """
    try:
        from arb_scanner.storage.db import Database
        from arb_scanner.storage.flippening_repository import FlippeningRepository

        db = Database(config.storage.database_url)
        await db.connect()
        return FlippeningRepository(db.pool)
    except Exception:
        logger.exception("repo_creation_failed")
        return None


async def create_tick_repo(config: Settings) -> Any:
    """Create TickRepository from config.

    Args:
        config: Application settings with storage config.

    Returns:
        TickRepository instance or None.
    """
    try:
        from arb_scanner.storage.db import Database
        from arb_scanner.storage.tick_repository import TickRepository

        db = Database(config.storage.database_url)
        await db.connect()
        return TickRepository(db.pool)
    except Exception:
        logger.exception("tick_repo_creation_failed")
        return None


async def persist_entry(
    repo: Any,
    event: FlippeningEvent,
    entry: EntrySignal,
    baseline: Any,
) -> None:
    """Persist entry data to the database.

    Args:
        repo: FlippeningRepository.
        event: Detected flippening event.
        entry: Entry signal.
        baseline: Baseline data.
    """
    try:
        await repo.insert_baseline(baseline)
        await repo.insert_event(event)
        await repo.insert_signal(entry)
        await repo.insert_flip_ticket(event, entry)
    except Exception:
        logger.exception("persist_entry_failed")


async def persist_exit(repo: Any, exit_sig: ExitSignal) -> None:
    """Persist exit signal to the database.

    Args:
        repo: FlippeningRepository.
        exit_sig: Exit signal.
    """
    try:
        await repo.insert_signal(exit_sig)
    except Exception:
        logger.exception("persist_exit_failed")


async def discover_markets(config: Settings) -> list[Any]:
    """Discover markets from Polymarket.

    Args:
        config: Application settings.

    Returns:
        List of Market objects.
    """
    try:
        from arb_scanner.ingestion.polymarket import PolymarketClient

        async with PolymarketClient(config.venues.polymarket) as poly:
            return await poly.fetch_markets()
    except Exception:
        logger.exception("market_discovery_failed")
        return []
