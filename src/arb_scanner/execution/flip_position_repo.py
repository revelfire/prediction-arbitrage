"""Repository for flippening auto-position tracking."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.storage import _flip_position_queries as Q

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.flip_position_repo",
)


class FlipPositionRepo:
    """CRUD for the flippening_auto_positions table.

    Tracks open Polymarket positions entered via the auto-execution
    pipeline so that exit signals can close them automatically.

    Args:
        pool: asyncpg connection pool.
    """

    def __init__(self, pool: Any) -> None:
        """Initialise with a shared connection pool.

        Args:
            pool: asyncpg connection pool.
        """
        self._pool = pool

    async def insert_position(
        self,
        *,
        arb_id: str,
        market_id: str,
        token_id: str,
        side: str,
        size_contracts: int,
        entry_price: Decimal,
        entry_order_id: str = "",
        max_hold_minutes: int | None = None,
        market_title: str = "",
        market_slug: str = "",
    ) -> str:
        """Insert a new open position after a successful entry order.

        Args:
            arb_id: Execution ticket identifier.
            market_id: Polymarket market slug or ID.
            token_id: CLOB token ID that was purchased.
            side: 'yes' or 'no' — which token is held.
            size_contracts: Number of contracts purchased.
            entry_price: Price per contract at entry.
            entry_order_id: Internal execution order UUID (optional).
            max_hold_minutes: Target hold duration from entry signal.
            market_title: Human-readable market title for display.
            market_slug: Polymarket slug for building market URLs.

        Returns:
            New position ID string.
        """
        row = await self._pool.fetchrow(
            Q.INSERT_POSITION,
            arb_id,
            market_id,
            token_id,
            side,
            size_contracts,
            entry_price,
            entry_order_id,
            max_hold_minutes,
            market_title,
            market_slug,
        )
        position_id: str = row["id"]
        logger.info("flip_position_inserted", market_id=market_id, side=side)
        return position_id

    async def get_position_by_arb_id(self, arb_id: str) -> dict[str, Any] | None:
        """Return the most recent position for a ticket, or None.

        Args:
            arb_id: Execution ticket identifier.

        Returns:
            Position dict or None.
        """
        row = await self._pool.fetchrow(Q.GET_POSITION_BY_ARB_ID, arb_id)
        return dict(row) if row is not None else None

    async def get_open_position(self, market_id: str) -> dict[str, Any] | None:
        """Return the active position for a market, or None if none exists.

        Active includes ``open`` and ``exit_failed`` states (inventory still held).

        Args:
            market_id: Polymarket market identifier.

        Returns:
            Position dict or None.
        """
        row = await self._pool.fetchrow(Q.GET_OPEN_POSITION, market_id)
        return dict(row) if row is not None else None

    async def close_position(
        self,
        market_id: str,
        exit_order_id: str,
        exit_price: Decimal,
        realized_pnl: Decimal,
        exit_reason: str,
    ) -> None:
        """Mark a position as closed with exit details.

        Args:
            market_id: Polymarket market identifier.
            exit_order_id: Internal execution order UUID for the sell.
            exit_price: Actual sell limit price used.
            realized_pnl: Realized profit/loss per contract.
            exit_reason: Human-readable exit reason string.
        """
        await self._pool.execute(
            Q.CLOSE_POSITION,
            market_id,
            exit_order_id,
            exit_price,
            realized_pnl,
            exit_reason,
        )
        logger.info(
            "flip_position_closed",
            market_id=market_id,
            pnl=float(realized_pnl),
            reason=exit_reason,
        )

    async def mark_exit_failed(self, market_id: str) -> None:
        """Mark a position as exit_failed after a rejected sell order.

        Args:
            market_id: Polymarket market identifier.
        """
        await self._pool.execute(Q.MARK_EXIT_FAILED, market_id)
        logger.warning("flip_position_exit_failed", market_id=market_id)

    async def mark_exit_pending(
        self,
        market_id: str,
        *,
        exit_order_id: str,
        exit_price: Decimal,
        exit_reason: str,
    ) -> None:
        """Mark a position as exit_pending after a submitted sell.

        Args:
            market_id: Polymarket market identifier.
            exit_order_id: Internal execution order UUID for the sell.
            exit_price: Requested exit price.
            exit_reason: Human-readable exit reason string.
        """
        await self._pool.execute(
            Q.MARK_EXIT_PENDING,
            market_id,
            exit_order_id,
            exit_price,
            exit_reason,
        )
        logger.info(
            "flip_position_exit_pending",
            market_id=market_id,
            exit_order_id=exit_order_id,
            exit_price=float(exit_price),
            reason=exit_reason,
        )

    async def get_open_positions(self) -> list[dict[str, Any]]:
        """Return all active flip positions for limit enforcement.

        Returns:
            List of active position dicts ordered by opened_at ascending.
        """
        rows = await self._pool.fetch(Q.GET_OPEN_POSITIONS_LIST)
        return [dict(r) for r in rows]

    async def get_exit_pending_positions(self) -> list[dict[str, Any]]:
        """Return positions currently waiting on exit fill confirmation.

        Returns:
            List of exit_pending position dicts ordered by opened_at ascending.
        """
        rows = await self._pool.fetch(Q.GET_EXIT_PENDING_POSITIONS)
        return [dict(r) for r in rows]

    async def abandon_expired(self) -> list[dict[str, Any]]:
        """Abandon open positions that exceeded their max hold time.

        Returns:
            List of abandoned position dicts.
        """
        rows = await self._pool.fetch(Q.ABANDON_EXPIRED_POSITIONS)
        abandoned = [dict(r) for r in rows]
        if abandoned:
            logger.warning("flip_positions_abandoned", count=len(abandoned))
        return abandoned

    async def get_orphaned_positions(self) -> list[dict[str, Any]]:
        """Return all active positions, used for startup orphan detection.

        Returns:
            List of active position dicts ordered by opened_at ascending.
        """
        rows = await self._pool.fetch(Q.GET_ORPHANED_POSITIONS)
        return [dict(r) for r in rows]
