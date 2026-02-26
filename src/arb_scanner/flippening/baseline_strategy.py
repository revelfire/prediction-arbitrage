"""Baseline capture strategies for the flippening mean reversion engine."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal

import structlog

from arb_scanner.models.flippening import Baseline, PriceUpdate

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.baseline_strategy",
)

_ROLLING_MIN_POINTS = 3  # EC-006: need at least 3 data points


class BaselineCapture:
    """Strategy-based baseline capture for different market categories."""

    @staticmethod
    def capture_first_price(
        market_id: str,
        token_id: str,
        sport: str,
        category: str,
        category_type: str,
        game_start_time: datetime | None,
        update: PriceUpdate,
        late_join: bool,
    ) -> Baseline:
        """Capture baseline from the first observed price midpoint.

        Args:
            market_id: Market identifier.
            token_id: CLOB token identifier.
            sport: Legacy sport identifier.
            category: Category identifier.
            category_type: Category type (sport, politics, etc.).
            game_start_time: Scheduled start time.
            update: Current price update.
            late_join: Whether joining an in-progress event.

        Returns:
            Baseline with midpoint prices.
        """
        yes_mid = (update.yes_bid + update.yes_ask) / 2
        no_mid = (update.no_bid + update.no_ask) / 2
        baseline = Baseline(
            market_id=market_id,
            token_id=token_id,
            yes_price=yes_mid,
            no_price=no_mid,
            sport=sport,
            category=category,
            category_type=category_type,
            baseline_strategy="first_price",
            game_start_time=game_start_time,
            captured_at=update.timestamp,
            late_join=late_join,
        )
        logger.info(
            "baseline_captured",
            market_id=market_id,
            strategy="first_price",
            yes=float(yes_mid),
            no=float(no_mid),
            late_join=late_join,
        )
        return baseline

    @staticmethod
    def capture_rolling_window(
        market_id: str,
        token_id: str,
        sport: str,
        category: str,
        category_type: str,
        game_start_time: datetime | None,
        price_history: deque[PriceUpdate],
        window_minutes: int,
    ) -> Baseline | None:
        """Capture baseline as rolling average of YES midpoints over a window.

        Returns None if fewer than 3 data points in the window (EC-006).

        Args:
            market_id: Market identifier.
            token_id: CLOB token identifier.
            sport: Legacy sport identifier.
            category: Category identifier.
            category_type: Category type.
            game_start_time: Scheduled start time.
            price_history: Recent price updates.
            window_minutes: Window size in minutes.

        Returns:
            Baseline with rolling average, or None if insufficient data.
        """
        if len(price_history) < _ROLLING_MIN_POINTS:
            return None

        latest_ts = price_history[-1].timestamp
        cutoff = latest_ts - timedelta(minutes=window_minutes)
        window_updates = [u for u in price_history if u.timestamp >= cutoff]

        if len(window_updates) < _ROLLING_MIN_POINTS:
            return None

        yes_sum = sum((u.yes_bid + u.yes_ask) / 2 for u in window_updates)
        no_sum = sum((u.no_bid + u.no_ask) / 2 for u in window_updates)
        count = Decimal(str(len(window_updates)))
        yes_avg = yes_sum / count
        no_avg = no_sum / count

        logger.debug(
            "rolling_baseline_captured",
            market_id=market_id,
            window_points=len(window_updates),
            yes_avg=float(yes_avg),
        )
        return Baseline(
            market_id=market_id,
            token_id=token_id,
            yes_price=yes_avg,
            no_price=no_avg,
            sport=sport,
            category=category,
            category_type=category_type,
            baseline_strategy="rolling_window",
            game_start_time=game_start_time,
            captured_at=latest_ts,
            late_join=False,
        )

    @staticmethod
    def capture_pre_event_snapshot(
        market_id: str,
        token_id: str,
        sport: str,
        category: str,
        category_type: str,
        game_start_time: datetime | None,
        update: PriceUpdate,
        offset_minutes: int,
    ) -> Baseline | None:
        """Capture baseline snapshot at game_start_time minus offset.

        Returns None if game_start_time is not set (EC-003, falls back
        to first_price in the caller).

        Args:
            market_id: Market identifier.
            token_id: CLOB token identifier.
            sport: Legacy sport identifier.
            category: Category identifier.
            category_type: Category type.
            game_start_time: Scheduled start time (None triggers fallback).
            update: Current price update.
            offset_minutes: Minutes before start to snapshot.

        Returns:
            Baseline if within snapshot window, or None.
        """
        if game_start_time is None:
            return None

        snapshot_time = game_start_time - timedelta(minutes=offset_minutes)
        if update.timestamp < snapshot_time:
            return None

        yes_mid = (update.yes_bid + update.yes_ask) / 2
        no_mid = (update.no_bid + update.no_ask) / 2
        logger.info(
            "pre_event_baseline_captured",
            market_id=market_id,
            offset_minutes=offset_minutes,
            yes=float(yes_mid),
        )
        return Baseline(
            market_id=market_id,
            token_id=token_id,
            yes_price=yes_mid,
            no_price=no_mid,
            sport=sport,
            category=category,
            category_type=category_type,
            baseline_strategy="pre_event_snapshot",
            game_start_time=game_start_time,
            captured_at=update.timestamp,
            late_join=False,
        )
