"""Spike detection and confidence scoring for flippening events."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

import structlog

from arb_scanner.models.config import FlippeningConfig
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
    """Detect emotional overreaction spikes in live markets.

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
        cat_key = baseline.category or baseline.sport
        threshold = Decimal(str(self._get_threshold(cat_key)))

        if deviation < threshold:
            return None

        if not self._was_near_baseline_recently(baseline, price_history):
            return None

        if not self._is_against_favorite(baseline, update):
            return None

        confidence = self._score_confidence(deviation, baseline, update, price_history)
        min_conf = self._get_min_confidence(cat_key)
        if confidence < min_conf:
            return None

        direction = _determine_direction(baseline, yes_mid)
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
            category=baseline.category,
            category_type=baseline.category_type,
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
        """Check if price was near baseline within the spike window."""
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

    def _is_against_favorite(self, baseline: Baseline, update: PriceUpdate) -> bool:
        """Check if the move is against the pre-game favorite."""
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

        Returns:
            Confidence score clamped to [0.0, 1.0].
        """
        weights = self._config.confidence_weights
        magnitude_score = min(float(deviation) / 0.30, 1.0)
        fav_strength = max(float(max(baseline.yes_price, baseline.no_price)) - 0.50, 0.0) / 0.50
        speed_score = self._compute_speed_score(baseline, history, update)
        cat_cfg = self._config.categories.get(baseline.category or baseline.sport)
        cat_mod = cat_cfg.confidence_modifier if cat_cfg else 1.0

        raw = (
            weights.magnitude * magnitude_score
            + weights.strength * fav_strength
            + weights.speed * speed_score
        ) * cat_mod

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
        """Compute speed score based on how fast price moved."""
        near_threshold = Decimal("0.05")
        last_near_time = baseline.captured_at
        for upd in reversed(history):
            mid = _yes_mid(upd)
            if abs(mid - baseline.yes_price) < near_threshold:
                last_near_time = upd.timestamp
                break
        elapsed_min = (update.timestamp - last_near_time).total_seconds() / 60.0
        return min(1.0 / max(elapsed_min, 0.5), 1.0)

    def _get_threshold(self, category: str) -> float:
        """Get effective spike threshold for a category.

        Args:
            category: Category identifier.

        Returns:
            Spike threshold percentage.
        """
        cat_cfg = self._config.categories.get(category)
        if cat_cfg and cat_cfg.spike_threshold_pct is not None:
            return cat_cfg.spike_threshold_pct
        return self._config.spike_threshold_pct

    def _get_min_confidence(self, category: str) -> float:
        """Get effective minimum confidence for a category.

        Args:
            category: Category identifier.

        Returns:
            Minimum confidence threshold.
        """
        cat_cfg = self._config.categories.get(category)
        if cat_cfg and cat_cfg.min_confidence is not None:
            return cat_cfg.min_confidence
        return self._config.min_confidence


def _determine_direction(baseline: Baseline, yes_mid: Decimal) -> SpikeDirection:
    """Determine spike direction relative to the favorite."""
    if baseline.yes_price >= Decimal("0.50"):
        return (
            SpikeDirection.FAVORITE_DROP
            if yes_mid < baseline.yes_price
            else SpikeDirection.UNDERDOG_RISE
        )
    return (
        SpikeDirection.UNDERDOG_RISE
        if yes_mid > baseline.yes_price
        else SpikeDirection.FAVORITE_DROP
    )


def _yes_mid(update: PriceUpdate) -> Decimal:
    """Calculate YES midpoint price."""
    return (update.yes_bid + update.yes_ask) / 2
