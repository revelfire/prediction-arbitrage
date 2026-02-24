"""T019 - Integration tests for the repository layer.

These tests require a live PostgreSQL instance and will be skipped
when the DATABASE_URL environment variable is not set.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from arb_scanner.models.arbitrage import ArbOpportunity, ExecutionTicket
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult
from arb_scanner.models.scan_log import ScanLog
from arb_scanner.storage.repository import Repository

from .conftest import requires_postgres

if TYPE_CHECKING:
    import asyncpg

_NOW = datetime.now(tz=timezone.utc)
_FUTURE = _NOW + timedelta(hours=48)
_PAST = _NOW - timedelta(hours=48)


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


def _make_match(
    poly_id: str,
    kalshi_id: str,
    *,
    ttl_expires: datetime | None = None,
) -> MatchResult:
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
        ttl_expires=ttl_expires or _FUTURE,
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


# ---------------------------------------------------------------------------
# Market CRUD
# ---------------------------------------------------------------------------


@requires_postgres
class TestUpsertMarket:
    """Tests for upserting market records."""

    @pytest.mark.asyncio()
    async def test_insert_and_update(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify a market can be inserted and then updated via upsert."""
        repo = Repository(db_pool)
        market = _make_market(Venue.POLYMARKET, "poly-integ-001")
        await repo.upsert_market(market)

        # Update with a new title
        updated = market.model_copy(update={"title": "Updated title"})
        await repo.upsert_market(updated)

        row = await db_pool.fetchrow(
            "SELECT title FROM markets WHERE venue=$1 AND event_id=$2",
            "polymarket",
            "poly-integ-001",
        )
        assert row is not None
        assert row["title"] == "Updated title"


# ---------------------------------------------------------------------------
# MatchResult CRUD and cache
# ---------------------------------------------------------------------------


