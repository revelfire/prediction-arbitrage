"""Repository for trade history import and backtesting persistence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.models.backtesting import (
    CategoryPerformance,
    ImportedTrade,
    ImportResult,
    OptimalParamSnapshot,
    TradePosition,
)
from arb_scanner.storage import _backtesting_queries as BQ

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="storage.backtesting_repository",
)


class BacktestingRepository:
    """Manages imported_trades and trade_positions tables.

    Args:
        pool: asyncpg connection pool.
    """

    def __init__(self, pool: Any) -> None:
        """Initialize with a database pool.

        Args:
            pool: asyncpg connection pool.
        """
        self._pool = pool

    async def import_trades(self, trades: list[ImportedTrade]) -> ImportResult:
        """Batch-insert trades, skipping duplicates by tx_hash.

        Args:
            trades: Validated imported trades to persist.

        Returns:
            ImportResult with inserted/duplicate counts.
        """
        inserted = 0
        duplicates = 0
        for trade in trades:
            result = await self._pool.execute(
                BQ.INSERT_TRADE,
                trade.market_name,
                trade.action.value,
                trade.usdc_amount,
                trade.token_amount,
                trade.token_name,
                trade.timestamp,
                trade.tx_hash,
                trade.condition_id,
            )
            if result == "INSERT 0 0":
                duplicates += 1
            else:
                inserted += 1

        logger.info(
            "trades_imported",
            inserted=inserted,
            duplicates=duplicates,
        )
        return ImportResult(inserted=inserted, duplicates=duplicates, errors=0)

    async def get_trades(
        self,
        *,
        market_name: str | None = None,
        action: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = 500,
    ) -> list[dict[str, Any]]:
        """Fetch imported trades with optional filters.

        Args:
            market_name: Filter by market name substring.
            action: Filter by trade action.
            since: Only trades at or after this time.
            until: Only trades before this time.
            limit: Maximum rows to return.

        Returns:
            List of trade dicts.
        """
        clauses: list[str] = []
        params: list[Any] = []
        idx = 1

        if market_name:
            clauses.append(f"market_name ILIKE ${idx}")
            params.append(f"%{market_name}%")
            idx += 1
        if action:
            clauses.append(f"action = ${idx}")
            params.append(action)
            idx += 1
        if since:
            clauses.append(f"timestamp >= ${idx}")
            params.append(since)
            idx += 1
        if until:
            clauses.append(f"timestamp < ${idx}")
            params.append(until)
            idx += 1

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = BQ.GET_TRADES_BASE + where + " ORDER BY timestamp DESC"
        if limit is not None:
            query += f" LIMIT ${idx}"
            params.append(limit)

        rows = await self._pool.fetch(query, *params)
        return [dict(r) for r in rows]

    async def upsert_position(self, position: TradePosition) -> None:
        """Insert or update a materialized trade position.

        Args:
            position: Position to persist.
        """
        await self._pool.execute(
            BQ.UPSERT_POSITION,
            position.market_name,
            position.token_name,
            position.cost_basis,
            position.tokens_held,
            position.avg_entry_price,
            position.realized_pnl,
            position.unrealized_pnl,
            position.status.value,
            position.fee_paid,
            position.first_trade_at,
            position.last_trade_at,
        )

    async def get_positions(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """Fetch trade positions with optional status filter.

        Args:
            status: Filter by position status (open/closed/resolved).

        Returns:
            List of position dicts.
        """
        if status:
            query = BQ.GET_POSITIONS_BASE + " WHERE status = $1"
            rows = await self._pool.fetch(query, status)
        else:
            query = BQ.GET_POSITIONS_BASE + " ORDER BY last_trade_at DESC"
            rows = await self._pool.fetch(query)
        return [dict(r) for r in rows]

    async def get_portfolio_summary(self) -> dict[str, Any]:
        """Fetch aggregate portfolio metrics.

        Returns:
            Dict with total P&L, fees, win/loss counts, capital deployed.
        """
        row = await self._pool.fetchrow(BQ.GET_PORTFOLIO_AGGREGATE)
        if row is None:
            return {
                "total_realized_pnl": Decimal("0"),
                "total_unrealized_pnl": Decimal("0"),
                "total_fees": Decimal("0"),
                "total_capital_deployed": Decimal("0"),
                "position_count": 0,
                "win_count": 0,
                "loss_count": 0,
            }
        return dict(row)

    async def get_daily_pnl(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch daily realized P&L for charting.

        Args:
            since: Start date filter.
            until: End date filter.

        Returns:
            List of {trade_date, daily_pnl} dicts.
        """
        clauses: list[str] = []
        params: list[Any] = []
        idx = 1

        if since:
            clauses.append(f"last_trade_at >= ${idx}")
            params.append(since)
            idx += 1
        if until:
            clauses.append(f"last_trade_at < ${idx}")
            params.append(until)
            idx += 1

        base = (
            "SELECT DATE(last_trade_at) AS trade_date,"
            " SUM(realized_pnl) AS daily_pnl"
            " FROM trade_positions WHERE realized_pnl != 0"
        )
        if clauses:
            base += " AND " + " AND ".join(clauses)
        base += " GROUP BY DATE(last_trade_at) ORDER BY trade_date"

        rows = await self._pool.fetch(base, *params)
        return [dict(r) for r in rows]

    async def get_capital_flows(self) -> list[dict[str, Any]]:
        """Fetch deposit and withdrawal transactions.

        Returns:
            List of capital flow trade dicts ordered by timestamp.
        """
        rows = await self._pool.fetch(BQ.GET_CAPITAL_FLOWS)
        return [dict(r) for r in rows]

    async def upsert_category_performance(self, perf: CategoryPerformance) -> None:
        """Insert or update category performance metrics.

        Args:
            perf: Category performance to persist.
        """
        await self._pool.execute(
            BQ.UPSERT_CATEGORY_PERFORMANCE,
            perf.category,
            perf.win_rate,
            perf.avg_pnl,
            perf.trade_count,
            perf.total_pnl,
            perf.profit_factor,
            perf.avg_hold_minutes,
            perf.signal_alignment_rate,
            perf.aligned_win_rate,
            perf.contrary_win_rate,
            perf.computed_at,
        )

    async def get_category_performance(self) -> list[dict[str, Any]]:
        """Fetch all category performance rows.

        Returns:
            List of category performance dicts.
        """
        rows = await self._pool.fetch(BQ.GET_CATEGORY_PERFORMANCE)
        return [dict(r) for r in rows]

    async def upsert_optimal_param(self, snapshot: OptimalParamSnapshot) -> None:
        """Insert or update an optimal parameter snapshot.

        Args:
            snapshot: Optimal parameter snapshot to persist.
        """
        await self._pool.execute(
            BQ.UPSERT_OPTIMAL_PARAM,
            snapshot.category,
            snapshot.param_name,
            snapshot.optimal_value,
            snapshot.win_rate_at_optimal,
            snapshot.sweep_date,
        )

    async def get_optimal_params(
        self,
        *,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch optimal parameter snapshots.

        Args:
            category: Optional category filter.

        Returns:
            List of optimal param dicts.
        """
        if category:
            query = BQ.GET_OPTIMAL_PARAMS + " WHERE category = $1 ORDER BY param_name"
            rows = await self._pool.fetch(query, category)
        else:
            query = BQ.GET_OPTIMAL_PARAMS + " ORDER BY category, param_name"
            rows = await self._pool.fetch(query)
        return [dict(r) for r in rows]
