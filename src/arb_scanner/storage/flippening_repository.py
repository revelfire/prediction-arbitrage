"""Repository for flippening engine persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg
import structlog

from arb_scanner.models.flippening import (
    Baseline,
    EntrySignal,
    ExitSignal,
    FlippeningEvent,
)
from arb_scanner.storage import _flippening_queries as Q

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="storage.flippening_repository",
)


class FlippeningRepository:
    """Persistence layer for flippening baselines, events, and signals.

    Shares the same ``asyncpg.Pool`` as the main repository but keeps
    flippening-specific queries and methods isolated.
    """

    def __init__(self, pool: asyncpg.pool.Pool) -> None:
        """Initialise with a shared connection pool.

        Args:
            pool: asyncpg connection pool.
        """
        self._pool = pool

    async def insert_baseline(self, baseline: Baseline) -> None:
        """Persist a baseline odds capture.

        Args:
            baseline: Captured baseline odds for a sports market.
        """
        await self._pool.execute(
            Q.INSERT_BASELINE,
            baseline.market_id,
            baseline.token_id,
            baseline.yes_price,
            baseline.no_price,
            baseline.sport,
            baseline.game_start_time,
            baseline.captured_at,
            baseline.late_join,
        )
        logger.info(
            "baseline_inserted",
            market_id=baseline.market_id,
            sport=baseline.sport,
            late_join=baseline.late_join,
        )

    async def insert_event(self, event: FlippeningEvent) -> None:
        """Persist a detected flippening event.

        Args:
            event: The detected flippening.
        """
        await self._pool.execute(
            Q.INSERT_EVENT,
            event.id,
            event.market_id,
            event.market_title,
            event.baseline_yes,
            event.spike_price,
            event.spike_magnitude_pct,
            event.spike_direction.value,
            event.confidence,
            event.sport,
            event.detected_at,
        )
        logger.info(
            "event_inserted",
            event_id=event.id,
            market_id=event.market_id,
            sport=event.sport,
        )

    async def insert_signal(
        self,
        signal: EntrySignal | ExitSignal,
    ) -> None:
        """Persist an entry or exit signal.

        Args:
            signal: The signal to persist.
        """
        if isinstance(signal, EntrySignal):
            await self._pool.execute(
                Q.INSERT_SIGNAL,
                signal.id,
                signal.event_id,
                "entry",
                signal.side,
                signal.entry_price,
                signal.target_exit_price,
                signal.stop_loss_price,
                signal.suggested_size_usd,
                None,
                None,
                None,
                signal.created_at,
            )
        else:
            await self._pool.execute(
                Q.INSERT_SIGNAL,
                signal.id,
                signal.event_id,
                "exit",
                signal.side,
                signal.exit_price,
                None,
                None,
                None,
                signal.exit_reason.value,
                signal.realized_pnl,
                signal.hold_minutes,
                signal.created_at,
            )
        logger.info(
            "signal_inserted",
            signal_id=signal.id,
            event_id=signal.event_id,
            signal_type="entry" if isinstance(signal, EntrySignal) else "exit",
        )

    async def get_active_signals(
        self,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch open entry signals with no corresponding exit.

        Args:
            limit: Maximum number of results.

        Returns:
            List of active signal dicts.
        """
        rows = await self._pool.fetch(Q.GET_ACTIVE_SIGNALS, limit)
        return [dict(row) for row in rows]

    async def get_history(
        self,
        limit: int = 50,
        sport: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch completed flippenings with entry/exit pairs.

        Args:
            limit: Maximum number of results.
            sport: Optional sport filter.

        Returns:
            List of completed flippening dicts.
        """
        rows = await self._pool.fetch(Q.GET_HISTORY, limit, sport)
        return [dict(row) for row in rows]

    async def get_stats(
        self,
        sport: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch aggregated flippening performance by sport.

        Args:
            sport: Optional sport filter.
            since: Optional start timestamp.

        Returns:
            List of per-sport stat dicts.
        """
        rows = await self._pool.fetch(Q.GET_STATS, sport, since)
        return [dict(row) for row in rows]

    async def get_recent_events(
        self,
        limit: int = 50,
        sport: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch recent flippening events.

        Args:
            limit: Maximum number of results.
            sport: Optional sport filter.

        Returns:
            List of event dicts.
        """
        rows = await self._pool.fetch(Q.GET_RECENT_EVENTS, limit, sport)
        return [dict(row) for row in rows]
