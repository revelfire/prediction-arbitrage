"""Tests for replay evaluation and parameter sweep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from arb_scanner.flippening.replay_evaluator import (
    _generate_values,
    _max_drawdown,
    _profit_factor,
    evaluate_replay,
    sweep_parameter,
)
from arb_scanner.models.flippening import ExitReason
from arb_scanner.models.replay import ReplaySignal


def _ts(minutes: int = 0) -> datetime:
    return datetime(2026, 2, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def _signal(
    pnl: str = "0.05",
    hold: str = "10.0",
    reason: ExitReason = ExitReason.REVERSION,
) -> ReplaySignal:
    return ReplaySignal(
        market_id="m1",
        entry_price=Decimal("0.50"),
        exit_price=Decimal("0.50") + Decimal(pnl),
        exit_reason=reason,
        realized_pnl=Decimal(pnl),
        hold_minutes=Decimal(hold),
        confidence=Decimal("0.80"),
        side="yes",
        entry_at=_ts(0),
        exit_at=_ts(int(float(hold))),
    )


class TestEvaluateReplay:
    """Tests for evaluate_replay()."""

    def test_empty_signals(self) -> None:
        result = evaluate_replay([])
        assert result.total_signals == 0
        assert result.win_rate == 0.0
        assert result.avg_pnl == 0.0
        assert result.profit_factor == 0.0

    def test_all_wins(self) -> None:
        signals = [
            _signal(pnl="0.05"),
            _signal(pnl="0.03"),
            _signal(pnl="0.07"),
        ]
        result = evaluate_replay(signals)
        assert result.total_signals == 3
        assert result.win_count == 3
        assert result.win_rate == 1.0
        assert result.avg_pnl > 0
        assert result.profit_factor == 999.99  # no losses → capped

    def test_all_losses(self) -> None:
        signals = [
            _signal(pnl="-0.10", reason=ExitReason.STOP_LOSS),
            _signal(pnl="-0.05", reason=ExitReason.TIMEOUT),
        ]
        result = evaluate_replay(signals)
        assert result.total_signals == 2
        assert result.win_count == 0
        assert result.win_rate == 0.0
        assert result.profit_factor == 0.0

    def test_mixed_results(self) -> None:
        signals = [
            _signal(pnl="0.10"),
            _signal(pnl="-0.05", reason=ExitReason.STOP_LOSS),
            _signal(pnl="0.06"),
            _signal(pnl="-0.03", reason=ExitReason.TIMEOUT),
        ]
        result = evaluate_replay(signals)
        assert result.total_signals == 4
        assert result.win_count == 2
        assert result.win_rate == 0.5
        expected_avg = (0.10 - 0.05 + 0.06 - 0.03) / 4
        assert abs(result.avg_pnl - expected_avg) < 0.001
        # profit_factor = (0.10 + 0.06) / (0.05 + 0.03) = 2.0
        assert abs(result.profit_factor - 2.0) < 0.01

    def test_config_overrides_passed_through(self) -> None:
        result = evaluate_replay([], {"spike_threshold_pct": 0.12})
        assert result.config_overrides == {"spike_threshold_pct": 0.12}


class TestMaxDrawdown:
    """Tests for _max_drawdown()."""

    def test_empty(self) -> None:
        assert _max_drawdown([]) == 0.0

    def test_no_drawdown(self) -> None:
        assert _max_drawdown([1.0, 2.0, 3.0]) == 0.0

    def test_simple_drawdown(self) -> None:
        # Cumulative: 5, 2, 4, -4
        pnls = [5.0, -3.0, 2.0, -8.0]
        assert _max_drawdown(pnls) == 9.0  # peak=5, trough=-4, dd=9

    def test_recovery_after_drawdown(self) -> None:
        pnls = [5.0, -3.0, 10.0]
        # Cumulative: 5, 2, 12. Peak goes 5→12, max dd = 5-2 = 3
        assert _max_drawdown(pnls) == 3.0


class TestProfitFactor:
    """Tests for _profit_factor()."""

    def test_no_trades(self) -> None:
        assert _profit_factor([]) == 0.0

    def test_all_wins(self) -> None:
        assert _profit_factor([1.0, 2.0]) == 999.99

    def test_all_losses(self) -> None:
        assert _profit_factor([-1.0, -2.0]) == 0.0

    def test_mixed(self) -> None:
        # wins = 10 + 6 = 16, losses = 5 + 3 = 8
        result = _profit_factor([10.0, -5.0, 6.0, -3.0])
        assert abs(result - 2.0) < 0.01


class TestGenerateValues:
    """Tests for _generate_values()."""

    def test_basic_range(self) -> None:
        values = _generate_values(0.08, 0.12, 0.02)
        assert values == [0.08, 0.10, 0.12]

    def test_single_value(self) -> None:
        values = _generate_values(0.10, 0.10, 0.01)
        assert values == [0.10]

    def test_float_precision(self) -> None:
        # 0.1 + 0.1 + 0.1 should not miss 0.3 due to float issues
        values = _generate_values(0.1, 0.3, 0.1)
        assert len(values) == 3


class TestSweepParameter:
    """Tests for sweep_parameter()."""

    @pytest.mark.asyncio
    async def test_sweep_produces_result_per_value(self) -> None:
        engine = AsyncMock()
        engine.replay_sport.return_value = []

        result = await sweep_parameter(
            engine,
            "nba",
            _ts(),
            _ts(60),
            "spike_threshold_pct",
            0.08,
            0.12,
            0.02,
        )

        assert result.param_name == "spike_threshold_pct"
        assert len(result.results) == 3  # 0.08, 0.10, 0.12
        assert engine.replay_sport.call_count == 3

    @pytest.mark.asyncio
    async def test_sweep_empty_sport(self) -> None:
        engine = AsyncMock()
        engine.replay_sport.return_value = []

        result = await sweep_parameter(
            engine,
            "nba",
            _ts(),
            _ts(60),
            "spike_threshold_pct",
            0.10,
            0.10,
            0.01,
        )

        assert len(result.results) == 1
        val, evaluation = result.results[0]
        assert val == 0.10
        assert evaluation.total_signals == 0
