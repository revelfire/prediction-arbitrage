"""Tests for flippening data models and config models."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from arb_scanner.models.arbitrage import ExecutionTicket
from arb_scanner.models.config import (
    ConfidenceWeights,
    FlippeningConfig,
    SportOverride,
)
from arb_scanner.models.flippening import (
    Baseline,
    EntrySignal,
    ExitReason,
    ExitSignal,
    FlippeningEvent,
    GamePhase,
    PriceUpdate,
    SpikeDirection,
    SportsMarket,
)
from arb_scanner.models.market import Market, Venue


_NOW = datetime.now(tz=UTC)


def _make_market() -> Market:
    return Market(
        venue=Venue.POLYMARKET,
        event_id="test-123",
        title="Will Lakers beat Celtics?",
        description="NBA game",
        resolution_criteria="Official NBA result",
        yes_bid=Decimal("0.65"),
        yes_ask=Decimal("0.67"),
        no_bid=Decimal("0.32"),
        no_ask=Decimal("0.34"),
        volume_24h=Decimal("50000"),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )


# --- PriceUpdate ---


class TestPriceUpdate:
    """Tests for PriceUpdate model."""

    def test_valid_price_update(self) -> None:
        """Valid prices are accepted."""
        pu = PriceUpdate(
            market_id="m1",
            token_id="t1",
            yes_bid=Decimal("0.60"),
            yes_ask=Decimal("0.62"),
            no_bid=Decimal("0.37"),
            no_ask=Decimal("0.39"),
            timestamp=_NOW,
        )
        assert pu.yes_bid == Decimal("0.60")

    def test_rejects_price_above_one(self) -> None:
        """Prices above 1.0 are rejected."""
        with pytest.raises(ValidationError, match="Price must be in"):
            PriceUpdate(
                market_id="m1",
                token_id="t1",
                yes_bid=Decimal("1.01"),
                yes_ask=Decimal("0.62"),
                no_bid=Decimal("0.37"),
                no_ask=Decimal("0.39"),
                timestamp=_NOW,
            )

    def test_rejects_price_below_zero(self) -> None:
        """Prices below 0.0 are rejected."""
        with pytest.raises(ValidationError, match="Price must be in"):
            PriceUpdate(
                market_id="m1",
                token_id="t1",
                yes_bid=Decimal("-0.01"),
                yes_ask=Decimal("0.62"),
                no_bid=Decimal("0.37"),
                no_ask=Decimal("0.39"),
                timestamp=_NOW,
            )


# --- Baseline ---


class TestBaseline:
    """Tests for Baseline model."""

    def test_round_trip(self) -> None:
        """Baseline round-trips correctly."""
        b = Baseline(
            market_id="m1",
            token_id="t1",
            yes_price=Decimal("0.67"),
            no_price=Decimal("0.33"),
            sport="nba",
            captured_at=_NOW,
        )
        assert b.late_join is False
        assert b.game_start_time is None

    def test_rejects_invalid_price(self) -> None:
        """Invalid baseline prices are rejected."""
        with pytest.raises(ValidationError):
            Baseline(
                market_id="m1",
                token_id="t1",
                yes_price=Decimal("1.5"),
                no_price=Decimal("0.33"),
                sport="nba",
                captured_at=_NOW,
            )


# --- SportsMarket ---


class TestSportsMarket:
    """Tests for SportsMarket model."""

    def test_wraps_market(self) -> None:
        """SportsMarket wraps a Market with sport metadata."""
        m = _make_market()
        sm = SportsMarket(
            market=m,
            sport="nba",
            token_id="tok-abc",
            game_start_time=_NOW,
        )
        assert sm.sport == "nba"
        assert sm.market.title == "Will Lakers beat Celtics?"


# --- FlippeningEvent ---


class TestFlippeningEvent:
    """Tests for FlippeningEvent model."""

    def test_uuid_generation(self) -> None:
        """FlippeningEvent auto-generates a uuid."""
        e = FlippeningEvent(
            market_id="m1",
            market_title="Test",
            baseline_yes=Decimal("0.67"),
            spike_price=Decimal("0.52"),
            spike_magnitude_pct=Decimal("0.15"),
            spike_direction=SpikeDirection.FAVORITE_DROP,
            confidence=Decimal("0.75"),
            sport="nba",
            detected_at=_NOW,
        )
        assert len(e.id) == 36  # uuid4 string

    def test_rejects_invalid_confidence(self) -> None:
        """Confidence outside [0, 1] is rejected."""
        with pytest.raises(ValidationError, match="Confidence must be in"):
            FlippeningEvent(
                market_id="m1",
                market_title="Test",
                baseline_yes=Decimal("0.67"),
                spike_price=Decimal("0.52"),
                spike_magnitude_pct=Decimal("0.15"),
                spike_direction=SpikeDirection.FAVORITE_DROP,
                confidence=Decimal("1.5"),
                sport="nba",
                detected_at=_NOW,
            )


# --- EntrySignal ---


class TestEntrySignal:
    """Tests for EntrySignal model."""

    def test_valid_sides(self) -> None:
        """Both 'yes' and 'no' are valid sides."""
        for side in ("yes", "no"):
            s = EntrySignal(
                event_id="e1",
                side=side,
                entry_price=Decimal("0.52"),
                target_exit_price=Decimal("0.62"),
                stop_loss_price=Decimal("0.44"),
                suggested_size_usd=Decimal("100"),
                expected_profit_pct=Decimal("0.192"),
                max_hold_minutes=45,
                created_at=_NOW,
            )
            assert s.side == side

    def test_rejects_invalid_side(self) -> None:
        """Invalid side value is rejected."""
        with pytest.raises(ValidationError, match="side must be one of"):
            EntrySignal(
                event_id="e1",
                side="maybe",
                entry_price=Decimal("0.52"),
                target_exit_price=Decimal("0.62"),
                stop_loss_price=Decimal("0.44"),
                suggested_size_usd=Decimal("100"),
                expected_profit_pct=Decimal("0.192"),
                max_hold_minutes=45,
                created_at=_NOW,
            )

    def test_rejects_entry_price_out_of_range(self) -> None:
        """Entry price outside [0, 1] is rejected."""
        with pytest.raises(ValidationError, match="entry_price must be in"):
            EntrySignal(
                event_id="e1",
                side="yes",
                entry_price=Decimal("1.5"),
                target_exit_price=Decimal("1.62"),
                stop_loss_price=Decimal("1.27"),
                suggested_size_usd=Decimal("100"),
                expected_profit_pct=Decimal("0.08"),
                max_hold_minutes=45,
                created_at=_NOW,
            )


# --- ExitSignal ---


class TestExitSignal:
    """Tests for ExitSignal model."""

    def test_each_exit_reason(self) -> None:
        """ExitSignal accepts every ExitReason variant."""
        for reason in ExitReason:
            s = ExitSignal(
                event_id="e1",
                side="yes",
                exit_price=Decimal("0.62"),
                exit_reason=reason,
                realized_pnl=Decimal("0.10"),
                realized_pnl_pct=Decimal("0.192"),
                hold_minutes=Decimal("22.5"),
                created_at=_NOW,
            )
            assert s.exit_reason == reason


# --- Enums ---


class TestEnums:
    """Tests for enum values."""

    def test_game_phase_values(self) -> None:
        """GamePhase has expected values."""
        assert GamePhase.UPCOMING.value == "upcoming"
        assert GamePhase.LIVE.value == "live"
        assert GamePhase.COMPLETED.value == "completed"

    def test_spike_direction_values(self) -> None:
        """SpikeDirection has expected values."""
        assert SpikeDirection.FAVORITE_DROP.value == "favorite_drop"
        assert SpikeDirection.UNDERDOG_RISE.value == "underdog_rise"


# --- Config models ---


class TestConfidenceWeights:
    """Tests for ConfidenceWeights model."""

    def test_defaults_sum_to_one(self) -> None:
        """Default weights sum to 1.0."""
        w = ConfidenceWeights()
        assert abs(w.magnitude + w.strength + w.speed - 1.0) < 0.01

    def test_rejects_bad_sum(self) -> None:
        """Weights that don't sum to 1.0 are rejected."""
        with pytest.raises(ValidationError, match="must sum to 1.0"):
            ConfidenceWeights(magnitude=0.5, strength=0.5, speed=0.5)


