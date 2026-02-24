"""T052 - Integration tests for execution ticket persistence lifecycle.

Tests for get_pending_tickets, update_ticket_status, and expire_stale_tickets.
Requires a live PostgreSQL instance (DATABASE_URL).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from arb_scanner.models.arbitrage import ArbOpportunity, ExecutionTicket
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult
from arb_scanner.storage.repository import Repository

from .conftest import requires_postgres

if TYPE_CHECKING:
    import asyncpg

_NOW = datetime.now(tz=timezone.utc)
_FUTURE = _NOW + timedelta(hours=48)


def _make_market(venue: Venue, event_id: str) -> Market:
    """Build a test Market for a given venue and event_id."""
    return Market(
        venue=venue,
        event_id=event_id,
        title=f"Test market {event_id}",
        description="Integration test market",
        resolution_criteria="Resolves YES if condition met",
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.45"),
        no_bid=Decimal("0.50"),
        no_ask=Decimal("0.55"),
        volume_24h=Decimal("5000"),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )


def _make_match(poly_id: str, kalshi_id: str) -> MatchResult:
    """Build a test MatchResult."""
    return MatchResult(
        poly_event_id=poly_id,
        kalshi_event_id=kalshi_id,
        match_confidence=0.92,
        resolution_equivalent=True,
        resolution_risks=["minor wording difference"],
        safe_to_arb=True,
        reasoning="Same underlying event.",
        matched_at=_NOW,
        ttl_expires=_FUTURE,
    )


def _make_opportunity(
    match: MatchResult,
    poly_market: Market,
    kalshi_market: Market,
) -> ArbOpportunity:
    """Build a test ArbOpportunity."""
    return ArbOpportunity(
        match=match,
        poly_market=poly_market,
        kalshi_market=kalshi_market,
        buy_venue=Venue.KALSHI,
        sell_venue=Venue.POLYMARKET,
        cost_per_contract=Decimal("0.87"),
        gross_profit=Decimal("0.13"),
        net_profit=Decimal("0.05"),
        net_spread_pct=Decimal("0.05"),
        max_size=Decimal("200"),
        depth_risk=False,
        detected_at=_NOW,
    )


async def _insert_ticket_with_parent(
    repo: Repository,
    pool: asyncpg.Pool[asyncpg.Record],
    suffix: str,
    *,
    status: str = "pending",
) -> str:
    """Insert a ticket with its parent opportunity. Returns arb_id."""
    poly = _make_market(Venue.POLYMARKET, f"poly-lc-{suffix}")
    kalshi = _make_market(Venue.KALSHI, f"kalshi-lc-{suffix}")
    match = _make_match(f"poly-lc-{suffix}", f"kalshi-lc-{suffix}")
    opp = _make_opportunity(match, poly, kalshi)
    await repo.insert_opportunity(opp)
    ticket = ExecutionTicket(
        arb_id=opp.id,
        leg_1={"venue": "kalshi", "side": "buy"},
        leg_2={"venue": "polymarket", "side": "sell"},
        expected_cost=Decimal("0.87"),
        expected_profit=Decimal("0.05"),
        status=status,
    )
    await repo.insert_ticket(ticket)
    return opp.id


# ---------------------------------------------------------------------------
# get_pending_tickets
# ---------------------------------------------------------------------------


@requires_postgres
class TestGetPendingTickets:
    """Tests for retrieving pending execution tickets."""

    @pytest.mark.asyncio()
    async def test_returns_only_pending(
        self,
        db_pool: asyncpg.Pool[asyncpg.Record],
    ) -> None:
        """Verify only tickets with status='pending' are returned."""
        repo = Repository(db_pool)
        await _insert_ticket_with_parent(repo, db_pool, "p1", status="pending")
        await _insert_ticket_with_parent(repo, db_pool, "p2", status="pending")

        arb_id_approved = await _insert_ticket_with_parent(
            repo,
            db_pool,
            "p3",
            status="pending",
        )
        await repo.update_ticket_status(arb_id_approved, "approved")

        pending = await repo.get_pending_tickets()
        statuses = {t["status"] for t in pending}
        assert statuses == {"pending"}
        assert len(pending) == 2

    @pytest.mark.asyncio()
    async def test_empty_when_no_pending(
        self,
        db_pool: asyncpg.Pool[asyncpg.Record],
    ) -> None:
        """Verify empty list when no pending tickets exist."""
        repo = Repository(db_pool)
        pending = await repo.get_pending_tickets()
        assert pending == []


# ---------------------------------------------------------------------------
# update_ticket_status
# ---------------------------------------------------------------------------


@requires_postgres
class TestUpdateTicketStatus:
    """Tests for changing execution ticket status."""

    @pytest.mark.asyncio()
    async def test_changes_status(
        self,
        db_pool: asyncpg.Pool[asyncpg.Record],
    ) -> None:
        """Verify update_ticket_status changes the stored status."""
        repo = Repository(db_pool)
        arb_id = await _insert_ticket_with_parent(repo, db_pool, "u1")

        await repo.update_ticket_status(arb_id, "approved")

        row = await db_pool.fetchrow(
            "SELECT status FROM execution_tickets WHERE arb_id=$1",
            arb_id,
        )
        assert row is not None
        assert row["status"] == "approved"

    @pytest.mark.asyncio()
    async def test_status_to_expired(
        self,
        db_pool: asyncpg.Pool[asyncpg.Record],
    ) -> None:
        """Verify a ticket can be set to expired status."""
        repo = Repository(db_pool)
        arb_id = await _insert_ticket_with_parent(repo, db_pool, "u2")

        await repo.update_ticket_status(arb_id, "expired")

        row = await db_pool.fetchrow(
            "SELECT status FROM execution_tickets WHERE arb_id=$1",
            arb_id,
        )
        assert row is not None
        assert row["status"] == "expired"


# ---------------------------------------------------------------------------
# expire_stale_tickets
# ---------------------------------------------------------------------------


@requires_postgres
class TestExpireStaleTickets:
    """Tests for bulk-expiring old pending tickets."""

    @pytest.mark.asyncio()
    async def test_expires_old_tickets(
        self,
        db_pool: asyncpg.Pool[asyncpg.Record],
    ) -> None:
        """Verify tickets older than max_age_hours are expired."""
        repo = Repository(db_pool)
        arb_id = await _insert_ticket_with_parent(repo, db_pool, "e1")

        # Backdate the ticket's created_at to 48 hours ago
        await db_pool.execute(
            "UPDATE execution_tickets SET created_at = $2 WHERE arb_id = $1",
            arb_id,
            _NOW - timedelta(hours=48),
        )

        count = await repo.expire_stale_tickets(max_age_hours=24)
        assert count == 1

        row = await db_pool.fetchrow(
            "SELECT status FROM execution_tickets WHERE arb_id=$1",
            arb_id,
        )
        assert row is not None
        assert row["status"] == "expired"

    @pytest.mark.asyncio()
    async def test_does_not_expire_recent(
        self,
        db_pool: asyncpg.Pool[asyncpg.Record],
    ) -> None:
        """Verify recent tickets are not expired."""
        repo = Repository(db_pool)
        await _insert_ticket_with_parent(repo, db_pool, "e2")

        count = await repo.expire_stale_tickets(max_age_hours=24)
        assert count == 0

    @pytest.mark.asyncio()
    async def test_skips_already_approved(
        self,
        db_pool: asyncpg.Pool[asyncpg.Record],
    ) -> None:
        """Verify approved tickets are not expired even if old."""
        repo = Repository(db_pool)
        arb_id = await _insert_ticket_with_parent(repo, db_pool, "e3")
        await repo.update_ticket_status(arb_id, "approved")

        # Backdate the ticket
        await db_pool.execute(
            "UPDATE execution_tickets SET created_at = $2 WHERE arb_id = $1",
            arb_id,
            _NOW - timedelta(hours=48),
        )

        count = await repo.expire_stale_tickets(max_age_hours=24)
        assert count == 0