@requires_postgres
class TestUpsertMatchResult:
    """Tests for upserting match result records."""

    @pytest.mark.asyncio()
    async def test_insert_match(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify a match result can be inserted."""
        repo = Repository(db_pool)
        match = _make_match("poly-m1", "kalshi-m1")
        await repo.upsert_match_result(match)

        row = await db_pool.fetchrow(
            "SELECT * FROM match_results WHERE poly_event_id=$1 AND kalshi_event_id=$2",
            "poly-m1",
            "kalshi-m1",
        )
        assert row is not None
        assert row["safe_to_arb"] is True

    @pytest.mark.asyncio()
    async def test_upsert_overwrites(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify upserting updates the existing match record."""
        repo = Repository(db_pool)
        match = _make_match("poly-m2", "kalshi-m2")
        await repo.upsert_match_result(match)

        updated = MatchResult(
            poly_event_id="poly-m2",
            kalshi_event_id="kalshi-m2",
            match_confidence=0.50,
            resolution_equivalent=True,
            resolution_risks=[],
            safe_to_arb=False,
            reasoning="Updated reasoning.",
            matched_at=_NOW,
            ttl_expires=_FUTURE,
        )
        await repo.upsert_match_result(updated)

        row = await db_pool.fetchrow(
            "SELECT match_confidence, safe_to_arb FROM match_results "
            "WHERE poly_event_id=$1 AND kalshi_event_id=$2",
            "poly-m2",
            "kalshi-m2",
        )
        assert row is not None
        assert row["match_confidence"] == 0.50
        assert row["safe_to_arb"] is False


@requires_postgres
class TestGetCachedMatch:
    """Tests for the match result cache retrieval."""

    @pytest.mark.asyncio()
    async def test_returns_valid_cache(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify a non-expired match is returned from cache."""
        repo = Repository(db_pool)
        match = _make_match("poly-c1", "kalshi-c1", ttl_expires=_FUTURE)
        await repo.upsert_match_result(match)

        cached = await repo.get_cached_match("poly-c1", "kalshi-c1")
        assert cached is not None
        assert cached.poly_event_id == "poly-c1"

    @pytest.mark.asyncio()
    async def test_expired_cache_returns_none(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify an expired match (ttl in the past) returns None."""
        repo = Repository(db_pool)
        match = _make_match("poly-c2", "kalshi-c2", ttl_expires=_PAST)
        await repo.upsert_match_result(match)

        cached = await repo.get_cached_match("poly-c2", "kalshi-c2")
        assert cached is None

    @pytest.mark.asyncio()
    async def test_nonexistent_match_returns_none(
        self, db_pool: asyncpg.Pool[asyncpg.Record]
    ) -> None:
        """Verify querying a non-existent pair returns None."""
        repo = Repository(db_pool)
        cached = await repo.get_cached_match("no-such-poly", "no-such-kalshi")
        assert cached is None


# ---------------------------------------------------------------------------
# ArbOpportunity CRUD
# ---------------------------------------------------------------------------


@requires_postgres
class TestInsertOpportunity:
    """Tests for inserting arbitrage opportunity records."""

    @pytest.mark.asyncio()
    async def test_insert_opportunity(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify an arb opportunity can be inserted."""
        repo = Repository(db_pool)
        poly = _make_market(Venue.POLYMARKET, "poly-arb-1")
        kalshi = _make_market(Venue.KALSHI, "kalshi-arb-1")
        match = _make_match("poly-arb-1", "kalshi-arb-1")
        opp = _make_opportunity(match, poly, kalshi)

        await repo.insert_opportunity(opp)

        row = await db_pool.fetchrow("SELECT * FROM arb_opportunities WHERE id=$1", opp.id)
        assert row is not None
        assert row["buy_venue"] == "kalshi"
        assert row["sell_venue"] == "polymarket"


@requires_postgres
class TestGetRecentOpportunities:
    """Tests for fetching recent arb opportunities."""

    @pytest.mark.asyncio()
    async def test_returns_limited_results(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify the limit parameter constrains results."""
        repo = Repository(db_pool)

        for i in range(5):
            poly = _make_market(Venue.POLYMARKET, f"poly-rec-{i}")
            kalshi = _make_market(Venue.KALSHI, f"kalshi-rec-{i}")
            match = _make_match(f"poly-rec-{i}", f"kalshi-rec-{i}")
            opp = _make_opportunity(match, poly, kalshi)
            await repo.insert_opportunity(opp)

        results = await repo.get_recent_opportunities(limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio()
    async def test_returns_empty_when_none(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify an empty list is returned when no opportunities exist."""
        repo = Repository(db_pool)
        results = await repo.get_recent_opportunities()
        assert results == []


# ---------------------------------------------------------------------------
# ExecutionTicket CRUD
# ---------------------------------------------------------------------------


@requires_postgres
class TestInsertTicket:
    """Tests for inserting execution ticket records."""

    @pytest.mark.asyncio()
    async def test_insert_ticket(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify a ticket can be inserted (requires a parent opportunity)."""
        repo = Repository(db_pool)

        # Insert the parent opportunity first (FK constraint)
        poly = _make_market(Venue.POLYMARKET, "poly-tk-1")
        kalshi = _make_market(Venue.KALSHI, "kalshi-tk-1")
        match = _make_match("poly-tk-1", "kalshi-tk-1")
        opp = _make_opportunity(match, poly, kalshi)
        await repo.insert_opportunity(opp)

        ticket = ExecutionTicket(
            arb_id=opp.id,
            leg_1={"venue": "kalshi", "side": "buy"},
            leg_2={"venue": "polymarket", "side": "sell"},
            expected_cost=Decimal("0.87"),
            expected_profit=Decimal("0.05"),
            status="pending",
        )
        await repo.insert_ticket(ticket)

        row = await db_pool.fetchrow("SELECT * FROM execution_tickets WHERE arb_id=$1", opp.id)
        assert row is not None
        assert row["status"] == "pending"


# ---------------------------------------------------------------------------
# ScanLog CRUD
# ---------------------------------------------------------------------------


@requires_postgres
class TestInsertScanLog:
    """Tests for inserting scan log records."""

    @pytest.mark.asyncio()
    async def test_insert_scan_log(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify a scan log can be inserted."""
        repo = Repository(db_pool)
        log = ScanLog(
            id="scan-integ-001",
            started_at=_NOW,
            completed_at=_NOW,
            poly_markets_fetched=100,
            kalshi_markets_fetched=80,
            candidate_pairs=20,
            llm_evaluations=10,
            opportunities_found=3,
            errors=["timeout on one request"],
        )
        await repo.insert_scan_log(log)

        row = await db_pool.fetchrow("SELECT * FROM scan_logs WHERE id=$1", "scan-integ-001")
        assert row is not None
        assert row["poly_markets_fetched"] == 100
        assert row["opportunities_found"] == 3


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------


@requires_postgres
class TestMigrations:
    """Tests for the migration runner."""

    @pytest.mark.asyncio()
    async def test_migrations_are_idempotent(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify running migrations twice does not error."""
        from arb_scanner.storage.migrations_runner import run_migrations

        # Migrations already ran via fixture; running again should apply none
        newly_applied = await run_migrations(db_pool)
        assert newly_applied == []

    @pytest.mark.asyncio()
    async def test_migrations_tracking_table_exists(
        self, db_pool: asyncpg.Pool[asyncpg.Record]
    ) -> None:
        """Verify the _migrations tracking table contains records."""
        rows = await db_pool.fetch("SELECT filename FROM _migrations")
        filenames = {row["filename"] for row in rows}
        assert "001_create_markets.sql" in filenames
        assert "007_create_migrations_table.sql" in filenames
