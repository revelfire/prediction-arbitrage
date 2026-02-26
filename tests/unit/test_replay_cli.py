"""Tests for replay CLI commands."""

from __future__ import annotations


import pytest

from arb_scanner.cli.replay_commands import (
    _parse_time_range,
)


class TestParseTimeRange:
    """Tests for _parse_time_range helper."""

    def test_empty_defaults_to_24h(self) -> None:
        since_dt, until_dt = _parse_time_range("", "")
        assert until_dt > since_dt
        delta = (until_dt - since_dt).total_seconds()
        assert abs(delta - 86400) < 5  # ~24h

    def test_iso_since(self) -> None:
        since_dt, _ = _parse_time_range("2026-02-01T00:00:00Z", "")
        assert since_dt.year == 2026
        assert since_dt.month == 2

    def test_invalid_since_raises(self) -> None:
        import typer

        with pytest.raises(typer.BadParameter):
            _parse_time_range("not-a-date", "")


class TestParseOverrides:
    """Tests for parse_overrides helper."""

    def test_basic_key_value(self) -> None:
        from arb_scanner.cli._replay_helpers import parse_overrides

        result = parse_overrides(["spike_threshold_pct=0.12", "max_hold_minutes=30"])
        assert result == {"spike_threshold_pct": 0.12, "max_hold_minutes": 30.0}

    def test_non_numeric_value(self) -> None:
        from arb_scanner.cli._replay_helpers import parse_overrides

        result = parse_overrides(["sport=nba"])
        assert result == {"sport": "nba"}

    def test_empty_list(self) -> None:
        from arb_scanner.cli._replay_helpers import parse_overrides

        assert parse_overrides([]) == {}

    def test_malformed_skipped(self) -> None:
        from arb_scanner.cli._replay_helpers import parse_overrides

        result = parse_overrides(["noequalssign"])
        assert result == {}


class TestRenderReplayTable:
    """Tests for render_replay_table."""

    def test_empty_signals(self, capsys: pytest.CaptureFixture[str]) -> None:
        from arb_scanner.cli._replay_helpers import render_replay_table

        render_replay_table([])
        out = capsys.readouterr().out
        assert "No signals" in out

    def test_renders_signal(self, capsys: pytest.CaptureFixture[str]) -> None:
        from arb_scanner.cli._replay_helpers import render_replay_table

        signals = [
            {
                "market_id": "m1",
                "side": "yes",
                "entry_price": 0.50,
                "exit_price": 0.55,
                "realized_pnl": 0.05,
                "hold_minutes": 10,
                "exit_reason": "reversion",
            }
        ]
        render_replay_table(signals)
        out = capsys.readouterr().out
        assert "m1" in out
        assert "yes" in out


class TestRenderEvaluateTable:
    """Tests for render_evaluate_table."""

    def test_renders_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        from arb_scanner.cli._replay_helpers import render_evaluate_table

        evaluation = {
            "total_signals": 10,
            "win_count": 6,
            "win_rate": 0.6,
            "avg_pnl": 0.02,
            "avg_hold_minutes": 15.0,
            "max_drawdown": 0.05,
            "profit_factor": 2.5,
        }
        render_evaluate_table(evaluation)
        out = capsys.readouterr().out
        assert "10" in out
        assert "60.0%" in out


class TestRenderSweepTable:
    """Tests for render_sweep_table."""

    def test_empty_results(self, capsys: pytest.CaptureFixture[str]) -> None:
        from arb_scanner.cli._replay_helpers import render_sweep_table

        render_sweep_table({"param_name": "x", "results": []})
        out = capsys.readouterr().out
        assert "No sweep results" in out

    def test_renders_grid(self, capsys: pytest.CaptureFixture[str]) -> None:
        from arb_scanner.cli._replay_helpers import render_sweep_table

        sweep = {
            "param_name": "spike_threshold_pct",
            "results": [
                (
                    0.08,
                    {"total_signals": 5, "win_rate": 0.6, "avg_pnl": 0.02, "profit_factor": 1.5},
                ),
                (
                    0.10,
                    {"total_signals": 3, "win_rate": 0.33, "avg_pnl": -0.01, "profit_factor": 0.8},
                ),
            ],
        }
        render_sweep_table(sweep)
        out = capsys.readouterr().out
        assert "0.0800" in out
        assert "0.1000" in out
