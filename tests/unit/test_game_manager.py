"""Tests for game lifecycle management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arb_scanner.flippening.game_manager import GameManager
from arb_scanner.models.config import CategoryConfig, FlippeningConfig
from arb_scanner.models.flippening import (
    EntrySignal,
    ExitReason,
    GamePhase,
    PriceUpdate,
    SportsMarket,
)
from arb_scanner.models.market import Market, Venue

_NOW = datetime.now(tz=UTC)
_CONFIG = FlippeningConfig(enabled=True)


def _market(event_id: str = "m1") -> Market:
    return Market(
        venue=Venue.POLYMARKET,
        event_id=event_id,
        title="Lakers vs Celtics",
        description="NBA game",
        resolution_criteria="Official NBA",
        yes_bid=Decimal("0.65"),
        yes_ask=Decimal("0.67"),
        no_bid=Decimal("0.32"),
        no_ask=Decimal("0.34"),
        volume_24h=Decimal("10000"),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )


def _sports_market(
    event_id: str = "m1",
    start_time: datetime | None = None,
) -> SportsMarket:
    return SportsMarket(
        market=_market(event_id),
        sport="nba",
        category="nba",
        game_start_time=start_time,
        token_id="tok-1",
    )


def _update(
    market_id: str = "m1",
    yes_bid: str = "0.65",
    yes_ask: str = "0.67",
    ts: datetime | None = None,
) -> PriceUpdate:
    return PriceUpdate(
        market_id=market_id,
        token_id="tok-1",
        yes_bid=Decimal(yes_bid),
        yes_ask=Decimal(yes_ask),
        no_bid=Decimal("0.32"),
        no_ask=Decimal("0.34"),
        timestamp=ts or _NOW,
    )


def _entry_signal(event_id: str = "e1") -> EntrySignal:
    return EntrySignal(
        event_id=event_id,
        side="yes",
        entry_price=Decimal("0.52"),
        target_exit_price=Decimal("0.62"),
        stop_loss_price=Decimal("0.44"),
        suggested_size_usd=Decimal("100"),
        expected_profit_pct=Decimal("0.192"),
        max_hold_minutes=45,
        created_at=_NOW,
    )


class TestInitialize:
    """Tests for GameManager.initialize."""

    def test_upcoming_for_future_game(self) -> None:
        """Game with future start time is set to UPCOMING."""
        gm = GameManager(_CONFIG)
        sm = _sports_market(start_time=_NOW + timedelta(minutes=10))
        gm.initialize([sm])
        state = gm.get_state("m1")
        assert state is not None
        assert state.phase == GamePhase.UPCOMING

    def test_live_for_past_start(self) -> None:
        """Game with past start time is set to LIVE."""
        gm = GameManager(_CONFIG)
        sm = _sports_market(start_time=_NOW - timedelta(minutes=30))
        gm.initialize([sm])
        state = gm.get_state("m1")
        assert state is not None
        assert state.phase == GamePhase.LIVE

    def test_skips_far_future_game(self) -> None:
        """Game far in the future (beyond pre_game_window) is skipped."""
        gm = GameManager(_CONFIG)
        sm = _sports_market(start_time=_NOW + timedelta(hours=5))
        gm.initialize([sm])
        assert gm.get_state("m1") is None


class TestProcess:
    """Tests for GameManager.process."""

    def test_advances_upcoming_to_live(self) -> None:
        """UPCOMING game transitions to LIVE when start time passes."""
        gm = GameManager(_CONFIG)
        sm = _sports_market(start_time=_NOW + timedelta(seconds=1))
        gm.initialize([sm])

        update = _update(ts=_NOW + timedelta(seconds=2))
        gm.process(update)

        state = gm.get_state("m1")
        assert state is not None
        assert state.phase == GamePhase.LIVE
        assert state.baseline is not None

    def test_baseline_captured_on_live_transition(self) -> None:
        """Baseline is captured when game goes live."""
        gm = GameManager(_CONFIG)
        sm = _sports_market(start_time=_NOW - timedelta(seconds=1))
        gm.initialize([sm])

        update = _update(yes_bid="0.64", yes_ask="0.66")
        gm.process(update)

        state = gm.get_state("m1")
        assert state is not None
        assert state.baseline is not None
        assert state.baseline.yes_price == Decimal("0.65")  # mid

    def test_returns_none_for_unknown_market(self) -> None:
        """Unknown market returns no events."""
        gm = GameManager(_CONFIG)
        update = _update(market_id="unknown")
        event, exit_sig, _ = gm.process(update)
        assert event is None
        assert exit_sig is None

    def test_resolution_exit_signal(self) -> None:
        """Game resolving to YES emits exit signal for active position."""
        gm = GameManager(_CONFIG)
        sm = _sports_market(start_time=_NOW - timedelta(minutes=30))
        gm.initialize([sm])

        gm.process(_update())

        entry = _entry_signal()
        gm.set_active_signal("m1", entry)

        resolution_update = _update(
            yes_bid="0.99",
            yes_ask="1.00",
            ts=_NOW + timedelta(minutes=20),
        )
        _, exit_sig, _ = gm.process(resolution_update)

        assert exit_sig is not None
        assert exit_sig.exit_reason == ExitReason.RESOLUTION
        assert exit_sig.exit_price == Decimal("1.00")


class TestOpenSignal:
    """Tests for active signal tracking."""

    def test_has_open_signal_false(self) -> None:
        """No open signal returns False."""
        gm = GameManager(_CONFIG)
        sm = _sports_market(start_time=_NOW - timedelta(minutes=5))
        gm.initialize([sm])
        assert gm.has_open_signal("m1") is False

    def test_has_open_signal_true(self) -> None:
        """Set signal returns True."""
        gm = GameManager(_CONFIG)
        sm = _sports_market(start_time=_NOW - timedelta(minutes=5))
        gm.initialize([sm])
        gm.set_active_signal("m1", _entry_signal())
        assert gm.has_open_signal("m1") is True

    def test_clear_active_signal(self) -> None:
        """Clearing signal allows new entries."""
        gm = GameManager(_CONFIG)
        sm = _sports_market(start_time=_NOW - timedelta(minutes=5))
        gm.initialize([sm])
        gm.set_active_signal("m1", _entry_signal())
        gm.clear_active_signal("m1")
        assert gm.has_open_signal("m1") is False


class TestDriftUpdate:
    """Tests for baseline drift handling."""

    def test_gradual_drift_updates_baseline(self) -> None:
        """Slow, sustained drift updates the baseline."""
        gm = GameManager(_CONFIG)
        sm = _sports_market(start_time=_NOW - timedelta(minutes=30))
        gm.initialize([sm])

        gm.process(_update(yes_bid="0.65", yes_ask="0.67", ts=_NOW))

        state = gm.get_state("m1")
        assert state is not None
        original_baseline = state.baseline
        assert original_baseline is not None

        for i in range(1, 7):
            t = _NOW + timedelta(minutes=i)
            price = Decimal("0.65") + Decimal("0.005") * i
            gm.process(
                _update(
                    yes_bid=str(price),
                    yes_ask=str(price + Decimal("0.02")),
                    ts=t,
                )
            )

        state = gm.get_state("m1")
        assert state is not None
        assert state.baseline is not None
        assert state.baseline.yes_price != original_baseline.yes_price

    def test_sharp_spike_does_not_update_baseline(self) -> None:
        """Sharp price spikes do not update baseline."""
        gm = GameManager(_CONFIG)
        sm = _sports_market(start_time=_NOW - timedelta(minutes=30))
        gm.initialize([sm])
        gm.process(_update(ts=_NOW))

        state = gm.get_state("m1")
        assert state is not None
        original_yes = state.baseline.yes_price if state.baseline else None

        gm.process(
            _update(
                yes_bid="0.45",
                yes_ask="0.47",
                ts=_NOW + timedelta(seconds=30),
            )
        )

        state = gm.get_state("m1")
        assert state is not None
        assert state.baseline is not None
        assert state.baseline.yes_price == original_yes


class TestRemoveGame:
    """Tests for game removal."""

    def test_remove_game(self) -> None:
        """Removed game is no longer tracked."""
        gm = GameManager(_CONFIG)
        sm = _sports_market(start_time=_NOW - timedelta(minutes=5))
        gm.initialize([sm])
        assert gm.active_game_count == 1
        gm.remove_game("m1")
        assert gm.active_game_count == 0
        assert gm.get_state("m1") is None


def _category_market(
    event_id: str = "m1",
    start_time: datetime | None = None,
    category: str = "nba",
    category_type: str = "sport",
) -> SportsMarket:
    """Create a CategoryMarket with configurable category_type."""
    return SportsMarket(
        market=_market(event_id),
        sport=category if category_type == "sport" else "",
        category=category,
        category_type=category_type,
        game_start_time=start_time,
        token_id="tok-1",
    )


class TestSportFilterInit:
    """Tests for sport market filtering in initialize()."""

    def test_sport_without_start_time_skipped(self) -> None:
        """Sport markets with no game_start_time are skipped."""
        gm = GameManager(_CONFIG)
        sm = _category_market(start_time=None, category_type="sport")
        gm.initialize([sm])
        assert gm.get_state("m1") is None

    def test_sport_with_stale_start_time_skipped(self) -> None:
        """Sport markets with game_start_time far in the past are skipped."""
        gm = GameManager(_CONFIG)
        sm = _category_market(
            start_time=_NOW - timedelta(hours=10),
            category_type="sport",
        )
        gm.initialize([sm])
        assert gm.get_state("m1") is None

    def test_sport_with_recent_start_time_allowed(self) -> None:
        """Sport markets with recent game_start_time are accepted."""
        gm = GameManager(_CONFIG)
        sm = _category_market(
            start_time=_NOW - timedelta(hours=1),
            category_type="sport",
        )
        gm.initialize([sm])
        assert gm.get_state("m1") is not None

    def test_non_sport_without_start_time_allowed(self) -> None:
        """Non-sport markets without game_start_time are accepted."""
        cfg = FlippeningConfig(
            enabled=True,
            categories={"crypto": CategoryConfig(category_type="crypto")},
        )
        gm = GameManager(cfg)
        sm = _category_market(
            start_time=None,
            category="crypto",
            category_type="crypto",
        )
        gm.initialize([sm])
        assert gm.get_state("m1") is not None


class TestBaselineSanity:
    """Tests for baseline rejection and recapture."""

    def test_extreme_low_baseline_rejected(self) -> None:
        """Baseline with yes_price < min_baseline_price is rejected."""
        gm = GameManager(_CONFIG)
        sm = _category_market(start_time=_NOW - timedelta(minutes=5))
        gm.initialize([sm])
        state = gm.get_state("m1")
        assert state is not None
        update = _update(yes_bid="0.001", yes_ask="0.003")
        gm.process(update)
        assert state.baseline is None

    def test_extreme_high_baseline_rejected(self) -> None:
        """Baseline with yes_price > max_baseline_price is rejected."""
        gm = GameManager(_CONFIG)
        sm = _category_market(start_time=_NOW - timedelta(minutes=5))
        gm.initialize([sm])
        state = gm.get_state("m1")
        assert state is not None
        update = _update(yes_bid="0.97", yes_ask="0.99")
        gm.process(update)
        assert state.baseline is None

    def test_normal_baseline_accepted(self) -> None:
        """Baseline within bounds is accepted."""
        gm = GameManager(_CONFIG)
        sm = _category_market(start_time=_NOW - timedelta(minutes=5))
        gm.initialize([sm])
        state = gm.get_state("m1")
        assert state is not None
        update = _update(yes_bid="0.50", yes_ask="0.52")
        gm.process(update)
        assert state.baseline is not None
        assert state.baseline.yes_price == Decimal("0.51")

    def test_stale_baseline_recaptured(self) -> None:
        """Baseline is recaptured when deviation exceeds threshold."""
        cfg = FlippeningConfig(enabled=True, max_deviation_recapture_pct=100.0)
        gm = GameManager(cfg)
        sm = _category_market(start_time=_NOW - timedelta(minutes=30))
        gm.initialize([sm])

        gm.process(_update(yes_bid="0.10", yes_ask="0.12", ts=_NOW))
        state = gm.get_state("m1")
        assert state is not None
        assert state.baseline is not None
        old_yes = state.baseline.yes_price

        gm.process(
            _update(
                yes_bid="0.50",
                yes_ask="0.52",
                ts=_NOW + timedelta(minutes=1),
            )
        )
        assert state.baseline is not None
        assert state.baseline.yes_price != old_yes

    def test_moderate_deviation_no_recapture(self) -> None:
        """Moderate deviation does not trigger recapture."""
        gm = GameManager(_CONFIG)
        sm = _category_market(start_time=_NOW - timedelta(minutes=30))
        gm.initialize([sm])

        gm.process(_update(yes_bid="0.50", yes_ask="0.52", ts=_NOW))
        state = gm.get_state("m1")
        assert state is not None
        assert state.baseline is not None
        old_yes = state.baseline.yes_price

        gm.process(
            _update(
                yes_bid="0.55",
                yes_ask="0.57",
                ts=_NOW + timedelta(minutes=1),
            )
        )
        assert state.baseline is not None
        assert state.baseline.yes_price == old_yes


class TestDeviationCap:
    """Tests for deviation clamping in _push_price_tick."""

    def test_deviation_capped_positive(self) -> None:
        """Positive deviation is capped at 999.99."""
        from arb_scanner.flippening._orch_processing import _push_price_tick
        from arb_scanner.flippening.game_manager import GameState
        from arb_scanner.flippening.price_ring_buffer import (
            PriceRingBuffer,
            get_shared_buffer,
            set_shared_buffer,
        )
        from arb_scanner.models.flippening import Baseline

        set_shared_buffer(PriceRingBuffer(max_per_market=100))
        state = GameState(
            market_id="m1",
            market_title="Test",
            token_id="tok-1",
            sport="nba",
            phase=GamePhase.LIVE,
            baseline=Baseline(
                market_id="m1",
                token_id="tok-1",
                sport="nba",
                category="nba",
                category_type="sport",
                yes_price=Decimal("0.001"),
                no_price=Decimal("0.999"),
                captured_at=_NOW,
            ),
        )
        update = _update(yes_bid="0.50", yes_ask="0.52")
        _push_price_tick(update, state)
        buf = get_shared_buffer()
        assert buf is not None
        ticks = buf.get_history("m1")
        assert len(ticks) >= 1
        assert ticks[-1].deviation_pct <= 999.99

    def test_deviation_capped_negative(self) -> None:
        """Negative deviation is capped at -999.99."""
        from arb_scanner.flippening._orch_processing import _push_price_tick
        from arb_scanner.flippening.game_manager import GameState
        from arb_scanner.flippening.price_ring_buffer import (
            PriceRingBuffer,
            get_shared_buffer,
            set_shared_buffer,
        )
        from arb_scanner.models.flippening import Baseline

        set_shared_buffer(PriceRingBuffer(max_per_market=100))
        state = GameState(
            market_id="m2",
            market_title="Test2",
            token_id="tok-2",
            sport="nba",
            phase=GamePhase.LIVE,
            baseline=Baseline(
                market_id="m2",
                token_id="tok-2",
                sport="nba",
                category="nba",
                category_type="sport",
                yes_price=Decimal("0.999"),
                no_price=Decimal("0.001"),
                captured_at=_NOW,
            ),
        )
        update = PriceUpdate(
            market_id="m2",
            token_id="tok-2",
            yes_bid=Decimal("0.001"),
            yes_ask=Decimal("0.003"),
            no_bid=Decimal("0.99"),
            no_ask=Decimal("1.00"),
            timestamp=_NOW,
        )
        _push_price_tick(update, state)
        buf = get_shared_buffer()
        assert buf is not None
        ticks = buf.get_history("m2")
        assert len(ticks) >= 1
        assert ticks[-1].deviation_pct >= -999.99
