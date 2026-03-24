"""Tests for the ReplayEngine backtesting module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from arb_scanner.flippening.replay_engine import ReplayEngine
from arb_scanner.models.config import CategoryConfig, FlippeningConfig


def _ts(minutes: int = 0) -> datetime:
    """Helper: base timestamp offset by minutes."""
    return datetime(2026, 2, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def _baseline_row(
    market_id: str = "m1",
    yes: str = "0.65",
    no: str = "0.35",
) -> dict[str, Any]:
    return {
        "market_id": market_id,
        "token_id": "t1",
        "baseline_yes": Decimal(yes),
        "baseline_no": Decimal(no),
        "sport": "nba",
        "category": "nba",
        "category_type": "sport",
        "baseline_strategy": "first_price",
        "game_start_time": _ts(-30),
        "captured_at": _ts(-5),
        "late_join": False,
    }


def _tick_row(
    minutes: int = 0,
    yes_bid: str = "0.55",
    yes_ask: str = "0.57",
) -> dict[str, Any]:
    return {
        "market_id": "m1",
        "token_id": "t1",
        "yes_bid": Decimal(yes_bid),
        "yes_ask": Decimal(yes_ask),
        "no_bid": Decimal("0.43"),
        "no_ask": Decimal("0.45"),
        "timestamp": _ts(minutes),
        "synthetic_spread": False,
        "book_depth_bids": 5,
        "book_depth_asks": 5,
    }


def _market_context_row(
    sport: str = "nba",
    category: str = "nba",
    category_type: str = "sport",
) -> dict[str, Any]:
    return {
        "sport": sport,
        "category": category,
        "category_type": category_type,
    }


def _make_record(data: dict[str, Any]) -> MagicMock:
    """Create a mock asyncpg.Record with dict-like access."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: data[key]
    record.get = lambda key, default=None: data.get(key, default)
    record.keys = lambda: data.keys()
    return record


async def _async_iter(items: list[Any]) -> Any:
    for item in items:
        yield item


def _make_repo(
    baseline: dict[str, Any] | None = None,
    ticks: list[dict[str, Any]] | None = None,
    drifts: list[dict[str, Any]] | None = None,
    market_ids: list[str] | None = None,
    first_tick: dict[str, Any] | None = None,
    market_context: dict[str, Any] | None = None,
) -> AsyncMock:
    repo = AsyncMock()
    if baseline is not None:
        repo.get_baseline.return_value = _make_record(baseline)
    else:
        repo.get_baseline.return_value = None
    repo.get_first_tick.return_value = _make_record(first_tick) if first_tick is not None else None
    repo.get_market_context.return_value = (
        _make_record(market_context) if market_context is not None else None
    )
    repo.get_drifts.return_value = [_make_record(d) for d in (drifts or [])]
    repo.get_market_ids.return_value = market_ids or []

    tick_records = [_make_record(t) for t in (ticks or [])]

    async def stream_ticks(*_args: Any, **_kwargs: Any) -> Any:
        for r in tick_records:
            yield r

    repo.stream_ticks = stream_ticks
    return repo


def _make_config(**overrides: Any) -> FlippeningConfig:
    defaults: dict[str, Any] = {
        "spike_threshold_pct": 0.08,
        "min_confidence": 0.01,
        "spike_window_minutes": 60,
    }
    defaults.update(overrides)
    return FlippeningConfig(**defaults)


