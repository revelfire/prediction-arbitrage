"""Repository for persisting and querying arb scanner data."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import asyncpg

from arb_scanner.models.arbitrage import ArbOpportunity, ExecutionTicket
from arb_scanner.models.market import Market
from arb_scanner.models.matching import MatchResult
from arb_scanner.models.scan_log import ScanLog
from arb_scanner.storage import _queries as Q


class Repository:
    """Data access layer for all arb scanner database operations."""

    def __init__(self, pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Initialize with an asyncpg connection pool.

        Args:
            pool: An active asyncpg connection pool.
        """
        self._pool = pool

    # ------------------------------------------------------------------
    # Market operations
    # ------------------------------------------------------------------

    async def upsert_market(self, market: Market) -> None:
        """Insert or update a market record.

        Args:
            market: The Market model to persist.
        """
        await self._pool.execute(
            Q.UPSERT_MARKET,
            market.venue.value,
            market.event_id,
            market.title,
            market.description,
            market.resolution_criteria,
            market.yes_bid,
            market.yes_ask,
            market.no_bid,
            market.no_ask,
            market.volume_24h,
            market.expiry,
            market.fees_pct,
            market.fee_model,
            market.last_updated,
            json.dumps(market.raw_data, default=str),
        )

    async def update_market_embedding(
        self,
        venue: str,
        event_id: str,
        embedding: list[float],
    ) -> None:
        """Persist a 512-dim title embedding vector for a market.

        Args:
            venue: Venue identifier (e.g. ``"polymarket"``).
            event_id: Market event identifier.
            embedding: Float vector from the embedding model.
        """
        await self._pool.execute(
            Q.UPDATE_MARKET_EMBEDDING,
            venue,
            event_id,
            embedding,
        )

    async def update_market_embedding_384(
        self,
        venue: str,
        event_id: str,
        embedding: list[float],
    ) -> None:
        """Persist a 384-dim title embedding vector for a market.

        Args:
            venue: Venue identifier (e.g. ``"polymarket"``).
            event_id: Market event identifier.
            embedding: Float vector from the embedding model.
        """
        await self._pool.execute(
            Q.UPDATE_MARKET_EMBEDDING_384,
            venue,
            event_id,
            embedding,
        )

    async def get_cached_embeddings(
        self,
        pairs: list[tuple[str, str]],
        dimensions: int,
    ) -> dict[str, list[float]]:
        """Load cached embeddings from pgvector for the given markets.

        Args:
            pairs: List of ``(venue, event_id)`` tuples to look up.
            dimensions: Embedding dimensions (384 or 512) to pick the
                correct column.

        Returns:
            Dict mapping ``"venue:event_id"`` to float vectors.
        """
        if not pairs:
            return {}

        query = Q.GET_CACHED_EMBEDDINGS_384 if dimensions == 384 else Q.GET_CACHED_EMBEDDINGS_512
        venues = [p[0] for p in pairs]
        event_ids = [p[1] for p in pairs]
        rows = await self._pool.fetch(query, venues, event_ids)
        result: dict[str, list[float]] = {}
        for row in rows:
            col = "title_embedding_384" if dimensions == 384 else "title_embedding"
            vec = row[col]
            if vec is not None:
                result[f"{row['venue']}:{row['event_id']}"] = list(vec)
        return result

    # ------------------------------------------------------------------
    # Match result operations
    # ------------------------------------------------------------------

    async def upsert_match_result(self, match: MatchResult) -> None:
        """Insert or update a match result record.

        Args:
            match: The MatchResult model to persist.
        """
        await self._pool.execute(
            Q.UPSERT_MATCH,
            match.poly_event_id,
            match.kalshi_event_id,
            match.match_confidence,
            match.resolution_equivalent,
            json.dumps(match.resolution_risks),
            match.safe_to_arb,
            match.reasoning,
            match.matched_at,
            match.ttl_expires,
        )

    async def get_cached_match(
        self,
        poly_id: str,
        kalshi_id: str,
    ) -> MatchResult | None:
        """Retrieve a cached match result if not expired.

        Args:
            poly_id: Polymarket event ID.
            kalshi_id: Kalshi event ID.

        Returns:
            The cached MatchResult, or None if not found or expired.
        """
        now = datetime.now(tz=timezone.utc)
        row = await self._pool.fetchrow(Q.GET_CACHED_MATCH, poly_id, kalshi_id, now)
        if row is None:
            return None
        return _row_to_match_result(row)

    async def get_all_matches(
        self,
        *,
        include_expired: bool = False,
        min_confidence: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Retrieve all match results with optional filtering.

        Args:
            include_expired: When True, include matches past their TTL.
            min_confidence: Minimum match confidence to include.

        Returns:
            List of match result records as dictionaries.
        """
        rows = await self._pool.fetch(Q.GET_ALL_MATCHES, include_expired, min_confidence)
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Opportunity operations
    # ------------------------------------------------------------------

    async def insert_opportunity(self, opp: ArbOpportunity) -> None:
        """Insert an arbitrage opportunity record.

        Args:
            opp: The ArbOpportunity model to persist.
        """
        await self._pool.execute(
            Q.INSERT_OPP,
            opp.id,
            opp.match.poly_event_id,
            opp.match.kalshi_event_id,
            opp.buy_venue.value,
            opp.sell_venue.value,
            opp.cost_per_contract,
            opp.gross_profit,
            opp.net_profit,
            opp.net_spread_pct,
            opp.max_size,
            opp.annualized_return,
            opp.depth_risk,
            opp.detected_at,
        )

    async def get_recent_opportunities(self, limit: int = 10) -> list[dict[str, Any]]:
        """Fetch the most recent arbitrage opportunities.

        Args:
            limit: Maximum number of results to return.

        Returns:
            List of opportunity records as dictionaries.
        """
        rows = await self._pool.fetch(Q.GET_RECENT_OPPS, limit)
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Ticket operations
    # ------------------------------------------------------------------

    async def insert_ticket(self, ticket: ExecutionTicket) -> None:
        """Insert an execution ticket record.

        Args:
            ticket: The ExecutionTicket model to persist.
        """
        await self._pool.execute(
            Q.INSERT_TICKET,
            ticket.arb_id,
            json.dumps(ticket.leg_1, default=str),
            json.dumps(ticket.leg_2, default=str),
            ticket.expected_cost,
            ticket.expected_profit,
            ticket.status,
        )

    async def get_pending_arb_pair_ids(self) -> set[tuple[str, str]]:
        """Return (poly_event_id, kalshi_event_id) pairs that have a pending arb ticket.

        Returns:
            Set of (poly_event_id, kalshi_event_id) tuples.
        """
        rows = await self._pool.fetch(Q.GET_PENDING_ARB_PAIR_IDS)
        return {(r["poly_event_id"], r["kalshi_event_id"]) for r in rows}

    async def get_pending_tickets(self) -> list[dict[str, Any]]:
        """Fetch all execution tickets with pending status.

        Returns:
            List of pending ticket records as dictionaries.
        """
        rows = await self._pool.fetch(Q.GET_PENDING_TICKETS)
        return [dict(row) for row in rows]

    async def get_tickets_by_status(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch tickets filtered by status.

        Args:
            status: Filter to this status, or None for all.
            limit: Maximum number of results.

        Returns:
            List of ticket records as dictionaries.
        """
        rows = await self._pool.fetch(Q.GET_TICKETS_BY_STATUS, status, limit)
        return [dict(row) for row in rows]

    async def get_ticket_detail(self, arb_id: str) -> dict[str, Any] | None:
        """Fetch a single ticket with full opportunity and market data.

        Args:
            arb_id: The arbitrage opportunity ID.

        Returns:
            Ticket detail dict or None if not found.
        """
        row = await self._pool.fetchrow(Q.GET_TICKET_DETAIL, arb_id)
        if row is None:
            return None
        return dict(row)

    async def update_ticket_status(self, arb_id: str, status: str) -> None:
        """Update the status of an execution ticket.

        Args:
            arb_id: The arbitrage opportunity ID referencing the ticket.
            status: New status value (pending, approved, or expired).
        """
        await self._pool.execute(Q.UPDATE_TICKET_STATUS, arb_id, status)

    async def expire_stale_tickets(self, max_age_hours: int = 24) -> int:
        """Expire pending tickets older than the given threshold.

        Args:
            max_age_hours: Maximum age in hours before a ticket is expired.

        Returns:
            Number of tickets that were expired.
        """
        rows = await self._pool.fetch(Q.EXPIRE_STALE_TICKETS, max_age_hours)
        return len(rows)

    async def get_tickets_with_opportunities(
        self,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch tickets joined with their parent opportunities.

        Args:
            limit: Maximum number of results to return.

        Returns:
            List of ticket+opportunity records as dictionaries.
        """
        rows = await self._pool.fetch(Q.GET_TICKETS_WITH_OPPS, limit)
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Scan log operations
    # ------------------------------------------------------------------

    async def insert_scan_log(self, log: ScanLog) -> None:
        """Insert a scan log record.

        Args:
            log: The ScanLog model to persist.
        """
        await self._pool.execute(
            Q.INSERT_SCAN_LOG,
            log.id,
            log.started_at,
            log.completed_at,
            log.poly_markets_fetched,
            log.kalshi_markets_fetched,
            log.candidate_pairs,
            log.llm_evaluations,
            log.opportunities_found,
            json.dumps(log.errors),
        )

    async def upsert_scan_log(self, log: ScanLog) -> None:
        """Insert or update a scan log record.

        Args:
            log: The ScanLog model to persist or update.
        """
        await self._pool.execute(
            Q.UPSERT_SCAN_LOG,
            log.id,
            log.started_at,
            log.completed_at,
            log.poly_markets_fetched,
            log.kalshi_markets_fetched,
            log.candidate_pairs,
            log.llm_evaluations,
            log.opportunities_found,
            json.dumps(log.errors),
        )


def _row_to_match_result(row: asyncpg.Record) -> MatchResult:
    """Convert an asyncpg Record to a MatchResult model."""
    risks = row["resolution_risks"]
    if isinstance(risks, str):
        risks = json.loads(risks)
    return MatchResult(
        poly_event_id=row["poly_event_id"],
        kalshi_event_id=row["kalshi_event_id"],
        match_confidence=row["match_confidence"],
        resolution_equivalent=row["resolution_equivalent"],
        resolution_risks=risks,
        safe_to_arb=row["safe_to_arb"],
        reasoning=row["reasoning"],
        matched_at=row["matched_at"],
        ttl_expires=row["ttl_expires"],
    )
