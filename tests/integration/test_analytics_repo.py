"""Integration tests for the AnalyticsRepository.

These tests require a live PostgreSQL instance and will be skipped
when the DATABASE_URL environment variable is not set.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from arb_scanner.models.analytics import (
    HourlyBucket,
    PairSummary,
    ScanHealthSummary,
    SpreadSnapshot,
)
from arb_scanner.models.arbitrage import ArbOpportunity, ExecutionTicket
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult
from arb_scanner.models.scan_log import ScanLog
from arb_scanner.storage.analytics_repository import AnalyticsRepository
from arb_scanner.storage.repository import Repository

from .conftest import requires_postgres

if TYPE_CHECKING:
    import asyncpg

_NOW = datetime.now(tz=timezone.utc)
_FUTURE = _NOW + timedelta(hours=48)
_PAST = _NOW - timedelta(hours=48)
_FAR_PAST = _NOW - timedelta(days=7)


# ------------------------------------------------------------------
# Test helpers
# ------------------------------------------------------------------


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
    *,
    detected_at: datetime | None = None,
    net_spread_pct: Decimal = Decimal("0.05"),
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
        net_spread_pct=net_spread_pct,
        max_size=Decimal("200"),
        depth_risk=False,
        detected_at=detected_at or _NOW,
    )


async def _seed_opportunity(
    repo: Repository,
    poly_id: str,
    kalshi_id: str,
    *,
    detected_at: datetime | None = None,
    net_spread_pct: Decimal = Decimal("0.05"),
) -> ArbOpportunity:
    """Insert a full opportunity chain (match + opp) and return the opp."""
    match = _make_match(poly_id, kalshi_id)
    await repo.upsert_match_result(match)
    poly = _make_market(Venue.POLYMARKET, poly_id)
    kalshi = _make_market(Venue.KALSHI, kalshi_id)
    opp = _make_opportunity(
        match,
        poly,
        kalshi,
        detected_at=detected_at,
        net_spread_pct=net_spread_pct,
    )
    await repo.insert_opportunity(opp)
    return opp


async def _seed_scan_log(
    repo: Repository,
    scan_id: str,
    *,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    """Insert a scan log record."""
    start = started_at or _NOW
    end = completed_at or (start + timedelta(seconds=30))
    log = ScanLog(
        id=scan_id,
        started_at=start,
        completed_at=end,
        poly_markets_fetched=50,
        kalshi_markets_fetched=40,
        candidate_pairs=10,
        llm_evaluations=5,
        opportunities_found=2,
        errors=[],
    )
    await repo.insert_scan_log(log)


# ------------------------------------------------------------------
# Spread history (T006)
# ------------------------------------------------------------------


@requires_postgres
class TestGetSpreadHistory:
    """Tests for get_spread_history."""

    @pytest.mark.asyncio()
    async def test_returns_spread_snapshots(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify spread history returns SpreadSnapshot models."""
        repo = Repository(db_pool)
        analytics = AnalyticsRepository(db_pool)

        await _seed_opportunity(repo, "poly-sh-1", "kalshi-sh-1")

        results = await analytics.get_spread_history("poly-sh-1", "kalshi-sh-1", _PAST)
        assert len(results) == 1
        assert isinstance(results[0], SpreadSnapshot)
        assert results[0].net_spread_pct == Decimal("0.05")

    @pytest.mark.asyncio()
    async def test_empty_for_unknown_pair(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify empty list is returned for a non-existent pair."""
        analytics = AnalyticsRepository(db_pool)
        results = await analytics.get_spread_history("no-poly", "no-kalshi", _PAST)
        assert results == []


# ------------------------------------------------------------------
# Pair summaries (T007)
# ------------------------------------------------------------------


@requires_postgres
class TestGetPairSummaries:
    """Tests for get_pair_summaries."""

    @pytest.mark.asyncio()
    async def test_returns_pair_summaries(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify pair summaries are correctly aggregated."""
        repo = Repository(db_pool)
        analytics = AnalyticsRepository(db_pool)

        await _seed_opportunity(repo, "poly-ps-1", "kalshi-ps-1", net_spread_pct=Decimal("0.08"))
        await _seed_opportunity(repo, "poly-ps-2", "kalshi-ps-2", net_spread_pct=Decimal("0.03"))

        results = await analytics.get_pair_summaries(_PAST)
        assert len(results) == 2
        assert isinstance(results[0], PairSummary)
        # Ordered by peak_spread DESC
        assert results[0].peak_spread >= results[1].peak_spread
        assert results[0].total_detections == 1

    @pytest.mark.asyncio()
    async def test_empty_pair_summaries(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify empty list when no opportunities exist."""
        analytics = AnalyticsRepository(db_pool)
        results = await analytics.get_pair_summaries(_PAST)
        assert results == []


# ------------------------------------------------------------------
# Hourly buckets (T008)
# ------------------------------------------------------------------


@requires_postgres
class TestGetHourlyBuckets:
    """Tests for get_hourly_buckets."""

    @pytest.mark.asyncio()
    async def test_returns_hourly_buckets(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify hourly buckets aggregate correctly."""
        repo = Repository(db_pool)
        analytics = AnalyticsRepository(db_pool)

        await _seed_opportunity(repo, "poly-hb-1", "kalshi-hb-1")

        results = await analytics.get_hourly_buckets(_PAST)
        assert len(results) >= 1
        assert isinstance(results[0], HourlyBucket)
        assert results[0].detection_count >= 1

    @pytest.mark.asyncio()
    async def test_empty_hourly_buckets(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify empty list when no opportunities exist."""
        analytics = AnalyticsRepository(db_pool)
        results = await analytics.get_hourly_buckets(_PAST)
        assert results == []


# ------------------------------------------------------------------
# Scan health (T009)
# ------------------------------------------------------------------


@requires_postgres
class TestGetScanHealth:
    """Tests for get_scan_health."""

    @pytest.mark.asyncio()
    async def test_returns_scan_health(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify scan health returns ScanHealthSummary models."""
        repo = Repository(db_pool)
        analytics = AnalyticsRepository(db_pool)

        await _seed_scan_log(repo, "scan-h-1")

        results = await analytics.get_scan_health(_PAST)
        assert len(results) >= 1
        assert isinstance(results[0], ScanHealthSummary)
        assert results[0].scan_count >= 1
        assert isinstance(results[0].avg_duration_s, float)

    @pytest.mark.asyncio()
    async def test_empty_scan_health(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify empty list when no scan logs exist."""
        analytics = AnalyticsRepository(db_pool)
        results = await analytics.get_scan_health(_PAST)
        assert results == []


@requires_postgres
class TestGetRecentScanLogs:
    """Tests for get_recent_scan_logs."""

    @pytest.mark.asyncio()
    async def test_returns_dicts(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify recent scan logs returns list of dicts."""
        repo = Repository(db_pool)
        analytics = AnalyticsRepository(db_pool)

        await _seed_scan_log(repo, "scan-rl-1")
        await _seed_scan_log(repo, "scan-rl-2", started_at=_NOW + timedelta(minutes=5))

        results = await analytics.get_recent_scan_logs(limit=10)
        assert len(results) == 2
        assert isinstance(results[0], dict)
        assert "started_at" in results[0]


# ------------------------------------------------------------------
# Date-range queries (T010)
# ------------------------------------------------------------------


@requires_postgres
class TestGetOpportunitiesDateRange:
    """Tests for get_opportunities_date_range."""

    @pytest.mark.asyncio()
    async def test_since_filter(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify since filtering works."""
        repo = Repository(db_pool)
        analytics = AnalyticsRepository(db_pool)

        await _seed_opportunity(repo, "poly-odr-1", "kalshi-odr-1", detected_at=_NOW)

        results = await analytics.get_opportunities_date_range(_PAST)
        assert len(results) == 1
        assert isinstance(results[0], dict)

    @pytest.mark.asyncio()
    async def test_until_filter(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify until filtering excludes future records."""
        repo = Repository(db_pool)
        analytics = AnalyticsRepository(db_pool)

        await _seed_opportunity(repo, "poly-odr-2", "kalshi-odr-2", detected_at=_NOW)

        # until is in the past, so no results
        results = await analytics.get_opportunities_date_range(_FAR_PAST, until=_PAST)
        assert results == []


@requires_postgres
class TestGetTicketsDateRange:
    """Tests for get_tickets_date_range."""

    @pytest.mark.asyncio()
    async def test_since_filter(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify tickets are returned within date range."""
        repo = Repository(db_pool)
        analytics = AnalyticsRepository(db_pool)

        opp = await _seed_opportunity(repo, "poly-tdr-1", "kalshi-tdr-1")
        ticket = ExecutionTicket(
            arb_id=opp.id,
            leg_1={"venue": "kalshi", "side": "buy"},
            leg_2={"venue": "polymarket", "side": "sell"},
            expected_cost=Decimal("0.87"),
            expected_profit=Decimal("0.05"),
            status="pending",
        )
        await repo.insert_ticket(ticket)

        results = await analytics.get_tickets_date_range(_PAST)
        assert len(results) == 1
        assert isinstance(results[0], dict)

    @pytest.mark.asyncio()
    async def test_empty_tickets(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify empty list when no tickets exist in range."""
        analytics = AnalyticsRepository(db_pool)
        results = await analytics.get_tickets_date_range(_PAST)
        assert results == []


@requires_postgres
class TestGetMatchesDateRange:
    """Tests for get_matches_date_range."""

    @pytest.mark.asyncio()
    async def test_since_filter(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify matches are returned within date range."""
        repo = Repository(db_pool)
        analytics = AnalyticsRepository(db_pool)

        match = _make_match("poly-mdr-1", "kalshi-mdr-1")
        await repo.upsert_match_result(match)

        results = await analytics.get_matches_date_range(_PAST, include_expired=True)
        assert len(results) >= 1
        assert isinstance(results[0], dict)

    @pytest.mark.asyncio()
    async def test_empty_matches(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify empty list when no matches exist."""
        analytics = AnalyticsRepository(db_pool)
        results = await analytics.get_matches_date_range(_PAST)
        assert results == []


# ------------------------------------------------------------------
# Market snapshots (T011)
# ------------------------------------------------------------------


@requires_postgres
class TestMarketSnapshots:
    """Tests for insert_market_snapshot and get_price_history."""

    @pytest.mark.asyncio()
    async def test_roundtrip(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify snapshot insert and retrieval round-trip."""
        analytics = AnalyticsRepository(db_pool)
        market = _make_market(Venue.POLYMARKET, "poly-snap-1")

        await analytics.insert_market_snapshot(market)

        results = await analytics.get_price_history("polymarket", "poly-snap-1", _PAST)
        assert len(results) == 1
        assert isinstance(results[0], dict)
        assert results[0]["venue"] == "polymarket"
        assert results[0]["event_id"] == "poly-snap-1"
        assert results[0]["yes_bid"] == Decimal("0.40")

    @pytest.mark.asyncio()
    async def test_empty_price_history(self, db_pool: asyncpg.Pool[asyncpg.Record]) -> None:
        """Verify empty list for unknown market."""
        analytics = AnalyticsRepository(db_pool)
        results = await analytics.get_price_history("kalshi", "no-such-id", _PAST)
        assert results == []
