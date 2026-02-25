"""Tests for spike detection and confidence scoring."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arb_scanner.flippening.spike_detector import SpikeDetector
from arb_scanner.models.config import (
    FlippeningConfig,
    SportOverride,
)
from arb_scanner.models.flippening import (
    Baseline,
    PriceUpdate,
    SpikeDirection,
)

_NOW = datetime.now(tz=UTC)
_CONFIG = FlippeningConfig(
    enabled=True,
    spike_threshold_pct=0.15,
    min_confidence=0.50,
)


def _baseline(
    yes: str = "0.65",
    sport: str = "nba",
    late_join: bool = False,
) -> Baseline:
    return Baseline(
        market_id="m1",
        token_id="tok-1",
        yes_price=Decimal(yes),
        no_price=Decimal("1") - Decimal(yes),
        sport=sport,
        game_start_time=_NOW - timedelta(minutes=30),
        captured_at=_NOW,
        late_join=late_join,
    )


def _update(
    yes_bid: str = "0.65",
    yes_ask: str = "0.67",
    ts: datetime | None = None,
) -> PriceUpdate:
    return PriceUpdate(
        market_id="m1",
        token_id="tok-1",
        yes_bid=Decimal(yes_bid),
        yes_ask=Decimal(yes_ask),
        no_bid=Decimal("0.32"),
        no_ask=Decimal("0.34"),
        timestamp=ts or _NOW,
    )


def _history_near_baseline(
    baseline_yes: str = "0.65",
    count: int = 5,
) -> deque[PriceUpdate]:
    """Build history where prices are near the baseline."""
    history: deque[PriceUpdate] = deque(maxlen=200)
    base = Decimal(baseline_yes)
    for i in range(count):
        t = _NOW + timedelta(seconds=i * 10)
        history.append(
            _update(
                yes_bid=str(base - Decimal("0.01")),
                yes_ask=str(base + Decimal("0.01")),
                ts=t,
            )
        )
    return history


class TestNoSpike:
    """Tests for cases that should NOT produce a spike."""

    def test_deviation_below_threshold(self) -> None:
        """Small deviation returns None."""
        detector = SpikeDetector(_CONFIG)
        bl = _baseline(yes="0.65")
        update = _update(yes_bid="0.60", yes_ask="0.62")
        history = _history_near_baseline()
        assert detector.check_spike(update, bl, history) is None

    def test_gradual_drift_not_near_baseline_recently(self) -> None:
        """If price drifted gradually, no recent near-baseline update."""
        detector = SpikeDetector(_CONFIG)
        bl = _baseline(yes="0.65")
        # History far from baseline (already drifted)
        history: deque[PriceUpdate] = deque(maxlen=200)
        for i in range(5):
            t = _NOW + timedelta(seconds=i * 10)
            history.append(
                _update(
                    yes_bid="0.45",
                    yes_ask="0.47",
                    ts=t,
                )
            )
        update = _update(
            yes_bid="0.40",
            yes_ask="0.42",
            ts=_NOW + timedelta(seconds=60),
        )
        assert detector.check_spike(update, bl, history) is None

    def test_move_with_favorite_returns_none(self) -> None:
        """Move in same direction as favorite returns None."""
        detector = SpikeDetector(_CONFIG)
        bl = _baseline(yes="0.65")
        history = _history_near_baseline()
        # YES favorite, price goes UP (not against favorite)
        update = _update(
            yes_bid="0.84",
            yes_ask="0.86",
            ts=_NOW + timedelta(seconds=60),
        )
        assert detector.check_spike(update, bl, history) is None

    def test_below_min_confidence_returns_none(self) -> None:
        """Spike below min confidence returns None."""
        config = FlippeningConfig(
            enabled=True,
            spike_threshold_pct=0.10,
            min_confidence=0.99,  # very high bar
        )
        detector = SpikeDetector(config)
        bl = _baseline(yes="0.65")
        history = _history_near_baseline()
        update = _update(
            yes_bid="0.48",
            yes_ask="0.50",
            ts=_NOW + timedelta(seconds=60),
        )
        assert detector.check_spike(update, bl, history) is None


class TestSpikeDetected:
    """Tests for cases that should produce a spike."""

    def test_spike_detected_favorite_drops(self) -> None:
        """YES favorite drops → spike detected."""
        detector = SpikeDetector(_CONFIG)
        bl = _baseline(yes="0.65")
        history = _history_near_baseline()
        update = _update(
            yes_bid="0.44",
            yes_ask="0.46",
            ts=_NOW + timedelta(seconds=60),
        )
        event = detector.check_spike(update, bl, history)
        assert event is not None
        assert event.spike_direction == SpikeDirection.FAVORITE_DROP
        assert event.market_id == "m1"
        assert event.sport == "nba"

    def test_spike_detected_underdog_rises(self) -> None:
        """NO favorite (YES < 0.50), YES rises → spike detected."""
        detector = SpikeDetector(_CONFIG)
        bl = _baseline(yes="0.35")
        history = _history_near_baseline(baseline_yes="0.35")
        # YES rises from 0.35 to 0.55 → against NO favorite
        update = _update(
            yes_bid="0.54",
            yes_ask="0.56",
            ts=_NOW + timedelta(seconds=60),
        )
        event = detector.check_spike(update, bl, history)
        assert event is not None
        assert event.spike_direction == SpikeDirection.UNDERDOG_RISE


class TestConfidenceScoring:
    """Tests for confidence score factors."""

    def test_larger_magnitude_higher_confidence(self) -> None:
        """Larger deviation produces higher confidence."""
        detector = SpikeDetector(_CONFIG)
        bl = _baseline(yes="0.70")
        history = _history_near_baseline(baseline_yes="0.70")

        # Moderate drop
        upd_small = _update(
            yes_bid="0.50",
            yes_ask="0.52",
            ts=_NOW + timedelta(seconds=30),
        )
        ev_small = detector.check_spike(upd_small, bl, history)

        # Large drop
        upd_large = _update(
            yes_bid="0.35",
            yes_ask="0.37",
            ts=_NOW + timedelta(seconds=30),
        )
        ev_large = detector.check_spike(upd_large, bl, history)

        assert ev_small is not None
        assert ev_large is not None
        assert ev_large.confidence > ev_small.confidence

    def test_stronger_favorite_higher_confidence(self) -> None:
        """Stronger pre-game favorite gives higher confidence."""
        config = FlippeningConfig(
            enabled=True,
            spike_threshold_pct=0.10,
            min_confidence=0.0,
        )
        detector = SpikeDetector(config)

        # Moderate favorite (0.60)
        bl_mod = _baseline(yes="0.60")
        hist_mod = _history_near_baseline(baseline_yes="0.60")
        upd_mod = _update(
            yes_bid="0.44",
            yes_ask="0.46",
            ts=_NOW + timedelta(seconds=30),
        )
        ev_mod = detector.check_spike(upd_mod, bl_mod, hist_mod)

        # Strong favorite (0.80)
        bl_strong = _baseline(yes="0.80")
        hist_strong = _history_near_baseline(baseline_yes="0.80")
        upd_strong = _update(
            yes_bid="0.64",
            yes_ask="0.66",
            ts=_NOW + timedelta(seconds=30),
        )
        ev_strong = detector.check_spike(upd_strong, bl_strong, hist_strong)

        assert ev_mod is not None
        assert ev_strong is not None
        assert ev_strong.confidence > ev_mod.confidence

    def test_sport_override_threshold(self) -> None:
        """Sport override changes effective threshold."""
        config = FlippeningConfig(
            enabled=True,
            spike_threshold_pct=0.30,
            min_confidence=0.0,
            sport_overrides={
                "nba": SportOverride(spike_threshold_pct=0.10),
            },
        )
        detector = SpikeDetector(config)
        bl = _baseline(yes="0.65", sport="nba")
        history = _history_near_baseline()
        update = _update(
            yes_bid="0.48",
            yes_ask="0.50",
            ts=_NOW + timedelta(seconds=30),
        )
        event = detector.check_spike(update, bl, history)
        assert event is not None

    def test_sport_override_confidence_modifier(self) -> None:
        """Sport confidence_modifier scales the score."""
        config_base = FlippeningConfig(
            enabled=True,
            spike_threshold_pct=0.10,
            min_confidence=0.0,
        )
        config_boosted = FlippeningConfig(
            enabled=True,
            spike_threshold_pct=0.10,
            min_confidence=0.0,
            sport_overrides={
                "nba": SportOverride(confidence_modifier=1.5),
            },
        )
        bl = _baseline(yes="0.65", sport="nba")
        history = _history_near_baseline()
        update = _update(
            yes_bid="0.44",
            yes_ask="0.46",
            ts=_NOW + timedelta(seconds=30),
        )

        ev_base = SpikeDetector(config_base).check_spike(update, bl, history)
        ev_boost = SpikeDetector(config_boosted).check_spike(update, bl, history)

        assert ev_base is not None
        assert ev_boost is not None
        assert ev_boost.confidence >= ev_base.confidence

    def test_late_join_penalty(self) -> None:
        """Late join penalty reduces confidence."""
        config = FlippeningConfig(
            enabled=True,
            spike_threshold_pct=0.10,
            min_confidence=0.0,
            late_join_penalty=0.50,
        )
        detector = SpikeDetector(config)
        history = _history_near_baseline()
        update = _update(
            yes_bid="0.44",
            yes_ask="0.46",
            ts=_NOW + timedelta(seconds=30),
        )

        bl_normal = _baseline(yes="0.65", late_join=False)
        ev_normal = detector.check_spike(update, bl_normal, history)

        bl_late = _baseline(yes="0.65", late_join=True)
        ev_late = detector.check_spike(update, bl_late, history)

        assert ev_normal is not None
        assert ev_late is not None
        assert ev_late.confidence < ev_normal.confidence

    def test_confidence_clamped_to_unit(self) -> None:
        """Confidence never exceeds 1.0 even with high modifier."""
        config = FlippeningConfig(
            enabled=True,
            spike_threshold_pct=0.01,
            min_confidence=0.0,
            sport_overrides={
                "nba": SportOverride(confidence_modifier=5.0),
            },
        )
        detector = SpikeDetector(config)
        bl = _baseline(yes="0.85", sport="nba")
        history = _history_near_baseline(baseline_yes="0.85")
        update = _update(
            yes_bid="0.44",
            yes_ask="0.46",
            ts=_NOW + timedelta(seconds=10),
        )
        event = detector.check_spike(update, bl, history)
        assert event is not None
        assert float(event.confidence) <= 1.0
