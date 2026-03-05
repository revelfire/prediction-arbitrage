"""Repository for auto-execution pipeline persistence."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.storage import _auto_exec_queries as AQ

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="storage.auto_exec_repository",
)


class AutoExecRepository:
    """Manages auto_execution_log and auto_execution_positions tables.

    Args:
        pool: asyncpg connection pool.
    """

    def __init__(self, pool: Any) -> None:
        """Initialize with a database pool.

        Args:
            pool: asyncpg connection pool.
        """
        self._pool = pool

    async def insert_log(
        self,
        *,
        log_id: str,
        arb_id: str,
        trigger_spread_pct: Decimal,
        trigger_confidence: Decimal,
        criteria_snapshot: dict[str, Any],
        pre_exec_balances: dict[str, Any],
        size_usd: Decimal,
        critic_verdict: dict[str, Any] | None,
        execution_result_id: str | None,
        actual_spread: Decimal | None,
        actual_pnl: Decimal | None,
        slippage: Decimal | None,
        duration_ms: int | None,
        circuit_breaker_state: list[dict[str, Any]],
        status: str,
        source: str,
    ) -> None:
        """Insert an auto-execution log entry.

        Args:
            log_id: Unique log entry ID.
            arb_id: Related ticket/opportunity ID.
            trigger_spread_pct: Spread at trigger time.
            trigger_confidence: Confidence at trigger time.
            criteria_snapshot: Evaluation criteria state.
            pre_exec_balances: Venue balances before execution.
            size_usd: Trade size.
            critic_verdict: AI critic evaluation result.
            execution_result_id: Linked execution result.
            actual_spread: Realized spread.
            actual_pnl: Realized P&L.
            slippage: Price slippage.
            duration_ms: Execution duration.
            circuit_breaker_state: Breaker states at execution.
            status: Log entry status.
            source: Trigger source (arb_watch, flippening).
        """
        await self._pool.execute(
            AQ.INSERT_LOG,
            log_id,
            arb_id,
            trigger_spread_pct,
            trigger_confidence,
            json.dumps(criteria_snapshot, default=str),
            json.dumps(pre_exec_balances, default=str),
            size_usd,
            json.dumps(critic_verdict, default=str) if critic_verdict else None,
            execution_result_id,
            actual_spread,
            actual_pnl,
            slippage,
            duration_ms,
            json.dumps([s for s in circuit_breaker_state], default=str),
            status,
            source,
        )

    async def update_log(
        self,
        log_id: str,
        *,
        execution_result_id: str | None = None,
        actual_spread: Decimal | None = None,
        actual_pnl: Decimal | None = None,
        slippage: Decimal | None = None,
        duration_ms: int | None = None,
        status: str | None = None,
    ) -> None:
        """Update an existing log entry.

        Args:
            log_id: Log entry ID.
            execution_result_id: Linked execution result.
            actual_spread: Realized spread.
            actual_pnl: Realized P&L.
            slippage: Price slippage.
            duration_ms: Execution duration.
            status: New status.
        """
        await self._pool.execute(
            AQ.UPDATE_LOG,
            log_id,
            execution_result_id,
            actual_spread,
            actual_pnl,
            slippage,
            duration_ms,
            status,
        )

    async def get_log(self, log_id: str) -> dict[str, Any] | None:
        """Get a single log entry by ID.

        Args:
            log_id: Log entry ID.

        Returns:
            Log entry dict or None.
        """
        row = await self._pool.fetchrow(AQ.GET_LOG, log_id)
        return dict(row) if row else None

    async def list_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent log entries.

        Args:
            limit: Maximum entries to return.

        Returns:
            List of log entry dicts.
        """
        rows = await self._pool.fetch(AQ.LIST_LOG, limit)
        return [dict(r) for r in rows]

    async def insert_position(
        self,
        *,
        position_id: str,
        arb_id: str,
        poly_market_id: str,
        kalshi_ticker: str,
        entry_spread: Decimal,
        entry_cost_usd: Decimal,
        status: str = "open",
    ) -> None:
        """Insert an open position record.

        Args:
            position_id: Unique position ID.
            arb_id: Related ticket ID.
            poly_market_id: Polymarket token/market ID.
            kalshi_ticker: Kalshi ticker symbol.
            entry_spread: Entry spread percentage.
            entry_cost_usd: Total entry cost.
            status: Position status.
        """
        await self._pool.execute(
            AQ.INSERT_POSITION,
            position_id,
            arb_id,
            poly_market_id,
            kalshi_ticker,
            entry_spread,
            entry_cost_usd,
            status,
        )

    async def close_position(
        self,
        position_id: str,
        current_value_usd: Decimal,
    ) -> None:
        """Close an open position.

        Args:
            position_id: Position ID.
            current_value_usd: Value at close.
        """
        await self._pool.execute(AQ.CLOSE_POSITION, position_id, current_value_usd)

    async def get_open_positions(self) -> list[dict[str, Any]]:
        """Get all currently open positions.

        Returns:
            List of open position dicts.
        """
        rows = await self._pool.fetch(AQ.GET_OPEN_POSITIONS)
        return [dict(r) for r in rows]

    async def abandon_expired(self) -> list[dict[str, Any]]:
        """Abandon open arb positions past their max hold time.

        Returns:
            List of abandoned position dicts.
        """
        rows = await self._pool.fetch(AQ.ABANDON_EXPIRED_POSITIONS)
        abandoned = [dict(r) for r in rows]
        if abandoned:
            logger.warning("arb_positions_abandoned", count=len(abandoned))
        return abandoned

    async def get_daily_stats(self, days: int = 1) -> dict[str, Any]:
        """Get aggregate stats for a time window.

        Args:
            days: Number of days to aggregate.

        Returns:
            Stats dict with counts and aggregates.
        """
        row = await self._pool.fetchrow(AQ.GET_DAILY_STATS, str(days))
        if row is None:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": Decimal("0"),
                "avg_spread": Decimal("0"),
                "avg_slippage": Decimal("0"),
                "critic_rejections": 0,
                "breaker_trips": 0,
            }
        return dict(row)
