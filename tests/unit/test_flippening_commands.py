"""Tests for flippening CLI commands."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from arb_scanner.cli.app import app

runner = CliRunner()


class TestFlipWatch:
    """Tests for flip-watch CLI command."""

    def test_flip_watch_exits_when_config_missing(self) -> None:
        """Exits with code 1 when config.yaml not found."""
        with patch(
            "arb_scanner.cli.flippening_commands.load_config",
            side_effect=FileNotFoundError("no config"),
        ):
            result = runner.invoke(app, ["flip-watch", "--dry-run"])
            assert result.exit_code == 1

    def test_flip_watch_passes_dry_run(self) -> None:
        """Dry run flag passed to run_flip_watch."""
        with (
            patch(
                "arb_scanner.cli.flippening_commands.load_config",
            ) as mock_config,
            patch(
                "arb_scanner.cli.flippening_commands.asyncio",
            ) as mock_asyncio,
        ):
            mock_config.return_value.flippening.min_confidence = 0.6
            runner.invoke(app, ["flip-watch", "--dry-run"])
            assert mock_asyncio.run.called

    def test_flip_watch_sports_filter(self) -> None:
        """Sports filter parsed from comma-separated option."""
        with (
            patch(
                "arb_scanner.cli.flippening_commands.load_config",
            ) as mock_config,
            patch(
                "arb_scanner.cli.flippening_commands.asyncio",
            ) as mock_asyncio,
        ):
            mock_config.return_value.flippening.min_confidence = 0.6
            runner.invoke(
                app,
                ["flip-watch", "--dry-run", "--sports", "nba,nhl"],
            )
            assert mock_asyncio.run.called


class TestFlipHistory:
    """Tests for flip-history CLI command."""

    def test_flip_history_renders(self) -> None:
        """History renders table output."""
        mock_rows = [
            {
                "sport": "nba",
                "side": "yes",
                "entry_price": "0.50",
                "exit_price": "0.60",
                "realized_pnl": "0.10",
                "hold_minutes": "15",
            },
        ]
        with (
            patch(
                "arb_scanner.cli.flippening_commands.load_config",
            ),
            patch(
                "arb_scanner.cli.flippening_commands.asyncio",
            ) as mock_asyncio,
        ):
            mock_asyncio.run.return_value = mock_rows
            result = runner.invoke(app, ["flip-history"])
            assert result.exit_code == 0

    def test_flip_history_json_format(self) -> None:
        """JSON format renders JSON output."""
        with (
            patch(
                "arb_scanner.cli.flippening_commands.load_config",
            ),
            patch(
                "arb_scanner.cli.flippening_commands.asyncio",
            ) as mock_asyncio,
        ):
            mock_asyncio.run.return_value = []
            result = runner.invoke(
                app,
                ["flip-history", "--format", "json"],
            )
            assert result.exit_code == 0


class TestFlipStats:
    """Tests for flip-stats CLI command."""

    def test_flip_stats_renders(self) -> None:
        """Stats renders summary output."""
        mock_data = [
            {
                "sport": "nba",
                "total": 10,
                "win_rate": 0.70,
                "avg_pnl": 0.05,
                "avg_hold": 12.5,
            },
        ]
        with (
            patch(
                "arb_scanner.cli.flippening_commands.load_config",
            ),
            patch(
                "arb_scanner.cli.flippening_commands.asyncio",
            ) as mock_asyncio,
        ):
            mock_asyncio.run.return_value = mock_data
            result = runner.invoke(app, ["flip-stats"])
            assert result.exit_code == 0
