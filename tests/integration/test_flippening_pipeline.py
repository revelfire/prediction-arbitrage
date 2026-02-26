"""Integration tests for the full flippening pipeline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arb_scanner.flippening.game_manager import GameManager
from arb_scanner.flippening.signal_generator import SignalGenerator
from arb_scanner.flippening.spike_detector import SpikeDetector
from arb_scanner.models.config import FlippeningConfig
from arb_scanner.models.flippening import (
    ExitReason,
    PriceUpdate,
    SportsMarket,
)
from arb_scanner.models.market import Market, Venue

_NOW = datetime.now(tz=UTC)
_CONFIG = FlippeningConfig(
    enabled=True,
    spike_threshold_pct=0.10,
    min_confidence=0.0,
    reversion_target_pct=0.70,
    stop_loss_pct=0.15,
    max_hold_minutes=45,
    base_position_usd=100.0,
    max_position_usd=500.0,
)


def _market(event_id: str = "m1") -> Market:
    return Market(
        venue=Venue.POLYMARKET,
        event_id=event_id,
        title="Lakers vs Celtics",
        description="NBA",
        resolution_criteria="Official",
        yes_bid=Decimal("0.65"),
        yes_ask=Decimal("0.67"),
        no_bid=Decimal("0.32"),
        no_ask=Decimal("0.34"),
        volume_24h=Decimal("10000"),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )


def _sm(event_id: str = "m1", start: datetime | None = None) -> SportsMarket:
    return SportsMarket(
        market=_market(event_id),
        sport="nba",
        category="nba",
        game_start_time=start or _NOW - timedelta(minutes=30),
        token_id="tok-1",
    )


def _upd(
    market_id: str = "m1",
    yes_bid: str = "0.65",
    yes_ask: str = "0.67",
    ts: datetime | None = None,
) -> PriceUpdate:
    yb = Decimal(yes_bid)
    ya = Decimal(yes_ask)
    return PriceUpdate(
        market_id=market_id,
        token_id="tok-1",
        yes_bid=yb,
        yes_ask=ya,
        no_bid=Decimal("1") - ya,
        no_ask=Decimal("1") - yb,
        timestamp=ts or _NOW,
    )


class TestFullPipeline:
    """End-to-end pipeline tests."""

    def test_baseline_spike_reversion(self) -> None:
        """Full cycle: baseline → spike → entry → reversion exit."""
        game_mgr = GameManager(_CONFIG)
        spike_det = SpikeDetector(_CONFIG)
        signal_gen = SignalGenerator(_CONFIG)

        game_mgr.initialize([_sm()])

        # Baseline captured on first update
        game_mgr.process(_upd(ts=_NOW))
        state = game_mgr.get_state("m1")
        assert state is not None
        assert state.baseline is not None
        # Spike: YES drops from ~0.66 to ~0.45
        spike_time = _NOW + timedelta(seconds=30)
        spike_upd = _upd(yes_bid="0.44", yes_ask="0.46", ts=spike_time)
        game_mgr.process(spike_upd)

        event = spike_det.check_spike(
            spike_upd,
            state.baseline,
            state.price_history,
        )
        assert event is not None

        entry = signal_gen.create_entry(
            event,
            spike_upd.yes_ask,
            state.baseline,
        )
        assert entry.side == "yes"
        game_mgr.set_active_signal("m1", entry)

        # Reversion: price bounces back
        revert_time = _NOW + timedelta(minutes=10)
        revert_upd = _upd(
            yes_bid=str(entry.target_exit_price),
            yes_ask=str(entry.target_exit_price + Decimal("0.02")),
            ts=revert_time,
        )
        exit_sig = signal_gen.check_exit(revert_upd, entry)
        assert exit_sig is not None
        assert exit_sig.exit_reason == ExitReason.REVERSION
        assert exit_sig.realized_pnl > 0

    def test_baseline_spike_stop_loss(self) -> None:
        """Full cycle: baseline → spike → entry → stop loss exit."""
        game_mgr = GameManager(_CONFIG)
        spike_det = SpikeDetector(_CONFIG)
        signal_gen = SignalGenerator(_CONFIG)

        game_mgr.initialize([_sm()])
        game_mgr.process(_upd(ts=_NOW))
        state = game_mgr.get_state("m1")
        assert state is not None

        spike_upd = _upd(
            yes_bid="0.44",
            yes_ask="0.46",
            ts=_NOW + timedelta(seconds=30),
        )
        game_mgr.process(spike_upd)
        event = spike_det.check_spike(
            spike_upd,
            state.baseline,
            state.price_history,
        )
        assert event is not None
        entry = signal_gen.create_entry(
            event,
            spike_upd.yes_ask,
            state.baseline,
        )
        game_mgr.set_active_signal("m1", entry)

        # Further drop below stop loss
        drop_upd = _upd(
            yes_bid=str(entry.stop_loss_price - Decimal("0.01")),
            yes_ask=str(entry.stop_loss_price + Decimal("0.01")),
            ts=_NOW + timedelta(minutes=5),
        )
        exit_sig = signal_gen.check_exit(drop_upd, entry)
        assert exit_sig is not None
        assert exit_sig.exit_reason == ExitReason.STOP_LOSS

    def test_baseline_spike_timeout(self) -> None:
        """Full cycle: baseline → spike → entry → timeout exit."""
        game_mgr = GameManager(_CONFIG)
        spike_det = SpikeDetector(_CONFIG)
        signal_gen = SignalGenerator(_CONFIG)

        game_mgr.initialize([_sm()])
        game_mgr.process(_upd(ts=_NOW))
        state = game_mgr.get_state("m1")
        assert state is not None

        spike_upd = _upd(
            yes_bid="0.44",
            yes_ask="0.46",
            ts=_NOW + timedelta(seconds=30),
        )
        game_mgr.process(spike_upd)
        event = spike_det.check_spike(
            spike_upd,
            state.baseline,
            state.price_history,
        )
        assert event is not None
        entry = signal_gen.create_entry(
            event,
            spike_upd.yes_ask,
            state.baseline,
        )

        # No reversion, timeout at 50 minutes
        timeout_upd = _upd(
            yes_bid="0.48",
            yes_ask="0.50",
            ts=_NOW + timedelta(minutes=50),
        )
        exit_sig = signal_gen.check_exit(timeout_upd, entry)
        assert exit_sig is not None
        assert exit_sig.exit_reason == ExitReason.TIMEOUT

    def test_late_join_reduces_confidence(self) -> None:
        """Late join penalty reduces confidence score."""
        config_penalty = FlippeningConfig(
            enabled=True,
            spike_threshold_pct=0.10,
            min_confidence=0.0,
            late_join_penalty=0.50,
        )
        game_mgr = GameManager(config_penalty)
        spike_det = SpikeDetector(config_penalty)

        game_mgr.initialize([_sm()])
        game_mgr.process(_upd(ts=_NOW))

        state = game_mgr.get_state("m1")
        assert state is not None
        assert state.baseline is not None
        assert state.baseline.late_join is True  # Already live

        spike_upd = _upd(
            yes_bid="0.44",
            yes_ask="0.46",
            ts=_NOW + timedelta(seconds=30),
        )
        game_mgr.process(spike_upd)
        event = spike_det.check_spike(
            spike_upd,
            state.baseline,
            state.price_history,
        )
        assert event is not None
        assert float(event.confidence) < 1.0

    def test_multiple_games_independent(self) -> None:
        """Two games: spike in one doesn't affect the other."""
        game_mgr = GameManager(_CONFIG)
        spike_det = SpikeDetector(_CONFIG)

        m1 = _market("m1")
        m2 = Market(
            venue=Venue.POLYMARKET,
            event_id="m2",
            title="Heat vs Knicks",
            description="NBA",
            resolution_criteria="Official",
            yes_bid=Decimal("0.55"),
            yes_ask=Decimal("0.57"),
            no_bid=Decimal("0.42"),
            no_ask=Decimal("0.44"),
            volume_24h=Decimal("5000"),
            fees_pct=Decimal("0.02"),
            fee_model="on_winnings",
            last_updated=_NOW,
        )
        sm1 = SportsMarket(
            market=m1,
            sport="nba",
            category="nba",
            game_start_time=_NOW - timedelta(minutes=30),
            token_id="tok-1",
        )
        sm2 = SportsMarket(
            market=m2,
            sport="nba",
            category="nba",
            game_start_time=_NOW - timedelta(minutes=20),
            token_id="tok-2",
        )
        game_mgr.initialize([sm1, sm2])
        game_mgr.process(_upd("m1", ts=_NOW))
        game_mgr.process(
            _upd(
                "m2",
                yes_bid="0.55",
                yes_ask="0.57",
                ts=_NOW,
            )
        )

        state1 = game_mgr.get_state("m1")
        state2 = game_mgr.get_state("m2")
        assert state1 is not None and state2 is not None

        # Spike only in m1
        spike = _upd(
            "m1",
            yes_bid="0.44",
            yes_ask="0.46",
            ts=_NOW + timedelta(seconds=30),
        )
        game_mgr.process(spike)
        ev1 = spike_det.check_spike(
            spike,
            state1.baseline,
            state1.price_history,
        )
        assert ev1 is not None

        # m2 no spike (same price)
        stable = _upd(
            "m2",
            yes_bid="0.54",
            yes_ask="0.56",
            ts=_NOW + timedelta(seconds=30),
        )
        game_mgr.process(stable)
        ev2 = spike_det.check_spike(
            stable,
            state2.baseline,
            state2.price_history,
        )
        assert ev2 is None

    def test_game_resolution_exits_active_signal(self) -> None:
        """Game resolving to YES emits RESOLUTION exit."""
        game_mgr = GameManager(_CONFIG)
        game_mgr.initialize([_sm()])
        game_mgr.process(_upd(ts=_NOW))

        from arb_scanner.models.flippening import EntrySignal

        entry = EntrySignal(
            event_id="e1",
            side="yes",
            entry_price=Decimal("0.50"),
            target_exit_price=Decimal("0.60"),
            stop_loss_price=Decimal("0.42"),
            suggested_size_usd=Decimal("80"),
            expected_profit_pct=Decimal("0.20"),
            max_hold_minutes=45,
            created_at=_NOW,
        )
        game_mgr.set_active_signal("m1", entry)

        # Resolution update
        resolve = _upd(
            yes_bid="0.99",
            yes_ask="1.00",
            ts=_NOW + timedelta(minutes=20),
        )
        _, exit_sig, _ = game_mgr.process(resolve)
        assert exit_sig is not None
        assert exit_sig.exit_reason == ExitReason.RESOLUTION
        assert exit_sig.exit_price == Decimal("1.00")

    def test_no_spike_on_gradual_drift(self) -> None:
        """Slow drift does not trigger spike detection."""
        game_mgr = GameManager(_CONFIG)
        spike_det = SpikeDetector(_CONFIG)

        game_mgr.initialize([_sm()])
        game_mgr.process(_upd(ts=_NOW))
        state = game_mgr.get_state("m1")
        assert state is not None

        # Gradual drift — all updates are far from baseline
        for i in range(10):
            t = _NOW + timedelta(minutes=i + 1)
            price = Decimal("0.65") - Decimal("0.02") * (i + 1)
            game_mgr.process(
                _upd(
                    yes_bid=str(price - Decimal("0.01")),
                    yes_ask=str(price + Decimal("0.01")),
                    ts=t,
                )
            )

        # Final update still drifted
        final = _upd(
            yes_bid="0.42",
            yes_ask="0.44",
            ts=_NOW + timedelta(minutes=12),
        )
        game_mgr.process(final)
        event = spike_det.check_spike(
            final,
            state.baseline,
            state.price_history,
        )
        # Should be None — price hasn't been near baseline recently
        assert event is None

    def test_second_spike_blocked_while_signal_active(self) -> None:
        """EC-002: Second spike blocked when signal already active."""
        game_mgr = GameManager(_CONFIG)
        game_mgr.initialize([_sm()])
        game_mgr.process(_upd(ts=_NOW))
        state = game_mgr.get_state("m1")
        assert state is not None

        from arb_scanner.models.flippening import EntrySignal

        entry = EntrySignal(
            event_id="e1",
            side="yes",
            entry_price=Decimal("0.46"),
            target_exit_price=Decimal("0.59"),
            stop_loss_price=Decimal("0.39"),
            suggested_size_usd=Decimal("80"),
            expected_profit_pct=Decimal("0.20"),
            max_hold_minutes=45,
            created_at=_NOW,
        )
        game_mgr.set_active_signal("m1", entry)
        assert game_mgr.has_open_signal("m1") is True

    def test_second_spike_allowed_after_exit(self) -> None:
        """EC-002: New spike allowed after previous signal exits."""
        game_mgr = GameManager(_CONFIG)

        game_mgr.initialize([_sm()])
        game_mgr.process(_upd(ts=_NOW))

        from arb_scanner.models.flippening import EntrySignal

        entry = EntrySignal(
            event_id="e1",
            side="yes",
            entry_price=Decimal("0.46"),
            target_exit_price=Decimal("0.59"),
            stop_loss_price=Decimal("0.39"),
            suggested_size_usd=Decimal("80"),
            expected_profit_pct=Decimal("0.20"),
            max_hold_minutes=45,
            created_at=_NOW,
        )
        game_mgr.set_active_signal("m1", entry)
        game_mgr.clear_active_signal("m1")
        assert game_mgr.has_open_signal("m1") is False
