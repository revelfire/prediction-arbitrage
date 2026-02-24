"""T037 - Unit tests for venue-specific fee models.

Tests Polymarket on-winnings fees, Kalshi per-contract fees,
and the zero-fee case when spread is negative.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from arb_scanner.engine.calculator import _compute_fee
from arb_scanner.models.config import FeeSchedule


# ---------------------------------------------------------------------------
# Polymarket on-winnings fee model
# ---------------------------------------------------------------------------


class TestPolymarketOnWinningsFee:
    """Tests for the Polymarket on-winnings fee calculation."""

    def test_standard_on_winnings_fee(self) -> None:
        """Verify fee = taker_fee_pct * (1.00 - price_paid)."""
        schedule = FeeSchedule(
            taker_fee_pct=Decimal("0.02"),
            fee_model="on_winnings",
        )
        # fee = 0.02 * (1.00 - 0.45) = 0.02 * 0.55 = 0.011
        fee = _compute_fee(Decimal("0.45"), schedule)
        assert fee == Decimal("0.011")

    def test_on_winnings_at_high_price(self) -> None:
        """Verify fee decreases as price approaches 1.00."""
        schedule = FeeSchedule(
            taker_fee_pct=Decimal("0.02"),
            fee_model="on_winnings",
        )
        # fee = 0.02 * (1.00 - 0.90) = 0.02 * 0.10 = 0.002
        fee = _compute_fee(Decimal("0.90"), schedule)
        assert fee == Decimal("0.002")

    def test_on_winnings_at_low_price(self) -> None:
        """Verify fee is higher for cheaper contracts (more upside)."""
        schedule = FeeSchedule(
            taker_fee_pct=Decimal("0.02"),
            fee_model="on_winnings",
        )
        # fee = 0.02 * (1.00 - 0.10) = 0.02 * 0.90 = 0.018
        fee = _compute_fee(Decimal("0.10"), schedule)
        assert fee == Decimal("0.018")

    def test_on_winnings_at_boundary_one(self) -> None:
        """Verify fee is zero when price is 1.00 (no upside)."""
        schedule = FeeSchedule(
            taker_fee_pct=Decimal("0.02"),
            fee_model="on_winnings",
        )
        fee = _compute_fee(Decimal("1.00"), schedule)
        assert fee == Decimal("0.00")


# ---------------------------------------------------------------------------
# Kalshi per-contract fee model
# ---------------------------------------------------------------------------


class TestKalshiPerContractFee:
    """Tests for the Kalshi per-contract fee calculation."""

    def test_per_contract_with_cap(self) -> None:
        """Verify fee = min(taker_fee_pct, fee_cap)."""
        schedule = FeeSchedule(
            taker_fee_pct=Decimal("0.07"),
            fee_model="per_contract",
            fee_cap=Decimal("0.07"),
        )
        fee = _compute_fee(Decimal("0.45"), schedule)
        assert fee == Decimal("0.07")

    def test_per_contract_without_cap(self) -> None:
        """Verify fee = taker_fee_pct when no cap is set."""
        schedule = FeeSchedule(
            taker_fee_pct=Decimal("0.07"),
            fee_model="per_contract",
        )
        fee = _compute_fee(Decimal("0.45"), schedule)
        assert fee == Decimal("0.07")

    def test_per_contract_cap_lower_than_fee(self) -> None:
        """Verify cap limits the fee when cap < taker_fee_pct."""
        schedule = FeeSchedule(
            taker_fee_pct=Decimal("0.10"),
            fee_model="per_contract",
            fee_cap=Decimal("0.05"),
        )
        fee = _compute_fee(Decimal("0.45"), schedule)
        assert fee == Decimal("0.05")

    def test_per_contract_independent_of_price(self) -> None:
        """Verify per-contract fee is the same regardless of price."""
        schedule = FeeSchedule(
            taker_fee_pct=Decimal("0.07"),
            fee_model="per_contract",
            fee_cap=Decimal("0.07"),
        )
        fee_low = _compute_fee(Decimal("0.10"), schedule)
        fee_high = _compute_fee(Decimal("0.90"), schedule)
        assert fee_low == fee_high == Decimal("0.07")


# ---------------------------------------------------------------------------
# Zero fee when spread is negative
# ---------------------------------------------------------------------------


class TestZeroFeeNegativeSpread:
    """Tests verifying no opportunity is returned when spread is negative."""

    @pytest.mark.parametrize(
        "poly_yes_ask,kalshi_no_ask",
        [
            (Decimal("0.55"), Decimal("0.50")),
            (Decimal("0.60"), Decimal("0.45")),
            (Decimal("0.70"), Decimal("0.40")),
        ],
        ids=["slight_negative", "moderate_negative", "large_negative"],
    )
    def test_negative_spread_no_arb(self, poly_yes_ask: Decimal, kalshi_no_ask: Decimal) -> None:
        """Verify no arb opportunity when prices sum > 1.00."""
        from datetime import datetime, timezone

        from arb_scanner.engine.calculator import calculate_arb
        from arb_scanner.models.config import ArbThresholds, FeesConfig
        from arb_scanner.models.market import Market, Venue
        from arb_scanner.models.matching import MatchResult

        now = datetime.now(tz=timezone.utc)
        future = datetime(2099, 1, 1, tzinfo=timezone.utc)

        poly = Market(
            venue=Venue.POLYMARKET,
            event_id="poly-neg",
            title="Test negative",
            description="Test",
            resolution_criteria="Test",
            yes_bid=poly_yes_ask - Decimal("0.02"),
            yes_ask=poly_yes_ask,
            no_bid=Decimal("0.40"),
            no_ask=Decimal("0.45"),
            volume_24h=Decimal("5000"),
            fees_pct=Decimal("0.02"),
            fee_model="on_winnings",
            last_updated=now,
        )
        kalshi = Market(
            venue=Venue.KALSHI,
            event_id="kalshi-neg",
            title="Test negative",
            description="Test",
            resolution_criteria="Test",
            yes_bid=Decimal("0.50"),
            yes_ask=Decimal("0.55"),
            no_bid=kalshi_no_ask - Decimal("0.02"),
            no_ask=kalshi_no_ask,
            volume_24h=Decimal("5000"),
            fees_pct=Decimal("0.07"),
            fee_model="per_contract",
            last_updated=now,
        )
        match = MatchResult(
            poly_event_id="poly-neg",
            kalshi_event_id="kalshi-neg",
            match_confidence=0.95,
            resolution_equivalent=True,
            resolution_risks=[],
            safe_to_arb=True,
            reasoning="Test",
            matched_at=now,
            ttl_expires=future,
        )
        fees = FeesConfig(
            polymarket=FeeSchedule(taker_fee_pct=Decimal("0.02"), fee_model="on_winnings"),
            kalshi=FeeSchedule(
                taker_fee_pct=Decimal("0.07"),
                fee_model="per_contract",
                fee_cap=Decimal("0.07"),
            ),
        )
        thresholds = ArbThresholds(
            min_net_spread_pct=Decimal("0.001"),
            min_size_usd=Decimal("1"),
            thin_liquidity_threshold=Decimal("50"),
        )

        result = calculate_arb(poly, kalshi, match, fees, thresholds)
        assert result is None
