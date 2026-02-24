"""Top-level test configuration and shared fixtures."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from arb_scanner.models.arbitrage import ArbOpportunity, ExecutionTicket
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult
from arb_scanner.models.scan_log import ScanLog

_NOW = datetime.now(tz=timezone.utc)


@pytest.fixture()
def poly_market() -> Market:
    """Create a valid Polymarket Market instance for testing."""
    return Market(
        venue=Venue.POLYMARKET,
        event_id="poly-evt-001",
        title="Will X happen by end of 2026?",
        description="Market on event X",
        resolution_criteria="Resolves YES if X occurs before 2027-01-01",
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.45"),
        no_bid=Decimal("0.50"),
        no_ask=Decimal("0.55"),
        volume_24h=Decimal("10000"),
        fees_pct=Decimal("0.00"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )


@pytest.fixture()
def kalshi_market() -> Market:
    """Create a valid Kalshi Market instance for testing."""
    return Market(
        venue=Venue.KALSHI,
        event_id="kalshi-evt-001",
        title="Will X happen by end of 2026?",
        description="Kalshi market on event X",
        resolution_criteria="Resolves YES if X occurs before 2027-01-01",
        yes_bid=Decimal("0.38"),
        yes_ask=Decimal("0.42"),
        no_bid=Decimal("0.53"),
        no_ask=Decimal("0.58"),
        volume_24h=Decimal("5000"),
        fees_pct=Decimal("0.07"),
        fee_model="per_contract",
        last_updated=_NOW,
    )


@pytest.fixture()
def match_result() -> MatchResult:
    """Create a valid MatchResult instance for testing."""
    return MatchResult(
        poly_event_id="poly-evt-001",
        kalshi_event_id="kalshi-evt-001",
        match_confidence=0.95,
        resolution_equivalent=True,
        resolution_risks=["minor wording difference"],
        safe_to_arb=True,
        reasoning="Both markets resolve on the same underlying event.",
        matched_at=_NOW,
        ttl_expires=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture()
def arb_opportunity(
    match_result: MatchResult,
    poly_market: Market,
    kalshi_market: Market,
) -> ArbOpportunity:
    """Create a valid ArbOpportunity instance for testing."""
    return ArbOpportunity(
        match=match_result,
        poly_market=poly_market,
        kalshi_market=kalshi_market,
        buy_venue=Venue.KALSHI,
        sell_venue=Venue.POLYMARKET,
        cost_per_contract=Decimal("0.90"),
        gross_profit=Decimal("0.10"),
        net_profit=Decimal("0.03"),
        net_spread_pct=Decimal("0.03"),
        max_size=Decimal("100"),
        depth_risk=False,
        detected_at=_NOW,
    )


@pytest.fixture()
def execution_ticket() -> ExecutionTicket:
    """Create a valid ExecutionTicket instance for testing."""
    return ExecutionTicket(
        arb_id="test-arb-001",
        leg_1={"venue": "kalshi", "side": "buy", "price": "0.42"},
        leg_2={"venue": "polymarket", "side": "sell", "price": "0.55"},
        expected_cost=Decimal("0.90"),
        expected_profit=Decimal("0.03"),
        status="pending",
    )


@pytest.fixture()
def scan_log() -> ScanLog:
    """Create a valid ScanLog instance for testing."""
    return ScanLog(
        id="scan-001",
        started_at=_NOW,
        completed_at=_NOW,
        poly_markets_fetched=50,
        kalshi_markets_fetched=30,
        candidate_pairs=10,
        llm_evaluations=5,
        opportunities_found=2,
        errors=[],
    )
