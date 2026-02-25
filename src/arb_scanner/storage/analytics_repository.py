"""Repository for analytics and historical spread queries."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import asyncpg

from arb_scanner.models.analytics import (
    AlertType,
    HourlyBucket,
    PairSummary,
    ScanHealthSummary,
    SpreadSnapshot,
    TrendAlert,
)
from arb_scanner.models.market import Market
from arb_scanner.storage import _analytics_queries as AQ


class AnalyticsRepository:
    """Data access layer for analytics and historical spread queries."""

    def __init__(self, pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Initialize with an asyncpg connection pool.

        Args:
            pool: An active asyncpg connection pool.
        """
        self._pool = pool

    # ------------------------------------------------------------------
    # Spread history (T006)
    # ------------------------------------------------------------------

    async def get_spread_history(
        self,
        poly_id: str,
        kalshi_id: str,
        since: datetime,
    ) -> list[SpreadSnapshot]:
        """Fetch time-series spread data for a specific arb pair.

        Args:
            poly_id: Polymarket event ID.
            kalshi_id: Kalshi event ID.
            since: Only include observations after this timestamp.

        Returns:
            List of SpreadSnapshot models ordered by detected_at descending.
        """
        rows = await self._pool.fetch(AQ.GET_SPREAD_HISTORY, poly_id, kalshi_id, since)
        return [_row_to_spread_snapshot(row) for row in rows]

    # ------------------------------------------------------------------
    # Pair summaries (T007)
    # ------------------------------------------------------------------

    async def get_pair_summaries(self, since: datetime) -> list[PairSummary]:
        """Fetch aggregated statistics for all arb pairs in a time window.

        Args:
            since: Only include observations after this timestamp.

        Returns:
            List of PairSummary models ordered by peak_spread descending.
        """
        rows = await self._pool.fetch(AQ.GET_PAIR_SUMMARIES, since)
        return [_row_to_pair_summary(row) for row in rows]

    # ------------------------------------------------------------------
    # Hourly buckets (T008)
    # ------------------------------------------------------------------

    async def get_hourly_buckets(self, since: datetime) -> list[HourlyBucket]:
        """Fetch hourly aggregations of spread observations.

        Args:
            since: Only include observations after this timestamp.

        Returns:
            List of HourlyBucket models ordered by hour descending.
        """
        rows = await self._pool.fetch(AQ.GET_HOURLY_BUCKETS, since)
        return [_row_to_hourly_bucket(row) for row in rows]

    # ------------------------------------------------------------------
    # Scan health (T009)
    # ------------------------------------------------------------------

    async def get_scan_health(self, since: datetime) -> list[ScanHealthSummary]:
        """Fetch hourly health metrics for the scanning pipeline.

        Args:
            since: Only include scan logs after this timestamp.

        Returns:
            List of ScanHealthSummary models ordered by hour descending.
        """
        rows = await self._pool.fetch(AQ.GET_SCAN_HEALTH, since)
        return [_row_to_scan_health(row) for row in rows]

    async def get_recent_scan_logs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch the most recent scan log records.

        Args:
            limit: Maximum number of scan logs to return.

        Returns:
            List of scan log records as dictionaries.
        """
        rows = await self._pool.fetch(AQ.GET_RECENT_SCAN_LOGS, limit)
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Date-range queries (T010)
    # ------------------------------------------------------------------

    async def get_opportunities_date_range(
        self,
        since: datetime,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch arb opportunities within a date range.

        Args:
            since: Start of the date range (inclusive).
            until: End of the date range (exclusive). None for no upper bound.
            limit: Maximum number of results to return.

        Returns:
            List of opportunity records as dictionaries.
        """
        rows = await self._pool.fetch(AQ.GET_OPPS_DATE_RANGE, since, until, limit)
        return [dict(row) for row in rows]

    async def get_tickets_date_range(
        self,
        since: datetime,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch execution tickets within a date range.

        Args:
            since: Start of the date range (inclusive).
            until: End of the date range (exclusive). None for no upper bound.
            limit: Maximum number of results to return.

        Returns:
            List of ticket records (joined with opportunities) as dictionaries.
        """
        rows = await self._pool.fetch(AQ.GET_TICKETS_DATE_RANGE, since, until, limit)
        return [dict(row) for row in rows]

    async def get_matches_date_range(
        self,
        since: datetime,
        include_expired: bool = False,
        min_confidence: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Fetch match results within a date range.

        Args:
            since: Only include matches after this timestamp.
            include_expired: When True, include matches past their TTL.
            min_confidence: Minimum match confidence threshold.

        Returns:
            List of match result records as dictionaries.
        """
        rows = await self._pool.fetch(
            AQ.GET_MATCHES_DATE_RANGE, include_expired, min_confidence, since
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Market snapshots (T011)
    # ------------------------------------------------------------------

    async def insert_market_snapshot(self, market: Market) -> None:
        """Insert a point-in-time price snapshot for a market.

        Args:
            market: The Market model whose prices will be snapshotted.
        """
        await self._pool.execute(
            AQ.INSERT_SNAPSHOT,
            market.venue.value,
            market.event_id,
            market.yes_bid,
            market.yes_ask,
            market.no_bid,
            market.no_ask,
            market.volume_24h,
            datetime.now(tz=timezone.utc),
        )

    async def get_price_history(
        self,
        venue: str,
        event_id: str,
        since: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch price snapshot history for a specific market.

        Args:
            venue: Venue identifier (e.g. "polymarket", "kalshi").
            event_id: The event ID to query.
            since: Only include snapshots after this timestamp.

        Returns:
            List of snapshot records as dictionaries.
        """
        rows = await self._pool.fetch(AQ.GET_PRICE_HISTORY, venue, event_id, since)
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Trend alerts (T015–T016)
    # ------------------------------------------------------------------

    async def insert_trend_alert(self, alert: TrendAlert) -> None:
        """Persist a trend alert to the database.

        Args:
            alert: The TrendAlert model to store.
        """
        await self._pool.execute(
            AQ.INSERT_TREND_ALERT,
            alert.alert_type.value,
            alert.poly_event_id,
            alert.kalshi_event_id,
            alert.spread_before,
            alert.spread_after,
            alert.message,
            alert.dispatched_at,
        )

    async def get_recent_alerts(
        self, limit: int = 20, alert_type: str | None = None
    ) -> list[TrendAlert]:
        """Fetch recent trend alerts from the database.

        Args:
            limit: Maximum number of alerts to return.
            alert_type: Optional filter by alert type value.

        Returns:
            List of TrendAlert models ordered by dispatched_at descending.
        """
        rows = await self._pool.fetch(
            AQ.GET_RECENT_ALERTS,
            alert_type,
            limit,
        )
        return [
            TrendAlert(
                alert_type=AlertType(row["alert_type"]),
                poly_event_id=row["poly_event_id"],
                kalshi_event_id=row["kalshi_event_id"],
                spread_before=row["spread_before"],
                spread_after=row["spread_after"],
                message=row["message"],
                dispatched_at=row["dispatched_at"],
            )
            for row in rows
        ]


# ------------------------------------------------------------------
# Row mappers (module-private)
# ------------------------------------------------------------------


def _row_to_spread_snapshot(row: asyncpg.Record) -> SpreadSnapshot:
    """Convert an asyncpg Record to a SpreadSnapshot model."""
    return SpreadSnapshot(
        detected_at=row["detected_at"],
        net_spread_pct=Decimal(str(row["net_spread_pct"])),
        annualized_return=(
            Decimal(str(row["annualized_return"])) if row["annualized_return"] is not None else None
        ),
        depth_risk=row["depth_risk"],
        max_size=Decimal(str(row["max_size"])),
    )


def _row_to_pair_summary(row: asyncpg.Record) -> PairSummary:
    """Convert an asyncpg Record to a PairSummary model."""
    return PairSummary(
        poly_event_id=row["poly_event_id"],
        kalshi_event_id=row["kalshi_event_id"],
        peak_spread=Decimal(str(row["peak_spread"])),
        min_spread=Decimal(str(row["min_spread"])),
        avg_spread=Decimal(str(row["avg_spread"])),
        total_detections=row["total_detections"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
    )


def _row_to_hourly_bucket(row: asyncpg.Record) -> HourlyBucket:
    """Convert an asyncpg Record to an HourlyBucket model."""
    return HourlyBucket(
        hour=row["hour"],
        avg_spread=Decimal(str(row["avg_spread"])),
        max_spread=Decimal(str(row["max_spread"])),
        detection_count=row["detection_count"],
    )


def _row_to_scan_health(row: asyncpg.Record) -> ScanHealthSummary:
    """Convert an asyncpg Record to a ScanHealthSummary model."""
    return ScanHealthSummary(
        hour=row["hour"],
        scan_count=int(row["scan_count"]),
        avg_duration_s=float(row["avg_duration_s"]),
        total_llm_calls=int(row["total_llm_calls"]),
        total_opps=int(row["total_opps"]),
        total_errors=int(row["total_errors"]),
    )
