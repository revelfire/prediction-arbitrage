"""Tests for signal generation and reversion monitoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arb_scanner.flippening.signal_generator import SignalGenerator
from arb_scanner.models.config import FlippeningConfig
from arb_scanner.models.flippening import (
    Baseline,
    EntrySignal,
    ExitReason,
    FlippeningEvent,
    PriceUpdate,
    SpikeDirection,
)

_NOW = datetime.now(tz=UTC)
_CONFIG = FlippeningConfig(
    enabled=True,
    reversion_target_pct=0.70,
    stop_loss_pct=0.15,
    base_position_usd=100.0,
    max_position_usd=500.0,
    max_hold_minutes=45,
)


def _baseline(yes: str = "0.65") -> Baseline:
    return Baseline(
        market_id="m1",
        token_id="tok-1",
        yes_price=Decimal(yes),
        no_price=Decimal("1") - Decimal(yes),
        sport="nba",
        game_start_time=_NOW - timedelta(minutes=30),
        captured_at=_NOW,
        late_join=False,
    )


def _event(
    direction: SpikeDirection = SpikeDirection.FAVORITE_DROP,
    confidence: str = "0.80",
    baseline_yes: str = "0.65",
    spike_price: str = "0.50",
    spike_magnitude_pct: str = "0.23",
) -> FlippeningEvent:
    return FlippeningEvent(
        market_id="m1",
        market_title="Lakers vs Celtics",
        baseline_yes=Decimal(baseline_yes),
        spike_price=Decimal(spike_price),
        spike_magnitude_pct=Decimal(spike_magnitude_pct),
        spike_direction=direction,
        confidence=Decimal(confidence),
        sport="nba",
        detected_at=_NOW,
    )


def _entry(
    side: str = "yes",
    entry_price: str = "0.50",
    target_exit: str = "0.605",
    stop_loss: str = "0.425",
) -> EntrySignal:
    return EntrySignal(
        event_id="e1",
        side=side,
        entry_price=Decimal(entry_price),
        target_exit_price=Decimal(target_exit),
        stop_loss_price=Decimal(stop_loss),
        suggested_size_usd=Decimal("80.00"),
        expected_profit_pct=Decimal("0.21"),
        max_hold_minutes=45,
        created_at=_NOW,
    )


def _update(
    yes_bid: str = "0.60",
    no_bid: str = "0.38",
    ts: datetime | None = None,
) -> PriceUpdate:
    return PriceUpdate(
        market_id="m1",
        token_id="tok-1",
        yes_bid=Decimal(yes_bid),
        yes_ask=Decimal("0.62"),
        no_bid=Decimal(no_bid),
        no_ask=Decimal("0.40"),
        timestamp=ts or _NOW,
    )


class TestCreateEntry:
    """Tests for SignalGenerator.create_entry."""

    def test_side_yes_when_favorite_drops(self) -> None:
        """YES favorite drops → side is 'yes'."""
        gen = SignalGenerator(_CONFIG)
        bl = _baseline(yes="0.65")
        ev = _event(direction=SpikeDirection.FAVORITE_DROP)
        signal = gen.create_entry(ev, Decimal("0.50"), bl)
        assert signal is not None
        assert signal.side == "yes"

    def test_side_no_when_underdog_rises(self) -> None:
        """NO favorite (YES < 0.50), underdog rises → side is 'no'."""
        gen = SignalGenerator(_CONFIG)
        bl = _baseline(yes="0.35")
        ev = _event(
            direction=SpikeDirection.UNDERDOG_RISE,
            baseline_yes="0.35",
        )
        signal = gen.create_entry(ev, Decimal("0.55"), bl)
        assert signal is not None
        assert signal.side == "no"

    def test_target_exit_calculation(self) -> None:
        """Target exit = entry + (baseline - entry) * reversion_pct."""
        gen = SignalGenerator(_CONFIG)
        bl = _baseline(yes="0.65")
        ev = _event()
        entry_ask = Decimal("0.50")
        signal = gen.create_entry(ev, entry_ask, bl)
        assert signal is not None
        expected_target = Decimal("0.50") + (Decimal("0.65") - Decimal("0.50")) * Decimal("0.70")
        assert signal.target_exit_price == expected_target

    def test_low_confidence_tightens_exit_profile(self) -> None:
        """Lower-confidence, weaker spikes tighten target/stop and shorten hold."""
        gen = SignalGenerator(_CONFIG)
        bl = _baseline(yes="0.65")
        ev = _event(confidence="0.55", spike_magnitude_pct="0.10")
        signal = gen.create_entry(ev, Decimal("0.50"), bl)
        assert signal is not None

        base_target = Decimal("0.50") + (Decimal("0.65") - Decimal("0.50")) * Decimal("0.70")
        base_stop = Decimal("0.50") * Decimal("0.85")
        assert signal.target_exit_price < base_target
        assert signal.stop_loss_price > base_stop
        assert signal.max_hold_minutes < 45

    def test_stop_loss_calculation(self) -> None:
        """Stop loss = entry * (1 - stop_loss_pct)."""
        gen = SignalGenerator(_CONFIG)
        bl = _baseline()
        ev = _event()
        signal = gen.create_entry(ev, Decimal("0.50"), bl)
        assert signal is not None
        expected_stop = Decimal("0.50") * Decimal("0.85")
        assert signal.stop_loss_price == expected_stop

    def test_size_scales_with_confidence(self) -> None:
        """Higher confidence → larger position size."""
        gen = SignalGenerator(_CONFIG)
        bl = _baseline()

        ev_low = _event(confidence="0.60")
        sig_low = gen.create_entry(ev_low, Decimal("0.50"), bl)

        ev_high = _event(confidence="0.95")
        sig_high = gen.create_entry(ev_high, Decimal("0.50"), bl)

        assert sig_low is not None
        assert sig_high is not None
        assert sig_high.suggested_size_usd > sig_low.suggested_size_usd

    def test_size_capped_at_max(self) -> None:
        """Size never exceeds max_position_usd."""
        config = FlippeningConfig(
            enabled=True,
            base_position_usd=1000.0,
            max_position_usd=200.0,
        )
        gen = SignalGenerator(config)
        bl = _baseline()
        ev = _event(confidence="0.95")
        signal = gen.create_entry(ev, Decimal("0.50"), bl)
        assert signal is not None
        assert signal.suggested_size_usd <= Decimal("200.00")

    def test_expected_profit_pct(self) -> None:
        """Expected profit % = (target - entry) / entry."""
        gen = SignalGenerator(_CONFIG)
        bl = _baseline()
        ev = _event()
        signal = gen.create_entry(ev, Decimal("0.50"), bl)
        assert signal is not None
        expected = (signal.target_exit_price - signal.entry_price) / signal.entry_price
        assert signal.expected_profit_pct == expected

    def test_rejects_entry_below_min_price(self) -> None:
        """Entry rejected when current_ask is below min_entry_price."""
        gen = SignalGenerator(_CONFIG)
        bl = _baseline()
        ev = _event()
        signal = gen.create_entry(ev, Decimal("0.001"), bl)
        assert signal is None

    def test_accepts_entry_at_min_price(self) -> None:
        """Entry accepted when current_ask equals min_entry_price."""
        gen = SignalGenerator(_CONFIG)
        bl = _baseline()
        ev = _event()
        signal = gen.create_entry(ev, Decimal("0.05"), bl)
        assert signal is not None
        assert signal.entry_price == Decimal("0.05")

    def test_custom_min_entry_price(self) -> None:
        """Custom min_entry_price from config is respected."""
        config = FlippeningConfig(enabled=True, min_entry_price=0.10)
        gen = SignalGenerator(config)
        bl = _baseline()
        ev = _event()
        assert gen.create_entry(ev, Decimal("0.09"), bl) is None
        assert gen.create_entry(ev, Decimal("0.10"), bl) is not None


class TestCheckExit:
    """Tests for SignalGenerator.check_exit."""

    def test_reversion_when_bid_hits_target(self) -> None:
        """Bid >= target → REVERSION exit."""
        gen = SignalGenerator(_CONFIG)
        entry = _entry(target_exit="0.605")
        update = _update(
            yes_bid="0.61",
            ts=_NOW + timedelta(minutes=10),
        )
        exit_sig = gen.check_exit(update, entry)
        assert exit_sig is not None
        assert exit_sig.exit_reason == ExitReason.REVERSION
        assert exit_sig.exit_price == Decimal("0.61")

    def test_stop_loss_when_bid_drops(self) -> None:
        """Bid <= stop_loss → STOP_LOSS exit."""
        gen = SignalGenerator(_CONFIG)
        entry = _entry(stop_loss="0.425")
        update = _update(
            yes_bid="0.42",
            ts=_NOW + timedelta(minutes=5),
        )
        exit_sig = gen.check_exit(update, entry)
        assert exit_sig is not None
        assert exit_sig.exit_reason == ExitReason.STOP_LOSS

    def test_timeout_when_hold_exceeded(self) -> None:
        """Elapsed >= max_hold_minutes → TIMEOUT exit."""
        gen = SignalGenerator(_CONFIG)
        entry = _entry()
        update = _update(
            yes_bid="0.52",
            ts=_NOW + timedelta(minutes=50),
        )
        exit_sig = gen.check_exit(update, entry)
        assert exit_sig is not None
        assert exit_sig.exit_reason == ExitReason.TIMEOUT

    def test_no_exit_when_conditions_not_met(self) -> None:
        """No exit conditions met → None."""
        gen = SignalGenerator(_CONFIG)
        entry = _entry()
        update = _update(
            yes_bid="0.52",
            ts=_NOW + timedelta(minutes=10),
        )
        assert gen.check_exit(update, entry) is None

    def test_pnl_and_hold_calculated(self) -> None:
        """Realized P&L and hold minutes are correct."""
        gen = SignalGenerator(_CONFIG)
        entry = _entry(entry_price="0.50", target_exit="0.60")
        update = _update(
            yes_bid="0.61",
            ts=_NOW + timedelta(minutes=15),
        )
        exit_sig = gen.check_exit(update, entry)
        assert exit_sig is not None
        assert exit_sig.realized_pnl == Decimal("0.11")
        assert float(exit_sig.hold_minutes) == 15.0

    def test_no_side_uses_no_bid(self) -> None:
        """Side='no' checks no_bid for exit."""
        gen = SignalGenerator(_CONFIG)
        entry = _entry(side="no", target_exit="0.50", stop_loss="0.30")
        update = _update(
            no_bid="0.51",
            ts=_NOW + timedelta(minutes=5),
        )
        exit_sig = gen.check_exit(update, entry)
        assert exit_sig is not None
        assert exit_sig.exit_reason == ExitReason.REVERSION


class TestCreateTicket:
    """Tests for SignalGenerator.create_ticket."""

    def test_ticket_has_both_legs(self) -> None:
        """Ticket contains buy and sell legs."""
        gen = SignalGenerator(_CONFIG)
        entry = _entry()
        ev = _event()
        ticket = gen.create_ticket(entry, ev)
        assert ticket is not None
        assert ticket.leg_1["action"] == "buy"
        assert ticket.leg_2["action"] == "sell"
        assert ticket.leg_1["venue"] == "polymarket"

    def test_ticket_type_is_flippening(self) -> None:
        """Ticket type is 'flippening'."""
        gen = SignalGenerator(_CONFIG)
        entry = _entry()
        ev = _event()
        ticket = gen.create_ticket(entry, ev)
        assert ticket is not None
        assert ticket.ticket_type == "flippening"

    def test_expected_cost_and_profit(self) -> None:
        """Expected cost and profit calculated from entry/target."""
        gen = SignalGenerator(_CONFIG)
        entry = _entry(entry_price="0.50", target_exit="0.605")
        ev = _event()
        ticket = gen.create_ticket(entry, ev)
        assert ticket is not None
        # expected_cost is the dollar position (suggested_size_usd)
        assert ticket.expected_cost == Decimal("80.00")
        # num_contracts = 80 / 0.50 = 160; profit = 0.105 * 160 = 16.80
        num_contracts = Decimal("80.00") / Decimal("0.50")
        expected_profit = Decimal("0.105") * num_contracts
        assert ticket.expected_profit == expected_profit

    def test_create_ticket_skips_below_min_profit(self) -> None:
        """Ticket is None when expected profit is below config threshold."""
        config = FlippeningConfig(enabled=True, min_expected_profit_usd=100.0)
        gen = SignalGenerator(config)
        entry = _entry(entry_price="0.50", target_exit="0.505")
        ev = _event()
        ticket = gen.create_ticket(entry, ev)
        assert ticket is None

    def test_create_ticket_respects_low_threshold(self) -> None:
        """Ticket is created when profit exceeds low threshold."""
        config = FlippeningConfig(enabled=True, min_expected_profit_usd=0.01)
        gen = SignalGenerator(config)
        entry = _entry(entry_price="0.50", target_exit="0.605")
        ev = _event()
        ticket = gen.create_ticket(entry, ev)
        assert ticket is not None
