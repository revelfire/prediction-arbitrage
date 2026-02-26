"""Repository for flippening engine persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
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

    async def insert_discovery_health(self, snapshot: dict[str, Any]) -> None:
        """Persist a discovery health snapshot.

        Args:
            snapshot: Discovery health dict with keys matching the table columns.
        """
        await self._pool.execute(
            Q.INSERT_DISCOVERY_HEALTH,
            snapshot.get("cycle_timestamp", datetime.now(tz=timezone.utc)),
            snapshot["total_scanned"],
            snapshot["sports_found"],
            snapshot["hit_rate"],
            json.dumps(snapshot["by_sport"]),
            snapshot.get("overrides_applied", 0),
            snapshot.get("exclusions_applied", 0),
            snapshot.get("unclassified_candidates", 0),
        )

    async def get_discovery_health(self, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recent discovery health snapshots.

        Args:
            limit: Maximum number of results.

        Returns:
            List of discovery health dicts.
        """
        rows = await self._pool.fetch(Q.GET_DISCOVERY_HEALTH, limit)
        return [dict(row) for row in rows]

    async def insert_flip_ticket(
        self,
        event: FlippeningEvent,
        entry: EntrySignal,
    ) -> None:
        """Persist a flippening execution ticket.

        Args:
            event: The flippening event.
            entry: The entry signal.
        """
        leg_1 = json.dumps(
            {
                "action": f"BUY {entry.side.upper()}",
                "market_id": event.market_id,
                "market_title": event.market_title,
                "price": str(entry.entry_price),
                "sport": event.sport,
            },
        )
        leg_2 = json.dumps(
            {
                "action": f"SELL {entry.side.upper()} at target",
                "target_price": str(entry.target_exit_price),
                "stop_loss": str(entry.stop_loss_price),
                "max_hold_minutes": entry.max_hold_minutes,
            },
        )
        await self._pool.execute(
            Q.INSERT_FLIP_TICKET,
            event.id,
            leg_1,
            leg_2,
            entry.entry_price * entry.suggested_size_usd,
            entry.expected_profit_pct * entry.suggested_size_usd,
            "pending",
            "flippening",
        )
        logger.info(
            "flip_ticket_created",
            event_id=event.id,
            side=entry.side,
        )

    async def insert_ws_telemetry(self, snapshot: dict[str, Any]) -> None:
        """Persist a WS telemetry snapshot.

        Args:
            snapshot: Telemetry dict with counter values.
        """
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
        """Fetch recent WS telemetry snapshots.

        Args:
            limit: Maximum number of results.

        Returns:
            List of WS telemetry dicts.
        """
        rows = await self._pool.fetch(Q.GET_WS_TELEMETRY, limit)
        return [dict(row) for row in rows]
