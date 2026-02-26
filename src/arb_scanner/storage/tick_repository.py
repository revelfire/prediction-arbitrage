"""Repository for tick capture and replay data."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg
import structlog

from arb_scanner.storage import _tick_queries as Q

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="storage.tick_repository",
)


class TickRepository:
    """Persistence layer for price ticks and baseline drifts.

    Shares the same ``asyncpg.Pool`` as other repositories.
    """

    def __init__(self, pool: asyncpg.pool.Pool) -> None:
        """Initialise with a shared connection pool.

        Args:
            pool: asyncpg connection pool.
        """
        self._pool = pool

    async def insert_ticks_batch(self, ticks: list[tuple[Any, ...]]) -> None:
        """Batch-insert price ticks.

        Args:
            ticks: List of row tuples matching INSERT_TICK columns.
        """
        if not ticks:
            return
        await self._pool.executemany(Q.INSERT_TICK, ticks)

    async def insert_drift(
        self,
        market_id: str,
        old_yes: Decimal,
        new_yes: Decimal,
        drifted_at: datetime,
        drift_reason: str = "gradual",
    ) -> None:
        """Persist a baseline drift event.

        Args:
            market_id: Market identifier.
            old_yes: Previous baseline YES price.
            new_yes: New baseline YES price after drift.
            drifted_at: Timestamp of the drift.
            drift_reason: Reason for drift (default "gradual").
        """
        await self._pool.execute(
            Q.INSERT_DRIFT,
            market_id,
            old_yes,
            new_yes,
            drift_reason,
            drifted_at,
        )

    async def stream_ticks(
        self,
        market_id: str,
        since: datetime,
        until: datetime,
    ) -> AsyncIterator[asyncpg.Record]:
        """Stream ticks for a market in a time range via cursor.

        Uses asyncpg cursor to avoid loading all ticks into memory.

        Args:
            market_id: Market identifier.
            since: Start of time range (inclusive).
            until: End of time range (inclusive).

        Yields:
            asyncpg.Record for each tick row.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                async for record in conn.cursor(
                    Q.SELECT_TICKS_BY_MARKET,
                    market_id,
                    since,
                    until,
                ):
                    yield record

    async def get_drifts(
        self,
        market_id: str,
        since: datetime,
        until: datetime,
    ) -> list[asyncpg.Record]:
        """Fetch baseline drifts for a market in a time range.

        Args:
            market_id: Market identifier.
            since: Start of time range.
            until: End of time range.

        Returns:
            List of drift records ordered by drifted_at.
        """
        return await self._pool.fetch(
            Q.SELECT_DRIFTS_BY_MARKET,
            market_id,
            since,
            until,
        )

    async def get_market_ids(
        self,
        sport: str,
        since: datetime,
        until: datetime,
    ) -> list[str]:
        """Get distinct market IDs with ticks for a sport/category in a time range.

        Args:
            sport: Sport or category identifier (e.g. "nba").
            since: Start of time range.
            until: End of time range.

        Returns:
            List of market ID strings.
        """
        rows = await self._pool.fetch(
            Q.SELECT_DISTINCT_MARKETS,
            sport,
            since,
            until,
        )
        return [row["market_id"] for row in rows]

    async def get_baseline(
        self,
        market_id: str,
    ) -> asyncpg.Record | None:
        """Fetch the most recent baseline for a market.

        Args:
            market_id: Market identifier.

        Returns:
            Baseline record or None if not found.
        """
        return await self._pool.fetchrow(Q.SELECT_BASELINE, market_id)

    async def prune_ticks(self, before: datetime) -> int:
        """Delete ticks older than the given timestamp.

        Args:
            before: Delete all ticks with timestamp before this.

        Returns:
            Number of rows deleted.
        """
        result = await self._pool.execute(Q.DELETE_OLD_TICKS, before)
        # asyncpg returns "DELETE N"
        count_str = result.split()[-1] if result else "0"
        return int(count_str)