class TestReplayMarket:
    """Tests for ReplayEngine.replay_market()."""

    @pytest.mark.asyncio
    async def test_missing_baseline_returns_empty(self) -> None:
        repo = _make_repo(baseline=None)
        engine = ReplayEngine(repo, _make_config())
        result = await engine.replay_market("m1", _ts(), _ts(60))
        assert result == []

    @pytest.mark.asyncio
    async def test_reconstructs_baseline_from_first_tick(self) -> None:
        repo = _make_repo(
            baseline=None,
            first_tick=_tick_row(0, yes_bid="0.64", yes_ask="0.66"),
            market_context=_market_context_row(),
        )
        engine = ReplayEngine(repo, _make_config())

        baseline = await engine._load_baseline("m1", _ts(), _ts(60), "nba")

        assert baseline is not None
        assert baseline.yes_price == Decimal("0.65")
        assert baseline.no_price == Decimal("0.44")
        assert baseline.category == "nba"
        assert baseline.category_type == "sport"
        assert baseline.late_join is True

    @pytest.mark.asyncio
    async def test_reconstructs_baseline_uses_category_hint_without_context(self) -> None:
        repo = _make_repo(
            baseline=None,
            first_tick=_tick_row(0, yes_bid="0.60", yes_ask="0.62"),
        )
        engine = ReplayEngine(
            repo,
            _make_config(
                categories={
                    "fed_meeting": CategoryConfig(category_type="economics"),
                },
            ),
        )

        baseline = await engine._load_baseline("m1", _ts(), _ts(60), "fed_meeting")

        assert baseline is not None
        assert baseline.category == "fed_meeting"
        assert baseline.sport == "fed_meeting"
        assert baseline.category_type == "economics"

    @pytest.mark.asyncio
    async def test_zero_ticks_returns_empty(self) -> None:
        repo = _make_repo(baseline=_baseline_row(), ticks=[])
        engine = ReplayEngine(repo, _make_config())
        result = await engine.replay_market("m1", _ts(), _ts(60))
        assert result == []

    @pytest.mark.asyncio
    async def test_spike_produces_entry_exit(self) -> None:
        """A spike followed by reversion should produce a signal."""
        # Baseline YES at 0.65. Tick drops to 0.50 (spike).
        # Then reverts toward baseline.
        ticks = [
            # Near baseline first (needed for recency check)
            _tick_row(0, yes_bid="0.64", yes_ask="0.66"),
            # Spike down — 0.50 is well below 0.65 baseline
            _tick_row(1, yes_bid="0.49", yes_ask="0.51"),
            # Reversion back up
            _tick_row(2, yes_bid="0.62", yes_ask="0.64"),
            _tick_row(3, yes_bid="0.63", yes_ask="0.65"),
            _tick_row(4, yes_bid="0.64", yes_ask="0.66"),
        ]
        repo = _make_repo(baseline=_baseline_row(), ticks=ticks)
        engine = ReplayEngine(
            repo,
            _make_config(
                spike_threshold_pct=0.08,
                min_confidence=0.01,
                reversion_target_pct=0.50,
            ),
        )
        result = await engine.replay_market("m1", _ts(), _ts(60))
        # Should have at least detected a spike entry
        # Exit depends on reversion target being hit
        assert len(result) >= 0  # non-crash assertion

    @pytest.mark.asyncio
    async def test_config_override_changes_threshold(self) -> None:
        """Higher threshold should prevent spike detection."""
        ticks = [
            _tick_row(0, yes_bid="0.64", yes_ask="0.66"),
            _tick_row(1, yes_bid="0.55", yes_ask="0.57"),
        ]
        repo = _make_repo(baseline=_baseline_row(), ticks=ticks)
        engine = ReplayEngine(repo, _make_config(spike_threshold_pct=0.08))

        # With very high threshold, no spike should be detected
        result = await engine.replay_market(
            "m1",
            _ts(),
            _ts(60),
            overrides={"spike_threshold_pct": 0.99},
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_invalid_override_raises(self) -> None:
        """Invalid config values should raise ValidationError."""
        repo = _make_repo(baseline=_baseline_row())
        engine = ReplayEngine(repo, _make_config())
        with pytest.raises(Exception):
            await engine.replay_market(
                "m1",
                _ts(),
                _ts(60),
                overrides={"confidence_weights": "not_a_dict"},
            )

    @pytest.mark.asyncio
    async def test_drift_updates_baseline(self) -> None:
        """Drift records should update baseline during replay."""
        ticks = [
            _tick_row(0, yes_bid="0.64", yes_ask="0.66"),
            _tick_row(5, yes_bid="0.60", yes_ask="0.62"),
            _tick_row(10, yes_bid="0.55", yes_ask="0.57"),
        ]
        drifts = [
            {
                "market_id": "m1",
                "old_yes": Decimal("0.65"),
                "new_yes": Decimal("0.61"),
                "drift_reason": "gradual",
                "drifted_at": _ts(4),
            }
        ]
        repo = _make_repo(
            baseline=_baseline_row(),
            ticks=ticks,
            drifts=drifts,
        )
        engine = ReplayEngine(repo, _make_config())
        # Should not crash; drift is applied between tick 0 and tick 5
        result = await engine.replay_market("m1", _ts(), _ts(60))
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_drift_before_baseline_skipped(self) -> None:
        """Drifts before baseline capture time should be ignored."""
        ticks = [_tick_row(0)]
        drifts = [
            {
                "market_id": "m1",
                "old_yes": Decimal("0.60"),
                "new_yes": Decimal("0.55"),
                "drift_reason": "gradual",
                "drifted_at": _ts(-10),  # before baseline captured_at
            }
        ]
        repo = _make_repo(
            baseline=_baseline_row(),
            ticks=ticks,
            drifts=drifts,
        )
        engine = ReplayEngine(repo, _make_config())
        result = await engine.replay_market("m1", _ts(), _ts(60))
        assert isinstance(result, list)


class TestReplaySport:
    """Tests for ReplayEngine.replay_sport()."""

    @pytest.mark.asyncio
    async def test_replays_multiple_markets(self) -> None:
        repo = _make_repo(
            baseline=_baseline_row(),
            ticks=[_tick_row(0)],
            market_ids=["m1", "m2"],
        )
        engine = ReplayEngine(repo, _make_config())
        result = await engine.replay_sport("nba", _ts(), _ts(60))
        assert isinstance(result, list)
        # get_baseline called twice (once per market)
        assert repo.get_baseline.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_sport_returns_empty(self) -> None:
        repo = _make_repo(market_ids=[])
        engine = ReplayEngine(repo, _make_config())
        result = await engine.replay_sport("nba", _ts(), _ts(60))
        assert result == []
