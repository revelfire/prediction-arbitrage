"""Spike detection and confidence scoring for flippening events."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

import structlog

from arb_scanner.models.config import FlippeningConfig, SportOverride
from arb_scanner.models.flippening import (
    Baseline,
    FlippeningEvent,
    PriceUpdate,
    SpikeDirection,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.spike_detector",
)


class SpikeDetector:
    """Detect emotional overreaction spikes in live sports markets.

    Compares current prices to baseline, checks recency and direction,
    then scores confidence using a weighted multi-factor model.
    """

    def __init__(self, config: FlippeningConfig) -> None:
        """Initialise with flippening configuration.

        Args:
            config: Flippening engine configuration.
        """
        self._config = config

    def check_spike(
        self,
        update: PriceUpdate,
        baseline: Baseline,
        price_history: deque[PriceUpdate],
    ) -> FlippeningEvent | None:
        """Check whether the current price represents a spike.

        Args:
            update: Current price update.
            baseline: Pre-spike baseline odds.
            price_history: Recent price history for the game.

        Returns:
            FlippeningEvent if spike detected, else None.
        """
        yes_mid = _yes_mid(update)
        deviation = abs(baseline.yes_price - yes_mid)
        threshold = Decimal(str(self._get_threshold(baseline.sport)))

        if deviation < threshold:
            return None

        if not self._was_near_baseline_recently(baseline, price_history):
            return None

        if not self._is_against_favorite(baseline, update):
            return None

        confidence = self._score_confidence(
            deviation,
            baseline,
            update,
            price_history,
        )
        min_conf = self._get_min_confidence(baseline.sport)
        if confidence < min_conf:
            return None

        if baseline.yes_price >= Decimal("0.50"):
            direction = (
                SpikeDirection.FAVORITE_DROP
                if yes_mid < baseline.yes_price
                else SpikeDirection.UNDERDOG_RISE
            )
        else:
            direction = (
                SpikeDirection.UNDERDOG_RISE
                if yes_mid > baseline.yes_price
                else SpikeDirection.FAVORITE_DROP
            )

        magnitude_pct = deviation / baseline.yes_price if baseline.yes_price else deviation

        event = FlippeningEvent(
            market_id=update.market_id,
            market_title="",
            baseline_yes=baseline.yes_price,
            spike_price=yes_mid,
            spike_magnitude_pct=magnitude_pct,
            spike_direction=direction,
            confidence=Decimal(str(round(confidence, 4))),
            sport=baseline.sport,
            detected_at=update.timestamp,
        )
        logger.info(
            "spike_detected",
            market_id=update.market_id,
            deviation=float(deviation),
            confidence=confidence,
            direction=direction.value,
        )
        return event

    def _was_near_baseline_recently(
        self,
        baseline: Baseline,
        history: deque[PriceUpdate],
    ) -> bool:
        """Check if price was near baseline within the spike window.

        Args:
            baseline: Pre-spike baseline.
            history: Recent price history.

        Returns:
            True if a recent price was close to baseline.
        """
        if not history:
            return False
        latest = history[-1]
        window_start = latest.timestamp - __import__(
            "datetime",
        ).timedelta(minutes=self._config.spike_window_minutes)
        near_threshold = Decimal("0.05")
        for upd in history:
            if upd.timestamp < window_start:
                continue
            mid = _yes_mid(upd)
            if abs(mid - baseline.yes_price) < near_threshold:
                return True
        return False

    def _is_against_favorite(
        self,
        baseline: Baseline,
        update: PriceUpdate,
    ) -> bool:
        """Check if the move is against the pre-game favorite.

        Args:
            baseline: Pre-spike baseline.
            update: Current price update.

        Returns:
            True if the spike direction opposes the favorite.
        """
        yes_mid = _yes_mid(update)
        if baseline.yes_price >= Decimal("0.50"):
            return yes_mid < baseline.yes_price
        return yes_mid > baseline.yes_price

    def _score_confidence(
        self,
        deviation: Decimal,
        baseline: Baseline,
        update: PriceUpdate,
        history: deque[PriceUpdate],
    ) -> float:
        """Score confidence using weighted multi-factor model.

        Args:
            deviation: Absolute price deviation from baseline.
            baseline: Pre-spike baseline.
            update: Current price update.
            history: Recent price history.

        Returns:
            Confidence score clamped to [0.0, 1.0].
        """
        weights = self._config.confidence_weights
        magnitude_score = min(float(deviation) / 0.30, 1.0)
        fav_strength = (
            max(
                float(max(baseline.yes_price, baseline.no_price)) - 0.50,
                0.0,
            )
            / 0.50
        )
        speed_score = self._compute_speed_score(baseline, history, update)
        override = self._config.sport_overrides.get(
            baseline.sport,
            SportOverride(),
        )
        sport_mod = override.confidence_modifier

        raw = (
            weights.magnitude * magnitude_score
            + weights.strength * fav_strength
            + weights.speed * speed_score
        ) * sport_mod

        if baseline.late_join:
            raw *= self._config.late_join_penalty

        if update.synthetic_spread:
            raw *= self._config.synthetic_spread_penalty

        return max(0.0, min(raw, 1.0))

    def _compute_speed_score(
        self,
        baseline: Baseline,
        history: deque[PriceUpdate],
        update: PriceUpdate,
    ) -> float:
        """Compute speed score based on how fast price moved.

        Args:
            baseline: Pre-spike baseline.
            history: Recent price history.
            update: Current price update.

        Returns:
            Speed score in [0.0, 1.0].
        """
        near_threshold = Decimal("0.05")
        last_near_time = baseline.captured_at
        for upd in reversed(history):
            mid = _yes_mid(upd)
            if abs(mid - baseline.yes_price) < near_threshold:
                last_near_time = upd.timestamp
                break
        elapsed_min = (update.timestamp - last_near_time).total_seconds() / 60.0
        return min(1.0 / max(elapsed_min, 0.5), 1.0)

    def _get_threshold(self, sport: str) -> float:
        """Get effective spike threshold for a sport.

        Args:
            sport: Sport identifier.

        Returns:
            Spike threshold percentage.
        """
        override = self._config.sport_overrides.get(sport)
        if override and override.spike_threshold_pct is not None:
            return override.spike_threshold_pct
        return self._config.spike_threshold_pct

    def _get_min_confidence(self, sport: str) -> float:
        """Get effective minimum confidence for a sport.

        Args:
            sport: Sport identifier.

        Returns:
            Minimum confidence threshold.
        """
        override = self._config.sport_overrides.get(sport)
        if override and override.min_confidence is not None:
            return override.min_confidence
        return self._config.min_confidence


def _yes_mid(update: PriceUpdate) -> Decimal:
    """Calculate YES midpoint price.

    Args:
        update: Price update.

    Returns:
        Midpoint of yes_bid and yes_ask.
    """
    return (update.yes_bid + update.yes_ask) / 2
