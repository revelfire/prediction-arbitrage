"""Tests for BaselineCapture: first_price, rolling_window, pre_event_snapshot."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arb_scanner.flippening.baseline_strategy import BaselineCapture
from arb_scanner.models.flippening import PriceUpdate


def _update(
    ts_offset_min: float = 0.0,
    yes_bid: str = "0.60",
    yes_ask: str = "0.62",
    no_bid: str = "0.38",
    no_ask: str = "0.40",
) -> PriceUpdate:
    base = datetime(2026, 3, 1, 20, 0, 0, tzinfo=UTC)
    return PriceUpdate(
        market_id="m1",
        token_id="tok1",
        yes_bid=Decimal(yes_bid),
        yes_ask=Decimal(yes_ask),
        no_bid=Decimal(no_bid),
        no_ask=Decimal(no_ask),
        timestamp=base + timedelta(minutes=ts_offset_min),
    )


class TestFirstPrice:
    """capture_first_price returns baseline from first price update."""

    def test_captures_midpoint(self) -> None:
        update = _update(yes_bid="0.60", yes_ask="0.62", no_bid="0.38", no_ask="0.40")
        baseline = BaselineCapture.capture_first_price(
            market_id="m1",
            token_id="tok1",
            sport="nba",
            category="nba",
            category_type="sport",
            game_start_time=None,
            update=update,
            late_join=False,
        )
        assert baseline.yes_price == Decimal("0.61")
        assert baseline.no_price == Decimal("0.39")
        assert baseline.baseline_strategy == "first_price"
        assert baseline.late_join is False

    def test_late_join_flag(self) -> None:
        update = _update()
        baseline = BaselineCapture.capture_first_price(
            market_id="m1",
            token_id="tok1",
            sport="nba",
            category="nba",
            category_type="sport",
            game_start_time=None,
            update=update,
            late_join=True,
        )
        assert baseline.late_join is True


class TestRollingWindow:
    """capture_rolling_window averages prices over a time window."""

    def test_returns_none_with_insufficient_data(self) -> None:
        """EC-006: < 3 data points returns None."""
        history: deque[PriceUpdate] = deque([_update(0), _update(1)])
        result = BaselineCapture.capture_rolling_window(
            market_id="m1",
            token_id="tok1",
            sport="nba",
            category="nba",
            category_type="sport",
            game_start_time=None,
            price_history=history,
            window_minutes=30,
        )
        assert result is None

    def test_averages_prices_in_window(self) -> None:
        history: deque[PriceUpdate] = deque(
            [
                _update(0, yes_bid="0.50", yes_ask="0.52"),
                _update(5, yes_bid="0.60", yes_ask="0.62"),
                _update(10, yes_bid="0.70", yes_ask="0.72"),
            ]
        )
        result = BaselineCapture.capture_rolling_window(
            market_id="m1",
            token_id="tok1",
            sport="nba",
            category="nba",
            category_type="sport",
            game_start_time=None,
            price_history=history,
            window_minutes=30,
        )
        assert result is not None
        expected_yes = (Decimal("0.51") + Decimal("0.61") + Decimal("0.71")) / 3
        assert result.yes_price == expected_yes
        assert result.baseline_strategy == "rolling_window"

    def test_excludes_old_prices_outside_window(self) -> None:
        history: deque[PriceUpdate] = deque(
            [
                _update(-40, yes_bid="0.30", yes_ask="0.32"),  # outside 30-min window
                _update(-5, yes_bid="0.60", yes_ask="0.62"),
                _update(-3, yes_bid="0.64", yes_ask="0.66"),
                _update(0, yes_bid="0.68", yes_ask="0.70"),
            ]
        )
        result = BaselineCapture.capture_rolling_window(
            market_id="m1",
            token_id="tok1",
            sport="nba",
            category="nba",
            category_type="sport",
            game_start_time=None,
            price_history=history,
            window_minutes=30,
        )
        assert result is not None
        # Only the last 3 updates are within 30 min of the latest
        expected_yes = (Decimal("0.61") + Decimal("0.65") + Decimal("0.69")) / 3
        assert result.yes_price == expected_yes

    def test_empty_history_returns_none(self) -> None:
        history: deque[PriceUpdate] = deque()
        result = BaselineCapture.capture_rolling_window(
            market_id="m1",
            token_id="tok1",
            sport="nba",
            category="nba",
            category_type="sport",
            game_start_time=None,
            price_history=history,
            window_minutes=30,
        )
        assert result is None


class TestPreEventSnapshot:
    """capture_pre_event_snapshot captures at offset before event start."""

    def test_returns_none_without_game_start_time(self) -> None:
        """EC-003: No game_start_time means fallback (returns None)."""
        update = _update()
        result = BaselineCapture.capture_pre_event_snapshot(
            market_id="m1",
            token_id="tok1",
            sport="oscars",
            category="oscars",
            category_type="entertainment",
            game_start_time=None,
            update=update,
            offset_minutes=15,
        )
        assert result is None

    def test_captures_when_within_offset_window(self) -> None:
        game_start = datetime(2026, 3, 1, 20, 30, 0, tzinfo=UTC)
        update = _update(ts_offset_min=10)  # 20:10, which is 20 min before 20:30
        result = BaselineCapture.capture_pre_event_snapshot(
            market_id="m1",
            token_id="tok1",
            sport="oscars",
            category="oscars",
            category_type="entertainment",
            game_start_time=game_start,
            update=update,
            offset_minutes=30,
        )
        assert result is not None
        assert result.baseline_strategy == "pre_event_snapshot"
        assert result.yes_price == Decimal("0.61")

    def test_returns_none_when_too_early(self) -> None:
        game_start = datetime(2026, 3, 1, 22, 0, 0, tzinfo=UTC)
        update = _update(ts_offset_min=0)  # 20:00, way too early for 22:00 game
        result = BaselineCapture.capture_pre_event_snapshot(
            market_id="m1",
            token_id="tok1",
            sport="oscars",
            category="oscars",
            category_type="entertainment",
            game_start_time=game_start,
            update=update,
            offset_minutes=15,
        )
        assert result is None

    def test_category_fields_set(self) -> None:
        game_start = datetime(2026, 3, 1, 20, 10, 0, tzinfo=UTC)
        update = _update()
        result = BaselineCapture.capture_pre_event_snapshot(
            market_id="m1",
            token_id="tok1",
            sport="fed_rate",
            category="fed_rate",
            category_type="economics",
            game_start_time=game_start,
            update=update,
            offset_minutes=15,
        )
        assert result is not None
        assert result.category == "fed_rate"
        assert result.category_type == "economics"
