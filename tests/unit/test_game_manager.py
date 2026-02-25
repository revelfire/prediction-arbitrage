"""Tests for game lifecycle management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arb_scanner.flippening.game_manager import GameManager
from arb_scanner.models.config import FlippeningConfig
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
        event, exit_sig = gm.process(update)
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
        _, exit_sig = gm.process(resolution_update)

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
