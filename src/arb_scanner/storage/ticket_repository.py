"""Repository for execution ticket management."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import asyncpg
import structlog

from arb_scanner.storage import _ticket_queries as TQ

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="storage.ticket_repository",
)


class TicketRepository:
    """Persistence layer for ticket lifecycle, actions, and summaries.

    Shares the same ``asyncpg.Pool`` as other repositories.
    """

    def __init__(self, pool: asyncpg.pool.Pool) -> None:
        """Initialise with a shared connection pool.

        Args:
            pool: asyncpg connection pool.
        """
        self._pool = pool

    async def get_tickets(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        ticket_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch tickets with optional filters.

        Args:
            status: Filter by status.
            category: Filter by category.
            ticket_type: Filter by ticket type.
            limit: Max results.

        Returns:
            List of ticket dicts.
        """
        rows = await self._pool.fetch(TQ.GET_TICKETS_FILTERED, status, category, ticket_type, limit)
        return [dict(row) for row in rows]

    async def get_ticket(self, arb_id: str) -> dict[str, Any] | None:
        """Fetch a single ticket with event data.

        Args:
            arb_id: Ticket/arb identifier.

        Returns:
            Ticket dict or None.
        """
        row = await self._pool.fetchrow(TQ.GET_TICKET_BY_ID, arb_id)
        if row is None:
            return None
        return dict(row)

    async def update_status(self, arb_id: str, status: str) -> None:
        """Update a ticket's status.

        Args:
            arb_id: Ticket identifier.
            status: New status value.
        """
        await self._pool.execute(TQ.UPDATE_TICKET_STATUS, arb_id, status)
        logger.info("ticket_status_updated", arb_id=arb_id, status=status)

    async def insert_action(
        self,
        *,
        action_id: str,
        ticket_id: str,
        action: str,
        actual_entry_price: Decimal | None = None,
        actual_size_usd: Decimal | None = None,
        actual_exit_price: Decimal | None = None,
        actual_pnl: Decimal | None = None,
        slippage: Decimal | None = None,
        notes: str = "",
    ) -> None:
        """Record a ticket action.

        Args:
            action_id: UUID for the action.
            ticket_id: Parent ticket arb_id.
            action: Action type.
            actual_entry_price: Actual execution entry price.
            actual_size_usd: Actual position size in USD.
            actual_exit_price: Actual exit price (if closing).
            actual_pnl: Realized P&L.
            slippage: Price slippage from suggested.
            notes: Operator notes.
        """
        await self._pool.execute(
            TQ.INSERT_TICKET_ACTION,
            action_id,
            ticket_id,
            action,
            actual_entry_price,
            actual_size_usd,
            actual_exit_price,
            actual_pnl,
            slippage,
            notes,
            datetime.now(tz=timezone.utc),
        )
        logger.info(
            "ticket_action_recorded",
            ticket_id=ticket_id,
            action=action,
        )

    async def get_actions(self, ticket_id: str) -> list[dict[str, Any]]:
        """Fetch all actions for a ticket.

        Args:
            ticket_id: Ticket arb_id.

        Returns:
            List of action dicts ordered by time.
        """
        rows = await self._pool.fetch(TQ.GET_TICKET_ACTIONS, ticket_id)
        return [dict(row) for row in rows]

    async def get_summary(self, days: int = 30) -> list[dict[str, Any]]:
        """Fetch performance summary aggregated by category.

        Args:
            days: Lookback window in days.

        Returns:
            List of summary dicts.
        """
        rows = await self._pool.fetch(TQ.GET_TICKET_SUMMARY, str(days))
        return [dict(row) for row in rows]

    async def auto_expire(self, max_age_hours: int = 24) -> list[str]:
        """Expire pending tickets older than threshold.

        Args:
            max_age_hours: Maximum age in hours.

        Returns:
            List of expired arb_ids.
        """
        rows = await self._pool.fetch(TQ.AUTO_EXPIRE_TICKETS, str(max_age_hours))
        expired = [row["arb_id"] for row in rows]
        if expired:
            logger.info("tickets_auto_expired", count=len(expired))
        return expired

    async def prune_tickets(self, before: datetime) -> int:
        """Delete terminal tickets older than the given cutoff.

        Removes tickets in expired, executed, or cancelled states
        that were created before ``before``.

        Args:
            before: Delete tickets created before this datetime.

        Returns:
            Number of deleted tickets.
        """
        rows = await self._pool.fetch(TQ.PRUNE_TERMINAL_TICKETS, before)
        count = len(rows)
        if count:
            logger.info("tickets_pruned", count=count)
        return count