class TestFlippeningConfig:
    """Tests for FlippeningConfig defaults."""

    def test_defaults(self) -> None:
        """FlippeningConfig defaults are correct."""
        c = FlippeningConfig()
        assert c.enabled is False
        assert c.spike_threshold_pct == 0.15
        assert c.min_confidence == 0.60
        assert "nba" in c.sports
        assert c.sport_overrides == {}

    def test_sport_override(self) -> None:
        """SportOverride can override thresholds."""
        so = SportOverride(spike_threshold_pct=0.12, confidence_modifier=1.1)
        assert so.spike_threshold_pct == 0.12
        assert so.confidence_modifier == 1.1


# --- ExecutionTicket ticket_type ---


class TestExecutionTicketType:
    """Tests for ticket_type field on ExecutionTicket."""

    def test_default_is_arbitrage(self) -> None:
        """Default ticket_type is 'arbitrage'."""
        from arb_scanner.models.matching import MatchResult

        from datetime import timedelta

        match = MatchResult(
            poly_event_id="p1",
            kalshi_event_id="k1",
            match_confidence=0.95,
            resolution_equivalent=True,
            resolution_risks=[],
            safe_to_arb=True,
            reasoning="test",
            matched_at=_NOW,
            ttl_expires=_NOW + timedelta(hours=24),
        )
        from arb_scanner.models.arbitrage import ArbOpportunity

        opp = ArbOpportunity(
            match=match,
            poly_market=_make_market(),
            kalshi_market=_make_market().model_copy(
                update={"venue": Venue.KALSHI, "event_id": "k1"},
            ),
            buy_venue=Venue.POLYMARKET,
            sell_venue=Venue.KALSHI,
            cost_per_contract=Decimal("0.95"),
            gross_profit=Decimal("0.05"),
            net_profit=Decimal("0.03"),
            net_spread_pct=Decimal("0.03"),
            max_size=Decimal("500"),
            depth_risk=False,
            detected_at=_NOW,
        )
        t = ExecutionTicket(
            arb_id=opp.id,
            leg_1={"venue": "polymarket"},
            leg_2={"venue": "kalshi"},
            expected_cost=Decimal("0.95"),
            expected_profit=Decimal("0.03"),
        )
        assert t.ticket_type == "arbitrage"

    def test_accepts_flippening_type(self) -> None:
        """ticket_type='flippening' is accepted."""
        t = ExecutionTicket(
            arb_id="flip-123",
            leg_1={"venue": "polymarket", "action": "buy"},
            leg_2={"venue": "polymarket", "action": "sell"},
            expected_cost=Decimal("0.52"),
            expected_profit=Decimal("0.10"),
            ticket_type="flippening",
        )
        assert t.ticket_type == "flippening"

    def test_rejects_invalid_type(self) -> None:
        """Invalid ticket_type is rejected."""
        with pytest.raises(ValidationError, match="ticket_type must be one of"):
            ExecutionTicket(
                arb_id="x",
                leg_1={},
                leg_2={},
                expected_cost=Decimal("0.50"),
                expected_profit=Decimal("0.10"),
                ticket_type="unknown",
            )
