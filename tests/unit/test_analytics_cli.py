"""Tests for analytics CLI commands: history, stats, and date-range options on report/match-audit."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from arb_scanner.cli.app import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# history command
# ---------------------------------------------------------------------------


class TestHistoryHelp:
    """Tests for the history command help text."""

    def test_help_exits_zero(self) -> None:
        """history --help exits with code 0."""
        result = runner.invoke(app, ["history", "--help"])
        assert result.exit_code == 0

    def test_help_shows_pair_option(self) -> None:
        """history --help mentions the --pair option."""
        result = runner.invoke(app, ["history", "--help"])
        assert "--pair" in result.output

    def test_help_shows_hours_option(self) -> None:
        """history --help mentions the --hours option."""
        result = runner.invoke(app, ["history", "--help"])
        assert "--hours" in result.output

    def test_help_shows_format_option(self) -> None:
        """history --help mentions the --format option."""
        result = runner.invoke(app, ["history", "--help"])
        assert "--format" in result.output


class TestHistoryValidation:
    """Tests for history command argument validation."""

    def test_missing_pair_flag_errors(self) -> None:
        """history without --pair flag exits with non-zero code."""
        result = runner.invoke(app, ["history"])
        assert result.exit_code != 0

    def test_pair_missing_slash_errors(self) -> None:
        """--pair without '/' separator exits with non-zero code."""
        result = runner.invoke(app, ["history", "--pair", "no-slash-here"])
        assert result.exit_code != 0

    def test_pair_empty_parts_errors(self) -> None:
        """--pair with empty component (e.g. '/kalshi') exits with non-zero code."""
        result = runner.invoke(app, ["history", "--pair", "/kalshi-id"])
        assert result.exit_code != 0


class TestHistoryWithMockedDB:
    """Tests for history command with mocked database layer."""

    @patch(
        "arb_scanner.cli.analytics_commands._fetch_history",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch("arb_scanner.cli.analytics_commands.load_config")
    def test_empty_history_table(self, mock_config: Any, mock_fetch: Any) -> None:
        """history with empty results shows '(no data)'."""
        result = runner.invoke(app, ["history", "--pair", "poly-x/kalshi-y"])
        assert result.exit_code == 0
        assert "(no data)" in result.output

    @patch(
        "arb_scanner.cli.analytics_commands._fetch_history",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch("arb_scanner.cli.analytics_commands.load_config")
    def test_json_format_returns_empty_list(self, mock_config: Any, mock_fetch: Any) -> None:
        """history --format json with empty results returns '[]'."""
        result = runner.invoke(app, ["history", "--pair", "a/b", "--format", "json"])
        assert result.exit_code == 0
        assert "[]" in result.output


# ---------------------------------------------------------------------------
# stats command
# ---------------------------------------------------------------------------


class TestStatsHelp:
    """Tests for the stats command help text."""

    def test_help_exits_zero(self) -> None:
        """stats --help exits with code 0."""
        result = runner.invoke(app, ["stats", "--help"])
        assert result.exit_code == 0

    def test_help_shows_hours_option(self) -> None:
        """stats --help mentions the --hours option."""
        result = runner.invoke(app, ["stats", "--help"])
        assert "--hours" in result.output

    def test_help_shows_top_option(self) -> None:
        """stats --help mentions the --top option."""
        result = runner.invoke(app, ["stats", "--help"])
        assert "--top" in result.output


class TestStatsWithMockedDB:
    """Tests for stats command with mocked database layer."""

    @patch(
        "arb_scanner.cli.analytics_commands._fetch_stats",
        new_callable=AsyncMock,
        return_value=([], []),
    )
    @patch("arb_scanner.cli.analytics_commands.load_config")
    def test_empty_stats_table(self, mock_config: Any, mock_fetch: Any) -> None:
        """stats with empty results shows '(no data)' for both sections."""
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0
        assert "(no data)" in result.output


# ---------------------------------------------------------------------------
# report --since / --until options
# ---------------------------------------------------------------------------


class TestReportDateOptions:
    """Tests for date-range options on the report command."""

    def test_help_shows_since(self) -> None:
        """report --help includes the --since option."""
        result = runner.invoke(app, ["report", "--help"])
        assert "--since" in result.output

    def test_help_shows_until(self) -> None:
        """report --help includes the --until option."""
        result = runner.invoke(app, ["report", "--help"])
        assert "--until" in result.output


# ---------------------------------------------------------------------------
# match-audit --since option
# ---------------------------------------------------------------------------


class TestMatchAuditDateOptions:
    """Tests for date-range options on the match-audit command."""

    def test_help_shows_since(self) -> None:
        """match-audit --help includes the --since option."""
        result = runner.invoke(app, ["match-audit", "--help"])
        assert "--since" in result.output
