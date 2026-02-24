"""T036 - Unit tests for the arbitrage spread and fee calculator.

Parametrized test cases covering basic arb, zero profit, negative profit,
annualized return with/without expiry, and depth risk flag.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from arb_scanner.engine.calculator import calculate_arb
from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.config import ArbThresholds, FeeSchedule, FeesConfig
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
    yes_bid: Decimal | None = None,
    no_bid: Decimal | None = None,
    volume: Decimal = Decimal("5000"),
    expiry: datetime | None = None,
) -> Market:
    """Build a Market with configurable ask prices and optional expiry."""
    return Market(
        venue=venue,
        event_id=event_id,
        title=f"Test {event_id}",
        description="Test",
        resolution_criteria="Test criteria",
        yes_bid=yes_bid if yes_bid is not None else max(yes_ask - Decimal("0.02"), Decimal("0")),
        yes_ask=yes_ask,
        no_bid=no_bid if no_bid is not None else max(no_ask - Decimal("0.02"), Decimal("0")),
        no_ask=no_ask,
        volume_24h=volume,
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW,
        expiry=expiry,
    )


def _make_match(*, safe_to_arb: bool = True) -> MatchResult:
    """Build a MatchResult with configurable safe_to_arb flag."""
    return MatchResult(
        poly_event_id="poly-1",
        kalshi_event_id="kalshi-1",
        match_confidence=0.95,
        resolution_equivalent=True if safe_to_arb else False,
        resolution_risks=[],
        safe_to_arb=safe_to_arb,
        reasoning="Test match",
        matched_at=_NOW,
        ttl_expires=_FUTURE,
    )


def _standard_fees() -> FeesConfig:
    """Build standard fee config: Polymarket 2% on winnings, Kalshi 7c per contract."""
    return FeesConfig(
        polymarket=FeeSchedule(
            taker_fee_pct=Decimal("0.02"),
            fee_model="on_winnings",
        ),
        kalshi=FeeSchedule(
            taker_fee_pct=Decimal("0.07"),
            fee_model="per_contract",
            fee_cap=Decimal("0.07"),
        ),
    )


def _permissive_thresholds() -> ArbThresholds:
    """Build permissive thresholds to allow most arbs through."""
    return ArbThresholds(
        min_net_spread_pct=Decimal("0.001"),
        min_size_usd=Decimal("1"),
        thin_liquidity_threshold=Decimal("50"),
    )


# ---------------------------------------------------------------------------
# Parametrized arb calculation tests
# ---------------------------------------------------------------------------


class TestCalculateArb:
    """Parametrized tests for the calculate_arb function."""

    @pytest.mark.parametrize(
        "poly_yes_ask,kalshi_no_ask,expected_has_arb",
        [
            # Case 1: Basic arb - cost < 1.00
            (Decimal("0.45"), Decimal("0.42"), True),
            # Case 2: Zero profit - prices sum to 1.00
            (Decimal("0.50"), Decimal("0.50"), False),
            # Case 3: Negative profit - prices sum > 1.00
            (Decimal("0.55"), Decimal("0.50"), False),
            # Case 4: Large spread arb
            (Decimal("0.30"), Decimal("0.30"), True),
            # Case 5: Tight but viable arb
            (Decimal("0.45"), Decimal("0.45"), True),
        ],
        ids=[
            "basic_arb",
            "zero_profit",
            "negative_profit",
            "large_spread",
            "tight_viable",
        ],
    )
    def test_arb_detection(
        self,
        poly_yes_ask: Decimal,
        kalshi_no_ask: Decimal,
        expected_has_arb: bool,
    ) -> None:
        """Verify arb is detected or rejected based on price sums."""
        poly = _make_market(
            Venue.POLYMARKET,
            "poly-1",
            yes_ask=poly_yes_ask,
            no_ask=Decimal("0.55"),
        )
        kalshi = _make_market(
            Venue.KALSHI,
            "kalshi-1",
            yes_ask=Decimal("0.50"),
            no_ask=kalshi_no_ask,
        )
        match = _make_match()
        fees = _standard_fees()
        thresholds = _permissive_thresholds()

        result = calculate_arb(poly, kalshi, match, fees, thresholds)

        if expected_has_arb:
            assert result is not None
            assert isinstance(result, ArbOpportunity)
            assert result.net_profit > Decimal("0")
        else:
            assert result is None

    def test_basic_arb_cost_calculation(self) -> None:
        """Verify cost = poly_yes_ask + kalshi_no_ask for direction A."""
        poly = _make_market(
            Venue.POLYMARKET,
            "poly-1",
            yes_ask=Decimal("0.45"),
            no_ask=Decimal("0.60"),
        )
        kalshi = _make_market(
            Venue.KALSHI,
            "kalshi-1",
            yes_ask=Decimal("0.55"),
            no_ask=Decimal("0.42"),
        )
        match = _make_match()
        fees = _standard_fees()
        thresholds = _permissive_thresholds()

        result = calculate_arb(poly, kalshi, match, fees, thresholds)
        assert result is not None
        # Direction A: cost = 0.45 + 0.42 = 0.87
        # Gross = 1.00 - 0.87 = 0.13
        # Fee (poly, on_winnings): 0.02 * (1.00 - 0.45) = 0.011
        # Fee (kalshi, per_contract): min(0.07, 0.07) = 0.07
        # Net = 0.13 - 0.011 - 0.07 = 0.049
        assert result.cost_per_contract == Decimal("0.87")
        assert result.gross_profit == Decimal("0.13")

    def test_unsafe_match_returns_none(self) -> None:
        """Verify safe_to_arb=False always returns None."""
        poly = _make_market(
            Venue.POLYMARKET,
            "poly-1",
            yes_ask=Decimal("0.30"),
            no_ask=Decimal("0.70"),
        )
        kalshi = _make_market(
            Venue.KALSHI,
            "kalshi-1",
            yes_ask=Decimal("0.70"),
            no_ask=Decimal("0.30"),
        )
        match = _make_match(safe_to_arb=False)
        fees = _standard_fees()
        thresholds = _permissive_thresholds()

        result = calculate_arb(poly, kalshi, match, fees, thresholds)
        assert result is None


# ---------------------------------------------------------------------------
# Annualized return
# ---------------------------------------------------------------------------


class TestAnnualizedReturn:
    """Tests for annualized return calculation with and without expiry."""

    def test_annualized_return_with_expiry(self) -> None:
        """Verify annualized_return is computed when expiry is set."""
        expiry = _NOW + timedelta(days=30)
        poly = _make_market(
            Venue.POLYMARKET,
            "poly-1",
            yes_ask=Decimal("0.40"),
            no_ask=Decimal("0.60"),
            expiry=expiry,
        )
        kalshi = _make_market(
            Venue.KALSHI,
            "kalshi-1",
            yes_ask=Decimal("0.60"),
            no_ask=Decimal("0.40"),
            expiry=expiry,
        )
        match = _make_match()
        fees = _standard_fees()
        thresholds = _permissive_thresholds()

        result = calculate_arb(poly, kalshi, match, fees, thresholds)
        assert result is not None
        assert result.annualized_return is not None
        assert result.annualized_return > Decimal("0")

    def test_annualized_return_none_without_expiry(self) -> None:
        """Verify annualized_return is None when no expiry is set."""
        poly = _make_market(
            Venue.POLYMARKET,
            "poly-1",
            yes_ask=Decimal("0.40"),
            no_ask=Decimal("0.60"),
        )
        kalshi = _make_market(
            Venue.KALSHI,
            "kalshi-1",
            yes_ask=Decimal("0.60"),
            no_ask=Decimal("0.40"),
        )
        match = _make_match()
        fees = _standard_fees()
        thresholds = _permissive_thresholds()

        result = calculate_arb(poly, kalshi, match, fees, thresholds)
        assert result is not None
        assert result.annualized_return is None


# ---------------------------------------------------------------------------
# Depth risk flag
# ---------------------------------------------------------------------------


class TestDepthRisk:
    """Tests for the depth_risk flag based on thin_liquidity_threshold."""

    def test_depth_risk_when_low_volume(self) -> None:
        """Verify depth_risk=True when max_size < thin_liquidity_threshold."""
        poly = _make_market(
            Venue.POLYMARKET,
            "poly-1",
            yes_ask=Decimal("0.40"),
            no_ask=Decimal("0.60"),
            volume=Decimal("20"),
        )
        kalshi = _make_market(
            Venue.KALSHI,
            "kalshi-1",
            yes_ask=Decimal("0.60"),
            no_ask=Decimal("0.40"),
            volume=Decimal("20"),
        )
        match = _make_match()
        fees = _standard_fees()
        thresholds = ArbThresholds(
            min_net_spread_pct=Decimal("0.001"),
            min_size_usd=Decimal("1"),
            thin_liquidity_threshold=Decimal("100"),
        )

        result = calculate_arb(poly, kalshi, match, fees, thresholds)
        assert result is not None
        assert result.depth_risk is True

    def test_no_depth_risk_when_sufficient_volume(self) -> None:
        """Verify depth_risk=False when max_size >= thin_liquidity_threshold."""
        poly = _make_market(
            Venue.POLYMARKET,
            "poly-1",
            yes_ask=Decimal("0.40"),
            no_ask=Decimal("0.60"),
            volume=Decimal("5000"),
        )
        kalshi = _make_market(
            Venue.KALSHI,
            "kalshi-1",
            yes_ask=Decimal("0.60"),
            no_ask=Decimal("0.40"),
            volume=Decimal("5000"),
        )
        match = _make_match()
        fees = _standard_fees()
        thresholds = _permissive_thresholds()

        result = calculate_arb(poly, kalshi, match, fees, thresholds)
        assert result is not None
        assert result.depth_risk is False
