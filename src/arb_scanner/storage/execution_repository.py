"""Repository for execution order persistence."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from arb_scanner.storage import _execution_queries as EQ


class ExecutionRepository:
    """Manages execution_orders and execution_results tables.

    Args:
        pool: asyncpg connection pool.
    """

    def __init__(self, pool: Any) -> None:
        """Initialize with a database pool.

        Args:
            pool: asyncpg connection pool.
        """
        self._pool = pool

    async def insert_order(
        self,
        *,
        order_id: str,
        arb_id: str,
        venue: str,
        venue_order_id: str | None,
        side: str,
        requested_price: Decimal,
        fill_price: Decimal | None,
        size_usd: Decimal,
        size_contracts: int | None,
        status: str,
        error_message: str | None,
    ) -> None:
        """Insert an execution order record.

        Args:
            order_id: UUID for this order.
            arb_id: Parent ticket ID.
            venue: 'polymarket' or 'kalshi'.
            venue_order_id: Venue's order identifier.
            side: Order side (buy_yes, buy_no, etc.).
            requested_price: Price we asked for.
            fill_price: Actual fill price (None if not filled).
            size_usd: Position size in USD.
            size_contracts: Number of contracts.
            status: Order status.
            error_message: Error details if failed.
        """
        await self._pool.execute(
            EQ.INSERT_ORDER,
            order_id,
            arb_id,
            venue,
            venue_order_id,
            side,
            requested_price,
            fill_price,
            size_usd,
            size_contracts,
            status,
            error_message,
        )

    async def update_order_status(
        self,
        order_id: str,
        status: str,
        fill_price: Decimal | None = None,
        venue_order_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update an order's status and optional fields.

        Args:
            order_id: The order UUID.
            status: New status.
            fill_price: Fill price if filled.
            venue_order_id: Venue order ID if now known.
            error_message: Error details if failed.
        """
        await self._pool.execute(
            EQ.UPDATE_ORDER_STATUS,
            order_id,
            status,
            fill_price,
            venue_order_id,
            error_message,
        )

    async def get_orders_for_ticket(self, arb_id: str) -> list[dict[str, Any]]:
        """Get all execution orders for a ticket.

        Args:
            arb_id: The ticket ID.

        Returns:
            List of order dicts.
        """
        rows = await self._pool.fetch(EQ.GET_ORDERS_FOR_TICKET, arb_id)
        return [dict(r) for r in rows]

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """Get all currently open execution orders.

        Returns:
            List of open order dicts.
        """
        rows = await self._pool.fetch(EQ.GET_OPEN_ORDERS)
        return [dict(r) for r in rows]

    async def count_open_positions(self) -> int:
        """Count distinct tickets with open orders.

        Returns:
            Number of open positions.
        """
        row = await self._pool.fetchrow(EQ.COUNT_OPEN_POSITIONS)
        return int(row["count"]) if row else 0

    async def insert_result(
        self,
        *,
        result_id: str,
        arb_id: str,
        total_cost_usd: Decimal | None,
        actual_spread: Decimal | None,
        slippage_from_ticket: Decimal | None,
        poly_order_id: str | None,
        kalshi_order_id: str | None,
        status: str,
    ) -> None:
        """Insert an execution result record.

        Args:
            result_id: UUID for this result.
            arb_id: Parent ticket ID.
            total_cost_usd: Total cost across both legs.
            actual_spread: Actual spread captured.
            slippage_from_ticket: Slippage vs ticket price.
            poly_order_id: Polymarket order UUID.
            kalshi_order_id: Kalshi order UUID.
            status: Result status (complete, partial, failed).
        """
        await self._pool.execute(
            EQ.INSERT_RESULT,
            result_id,
            arb_id,
            total_cost_usd,
            actual_spread,
            slippage_from_ticket,
            poly_order_id,
            kalshi_order_id,
            status,
        )

    async def get_result(self, arb_id: str) -> dict[str, Any] | None:
        """Get execution result for a ticket.

        Args:
            arb_id: The ticket ID.

        Returns:
            Result dict or None.
        """
        row = await self._pool.fetchrow(EQ.GET_RESULT, arb_id)
        return dict(row) if row else None

    async def get_daily_pnl(self) -> Decimal:
        """Get today's aggregate P&L from execution results.

        Returns:
            Today's total P&L.
        """
        row = await self._pool.fetchrow(EQ.GET_DAILY_PNL)
        return Decimal(str(row["daily_pnl"])) if row else Decimal("0")
