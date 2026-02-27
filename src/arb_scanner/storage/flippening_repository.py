"""Repository for flippening engine persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
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
from arb_scanner.storage import _ws_telemetry_queries as WQ

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
        """Persist a baseline odds capture."""
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
            baseline.category or baseline.sport,
            baseline.category_type,
            baseline.baseline_strategy,
        )
        logger.info(
            "baseline_inserted",
            market_id=baseline.market_id,
            category=baseline.category or baseline.sport,
            late_join=baseline.late_join,
        )

    async def insert_event(self, event: FlippeningEvent) -> None:
        """Persist a detected flippening event."""
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
            event.category or event.sport,
            event.category_type,
        )
        logger.info(
            "event_inserted",
            event_id=event.id,
            market_id=event.market_id,
            category=event.category or event.sport,
        )

    async def insert_signal(self, signal: EntrySignal | ExitSignal) -> None:
        """Persist an entry or exit signal."""
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

    async def get_active_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch open entry signals with no corresponding exit."""
        rows = await self._pool.fetch(Q.GET_ACTIVE_SIGNALS, limit)
        return [dict(row) for row in rows]

    async def get_history(
        self,
        limit: int = 50,
        sport: str | None = None,
        category: str | None = None,
        category_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch completed flippenings with entry/exit pairs."""
        cat_filter = category or sport
        rows = await self._pool.fetch(Q.GET_HISTORY, limit, cat_filter, category_type)
        return [dict(row) for row in rows]

    async def get_stats(
        self,
        sport: str | None = None,
        category: str | None = None,
        category_type: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch aggregated flippening performance by category."""
        cat_filter = category or sport
        rows = await self._pool.fetch(Q.GET_STATS, cat_filter, category_type, since)
        return [dict(row) for row in rows]

    async def get_recent_events(
        self,
        limit: int = 50,
        sport: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch recent flippening events."""
        rows = await self._pool.fetch(Q.GET_RECENT_EVENTS, limit, sport)
        return [dict(row) for row in rows]

    async def insert_discovery_health(self, snapshot: dict[str, Any]) -> None:
        """Persist a discovery health snapshot."""
        found = snapshot.get("markets_found", snapshot.get("sports_found", 0))
        by_cat = snapshot.get("by_category", snapshot.get("by_sport", {}))
        await self._pool.execute(
            Q.INSERT_DISCOVERY_HEALTH,
            snapshot.get("cycle_timestamp", datetime.now(tz=timezone.utc)),
            snapshot["total_scanned"],
            found,
            snapshot["hit_rate"],
            json.dumps(by_cat),
            snapshot.get("overrides_applied", 0),
            snapshot.get("exclusions_applied", 0),
            snapshot.get("unclassified_candidates", 0),
        )

    async def get_discovery_health(self, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recent discovery health snapshots."""
        rows = await self._pool.fetch(Q.GET_DISCOVERY_HEALTH, limit)
        return [dict(row) for row in rows]

    async def insert_flip_ticket(
        self,
        event: FlippeningEvent,
        entry: EntrySignal,
        *,
        min_expected_profit_usd: Decimal = Decimal("1.00"),
        market_slug: str = "",
    ) -> None:
        """Persist a flippening execution ticket (skips if below threshold).

        Args:
            event: Flippening event.
            entry: Entry signal.
            min_expected_profit_usd: Minimum profit to persist ticket.
            market_slug: Polymarket slug for building market URL.
        """
        expected_profit = entry.expected_profit_pct * entry.suggested_size_usd
        if expected_profit < min_expected_profit_usd:
            logger.debug("flip_ticket_skipped_below_min_profit", event_id=event.id)
            return
        market_url = f"https://polymarket.com/event/{market_slug}" if market_slug else ""
        leg_1 = json.dumps(
            {
                "action": f"BUY {entry.side.upper()}",
                "market_id": event.market_id,
                "market_title": event.market_title,
                "market_url": market_url,
                "price": str(entry.entry_price),
                "sport": event.sport,
            }
        )
        leg_2 = json.dumps(
            {
                "action": f"SELL {entry.side.upper()} at target",
                "target_price": str(entry.target_exit_price),
                "stop_loss": str(entry.stop_loss_price),
                "max_hold_minutes": entry.max_hold_minutes,
            }
        )
        await self._pool.execute(
            Q.INSERT_FLIP_TICKET,
            event.id,
            leg_1,
            leg_2,
            entry.suggested_size_usd,
            expected_profit,
            "pending",
            "flippening",
            event.category or event.sport,
            event.category_type,
        )
        logger.info("flip_ticket_created", event_id=event.id, side=entry.side)

    async def insert_ws_telemetry(self, snapshot: dict[str, Any]) -> None:
        """Persist a WS telemetry snapshot."""
        await self._pool.execute(
            Q.INSERT_WS_TELEMETRY,
            snapshot.get("snapshot_time", datetime.now(tz=timezone.utc)),
            snapshot.get("cum_received", 0),
            snapshot.get("cum_parsed_ok", 0),
            snapshot.get("cum_parse_failed", 0),
            snapshot.get("cum_ignored", 0),
            snapshot.get("schema_match_rate", 1.0),
            snapshot.get("book_cache_hit_rate", 0.0),
            snapshot.get("connection_state", "connected"),
        )

    async def get_ws_telemetry(self, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recent WS telemetry snapshots."""
        rows = await self._pool.fetch(Q.GET_WS_TELEMETRY, limit)
        return [dict(row) for row in rows]

    async def get_ws_telemetry_latest(self) -> dict[str, Any] | None:
        """Fetch the most recent WS telemetry snapshot."""
        rows = await self._pool.fetch(WQ.GET_WS_TELEMETRY_LATEST)
        return dict(rows[0]) if rows else None

    async def get_ws_telemetry_history(
        self,
        since: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch WS telemetry snapshots since ``since``."""
        rows = await self._pool.fetch(WQ.GET_WS_TELEMETRY_HISTORY, since)
        return [dict(row) for row in rows]

    async def get_ws_telemetry_events(
        self,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch stall/reconnect events derived from snapshots."""
        rows = await self._pool.fetch(WQ.GET_WS_TELEMETRY_EVENTS, limit)
        return [dict(row) for row in rows]

    async def get_discovery_health_history(
        self,
        since: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch discovery health snapshots since ``since``."""
        rows = await self._pool.fetch(Q.SELECT_DISCOVERY_HEALTH_HISTORY, since)
        return [dict(row) for row in rows]

    async def get_discovery_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recent degradation alerts, newest first."""
        rows = await self._pool.fetch(Q.SELECT_DISCOVERY_ALERTS, limit)
        return [dict(row) for row in rows]

    async def insert_discovery_alert(
        self,
        alert_text: str,
        category: str,
    ) -> None:
        """Persist a discovery degradation alert."""
        await self._pool.execute(
            Q.INSERT_DISCOVERY_ALERT,
            alert_text,
            category,
            datetime.now(tz=timezone.utc),
        )

    async def resolve_discovery_alerts(self, category: str) -> None:
        """Mark open alerts for a category as resolved."""
        await self._pool.execute(
            Q.RESOLVE_DISCOVERY_ALERT,
            category,
            datetime.now(tz=timezone.utc),
        )
