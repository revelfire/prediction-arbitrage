"""T038 - Unit tests for execution ticket generation.

Tests ticket leg construction, expected cost/profit calculations,
and default status.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from arb_scanner.engine.tickets import generate_ticket
from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult

_NOW = datetime.now(tz=timezone.utc)
_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _make_market(
    venue: Venue,
    event_id: str,
    *,
    yes_ask: Decimal,
    no_ask: Decimal,
) -> Market:
    """Build a Market with configurable ask prices."""
    return Market(
        venue=venue,
        event_id=event_id,
        title=f"Test {event_id}",
        description="Test",
        resolution_criteria="Test criteria",
        yes_bid=max(yes_ask - Decimal("0.02"), Decimal("0")),
        yes_ask=yes_ask,
        no_bid=max(no_ask - Decimal("0.02"), Decimal("0")),
        no_ask=no_ask,
        volume_24h=Decimal("5000"),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )


def _make_match() -> MatchResult:
    """Build a standard safe MatchResult."""
    return MatchResult(
        poly_event_id="poly-1",
        kalshi_event_id="kalshi-1",
        match_confidence=0.95,
        resolution_equivalent=True,
        resolution_risks=[],
        safe_to_arb=True,
        reasoning="Test match",
        matched_at=_NOW,
        ttl_expires=_FUTURE,
    )


def _make_opportunity(
    *,
    buy_venue: Venue = Venue.POLYMARKET,
    sell_venue: Venue = Venue.KALSHI,
    poly_yes_ask: Decimal = Decimal("0.45"),
    kalshi_no_ask: Decimal = Decimal("0.42"),
    kalshi_yes_ask: Decimal = Decimal("0.55"),
    poly_no_ask: Decimal = Decimal("0.58"),
    cost: Decimal = Decimal("0.87"),
    net_profit: Decimal = Decimal("0.05"),
    max_size: Decimal = Decimal("100"),
) -> ArbOpportunity:
    """Build an ArbOpportunity with configurable parameters."""
    poly = _make_market(
        Venue.POLYMARKET,
        "poly-1",
        yes_ask=poly_yes_ask,
        no_ask=poly_no_ask,
    )
    kalshi = _make_market(
        Venue.KALSHI,
        "kalshi-1",
        yes_ask=kalshi_yes_ask,
        no_ask=kalshi_no_ask,
    )
    return ArbOpportunity(
        match=_make_match(),
        poly_market=poly,
        kalshi_market=kalshi,
        buy_venue=buy_venue,
        sell_venue=sell_venue,
        cost_per_contract=cost,
        gross_profit=Decimal("0.13"),
        net_profit=net_profit,
        net_spread_pct=Decimal("0.05"),
        max_size=max_size,
        depth_risk=False,
        detected_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Ticket leg construction
# ---------------------------------------------------------------------------


class TestTicketLegConstruction:
    """Verify ticket legs have correct venue, side, price, and size."""

    def test_leg1_buys_yes_on_buy_venue(self) -> None:
        """Verify leg_1 buys YES on the buy venue."""
        opp = _make_opportunity(buy_venue=Venue.POLYMARKET, sell_venue=Venue.KALSHI)
        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None

        assert ticket.leg_1["venue"] == "polymarket"
        assert ticket.leg_1["side"] == "YES"

    def test_leg2_buys_no_on_sell_venue(self) -> None:
        """Verify leg_2 buys NO on the sell venue."""
        opp = _make_opportunity(buy_venue=Venue.POLYMARKET, sell_venue=Venue.KALSHI)
        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None

        assert ticket.leg_2["venue"] == "kalshi"
        assert ticket.leg_2["side"] == "NO"

    def test_leg_prices_match_market_asks(self) -> None:
        """Verify leg prices come from the correct market ask prices."""
        opp = _make_opportunity(
            buy_venue=Venue.POLYMARKET,
            sell_venue=Venue.KALSHI,
            poly_yes_ask=Decimal("0.45"),
            kalshi_no_ask=Decimal("0.42"),
        )
        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None

        assert ticket.leg_1["price"] == Decimal("0.45")
        assert ticket.leg_2["price"] == Decimal("0.42")

    def test_leg_sizes_equal_max_size(self) -> None:
        """Verify both legs use the max_size from the opportunity."""
        opp = _make_opportunity(max_size=Decimal("250"))
        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None

        assert ticket.leg_1["size"] == Decimal("250")
        assert ticket.leg_2["size"] == Decimal("250")

    def test_reversed_direction_legs(self) -> None:
        """Verify legs are correct when buy_venue=KALSHI, sell_venue=POLYMARKET."""
        opp = _make_opportunity(
            buy_venue=Venue.KALSHI,
            sell_venue=Venue.POLYMARKET,
            kalshi_yes_ask=Decimal("0.55"),
            poly_no_ask=Decimal("0.35"),
        )
        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None

        assert ticket.leg_1["venue"] == "kalshi"
        assert ticket.leg_1["side"] == "YES"
        assert ticket.leg_1["price"] == Decimal("0.55")
        assert ticket.leg_2["venue"] == "polymarket"
        assert ticket.leg_2["side"] == "NO"
        assert ticket.leg_2["price"] == Decimal("0.35")


# ---------------------------------------------------------------------------
# Expected cost and profit
# ---------------------------------------------------------------------------


class TestExpectedCostProfit:
    """Verify expected_cost and expected_profit calculations."""

    def test_expected_cost_equals_cost_times_size(self) -> None:
        """Verify expected_cost = cost_per_contract * max_size."""
        opp = _make_opportunity(cost=Decimal("0.87"), max_size=Decimal("100"))
        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None
        assert ticket.expected_cost == Decimal("87.00")

    def test_expected_profit_equals_net_profit_times_size(self) -> None:
        """Verify expected_profit = net_profit * max_size."""
        opp = _make_opportunity(net_profit=Decimal("0.05"), max_size=Decimal("100"))
        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None
        assert ticket.expected_profit == Decimal("5.00")

    def test_large_size_capped_to_max_ticket_size(self) -> None:
        """Verify max_size is capped to max_ticket_size_usd."""
        opp = _make_opportunity(
            cost=Decimal("0.90"),
            net_profit=Decimal("0.03"),
            max_size=Decimal("10000"),
        )
        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None
        # Default cap is $500, so effective_size = 500
        assert ticket.expected_cost == Decimal("450.00")
        assert ticket.expected_profit == Decimal("15.00")

    def test_custom_max_ticket_size(self) -> None:
        """Verify custom max_ticket_size_usd overrides default cap."""
        opp = _make_opportunity(
            cost=Decimal("0.90"),
            net_profit=Decimal("0.03"),
            max_size=Decimal("10000"),
        )
        ticket = generate_ticket(
            opp,
            min_expected_profit_usd=Decimal("0"),
            max_ticket_size_usd=Decimal("200"),
        )
        assert ticket is not None
        assert ticket.expected_cost == Decimal("180.00")
        assert ticket.expected_profit == Decimal("6.00")
        assert ticket.leg_1["size"] == Decimal("200")
        assert ticket.leg_2["size"] == Decimal("200")

    def test_size_below_cap_unchanged(self) -> None:
        """Verify sizes below cap are not modified."""
        opp = _make_opportunity(
            cost=Decimal("0.90"),
            net_profit=Decimal("0.03"),
            max_size=Decimal("100"),
        )
        ticket = generate_ticket(
            opp,
            min_expected_profit_usd=Decimal("0"),
            max_ticket_size_usd=Decimal("500"),
        )
        assert ticket is not None
        assert ticket.expected_cost == Decimal("90.00")
        assert ticket.leg_1["size"] == Decimal("100")


# ---------------------------------------------------------------------------
# Default status
# ---------------------------------------------------------------------------


class TestDefaultStatus:
    """Verify tickets are created with pending status."""

    def test_status_defaults_to_pending(self) -> None:
        """Verify generated tickets have status='pending'."""
        opp = _make_opportunity()
        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None
        assert ticket.status == "pending"

    def test_ticket_arb_id_matches_opportunity(self) -> None:
        """Verify ticket.arb_id matches the opportunity id."""
        opp = _make_opportunity()
        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None
        assert ticket.arb_id == opp.id


# ---------------------------------------------------------------------------
# Minimum profit threshold
# ---------------------------------------------------------------------------


class TestMinProfitThreshold:
    """Verify min_expected_profit_usd gating on ticket creation."""

    def test_skips_below_min_profit(self) -> None:
        """Ticket is None when expected profit < min threshold."""
        opp = _make_opportunity(net_profit=Decimal("0.005"), max_size=Decimal("100"))
        # expected_profit = 0.005 * 100 = $0.50, below default $1.00
        ticket = generate_ticket(opp)
        assert ticket is None

    def test_creates_at_min_profit(self) -> None:
        """Ticket is created when expected profit == min threshold."""
        opp = _make_opportunity(net_profit=Decimal("0.01"), max_size=Decimal("100"))
        # expected_profit = 0.01 * 100 = $1.00, equals default $1.00
        ticket = generate_ticket(opp)
        assert ticket is not None

    def test_custom_min_profit_threshold(self) -> None:
        """Custom threshold overrides the default."""
        opp = _make_opportunity(net_profit=Decimal("0.05"), max_size=Decimal("100"))
        # expected_profit = $5.00
        ticket_above = generate_ticket(opp, min_expected_profit_usd=Decimal("5.00"))
        assert ticket_above is not None

        ticket_below = generate_ticket(opp, min_expected_profit_usd=Decimal("6.00"))
        assert ticket_below is None

    def test_zero_threshold_allows_any_positive(self) -> None:
        """Zero threshold allows any positive profit."""
        opp = _make_opportunity(net_profit=Decimal("0.001"), max_size=Decimal("1"))
        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None

    def test_negative_profit_always_skipped(self) -> None:
        """Negative profit is always skipped regardless of threshold."""
        opp = _make_opportunity(net_profit=Decimal("-0.01"), max_size=Decimal("100"))
        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is None


class TestVenueIdentifiers:
    """Verify venue-specific token/ticker identifiers are attached to legs."""

    def test_includes_poly_token_and_kalshi_ticker(self) -> None:
        """Polymarket legs include token_id and Kalshi legs include ticker."""
        opp = _make_opportunity(buy_venue=Venue.POLYMARKET, sell_venue=Venue.KALSHI)
        opp.poly_market.raw_data = {"clobTokenIds": '["poly-token-1"]'}
        opp.kalshi_market.raw_data = {"ticker": "KXTEST"}

        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None
        assert ticket.leg_1["token_id"] == "poly-token-1"
        assert ticket.leg_2["ticker"] == "KXTEST"

    def test_poly_token_id_falls_back_to_event_id(self) -> None:
        """Missing Polymarket token metadata falls back to market event_id."""
        opp = _make_opportunity(buy_venue=Venue.POLYMARKET, sell_venue=Venue.KALSHI)
        opp.poly_market.raw_data = {}

        ticket = generate_ticket(opp, min_expected_profit_usd=Decimal("0"))
        assert ticket is not None
        assert ticket.leg_1["token_id"] == "poly-1"
