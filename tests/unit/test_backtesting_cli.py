"""Tests for backtesting CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

from arb_scanner.cli.backtesting_commands import (
    _parse_optional_range,
    _render_backtest_report,
    _render_import_dry_run,
    _render_import_table,
    _render_portfolio_table,
)


# ── Renderer tests ──────────────────────────────────────────────────


class TestRenderImportDryRun:
    def test_table_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        _render_import_dry_run([1, 2, 3], "table")
        out = capsys.readouterr().out
        assert "3 trade(s)" in out

    def test_json_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        _render_import_dry_run([1, 2], "json")
        data = json.loads(capsys.readouterr().out)
        assert data["parsed"] == 2


class TestRenderImportTable:
    def test_displays_counts(self, capsys: pytest.CaptureFixture[str]) -> None:
        _render_import_table({"inserted": 10, "duplicates": 2, "errors": 0})
        out = capsys.readouterr().out
        assert "10" in out
        assert "2" in out


class TestRenderPortfolioTable:
    def test_displays_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        data: dict[str, Any] = {
            "portfolio": {
                "trade_count": 5,
                "win_count": 3,
                "loss_count": 2,
                "win_rate": 0.6,
                "net_pnl": 12.50,
                "roi": 0.125,
                "total_fees": 0.50,
            },
            "category_performance": [
                {
                    "category": "nba",
                    "trade_count": 3,
                    "win_rate": 0.667,
                    "total_pnl": 10.0,
                    "profit_factor": 3.0,
                },
            ],
        }
        _render_portfolio_table(data)
        out = capsys.readouterr().out
        assert "Portfolio" in out
        assert "nba" in out
        assert "60.0%" in out

    def test_no_categories(self, capsys: pytest.CaptureFixture[str]) -> None:
        data: dict[str, Any] = {
            "portfolio": {
                "trade_count": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate": 0.0,
                "net_pnl": 0,
                "roi": 0.0,
                "total_fees": 0,
            },
            "category_performance": [],
        }
        _render_portfolio_table(data)
        out = capsys.readouterr().out
        assert "Category" not in out


class TestRenderBacktestReport:
    def test_shows_alignment(self, capsys: pytest.CaptureFixture[str]) -> None:
        data: dict[str, Any] = {
            "portfolio": {
                "trade_count": 2,
                "win_count": 1,
                "loss_count": 1,
                "win_rate": 0.5,
                "net_pnl": 5.0,
                "roi": 0.05,
                "total_fees": 0.10,
            },
            "category_performance": [],
            "signal_alignment": {
                "aligned": {"count": 5, "avg_pnl": 0.03},
                "contrary": {"count": 2, "avg_pnl": -0.01},
                "no_signal": {"count": 3, "avg_pnl": 0.0},
            },
        }
        _render_backtest_report(data)
        out = capsys.readouterr().out
        assert "Signal Alignment" in out
        assert "aligned" in out
        assert "5 trades" in out


# ── Parse helpers ───────────────────────────────────────────────────


class TestParseOptionalRange:
    def test_empty_returns_none(self) -> None:
        s, u = _parse_optional_range("", "")
        assert s is None
        assert u is None

    def test_valid_since(self) -> None:
        s, u = _parse_optional_range("2026-03-01T00:00:00Z", "")
        assert s is not None
        assert s.year == 2026
        assert u is None

    def test_invalid_raises(self) -> None:
        with pytest.raises(typer.BadParameter):
            _parse_optional_range("bad", "")


# ── Command integration tests (mocked DB) ───────────────────────────


class TestImportTradesCommand:
    def test_file_not_found_exits(self) -> None:
        from typer.testing import CliRunner

        from arb_scanner.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["import-trades", "/nonexistent/file.csv"])
        assert result.exit_code != 0

    def test_dry_run_no_db(self, tmp_path: Path) -> None:
        csv_content = (
            "marketName,action,usdcAmount,tokenAmount,tokenName,timestamp,hash\n"
            "Test Market,Buy,10.0,50.0,Yes,1709280000,0xabc123\n"
        )
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text(csv_content)

        from typer.testing import CliRunner

        from arb_scanner.cli.app import app

        runner = CliRunner()
        result = runner.invoke(app, ["import-trades", str(csv_file), "--dry-run"])
        assert result.exit_code == 0
        assert "1 trade(s)" in result.output


class TestPortfolioCommand:
    def test_portfolio_requires_config(self) -> None:
        from typer.testing import CliRunner

        from arb_scanner.cli.app import app

        runner = CliRunner()
        with patch(
            "arb_scanner.cli.backtesting_commands.load_config",
            side_effect=FileNotFoundError("no config"),
        ):
            result = runner.invoke(app, ["portfolio"])
            assert result.exit_code != 0


class TestBacktestReportCommand:
    def test_report_requires_config(self) -> None:
        from typer.testing import CliRunner

        from arb_scanner.cli.app import app

        runner = CliRunner()
        with patch(
            "arb_scanner.cli.backtesting_commands.load_config",
            side_effect=FileNotFoundError("no config"),
        ):
            result = runner.invoke(app, ["backtest-report"])
            assert result.exit_code != 0
