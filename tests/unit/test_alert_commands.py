"""Tests for alert CLI commands and format_alerts_table."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from arb_scanner.cli.app import app
from arb_scanner.models.analytics import AlertType, TrendAlert
from arb_scanner.notifications.reporter import format_alerts_table

runner = CliRunner()

_DT1 = datetime(2026, 2, 22, 10, 30, tzinfo=timezone.utc)
_DT2 = datetime(2026, 2, 22, 14, 0, tzinfo=timezone.utc)


def _alert(
    *,
    alert_type: AlertType = AlertType.convergence,
    poly_id: str | None = "poly-abc",
    kalshi_id: str | None = "KXABC",
    spread_before: Decimal | None = Decimal("0.05"),
    spread_after: Decimal | None = Decimal("0.02"),
    message: str = "Spread converged",
    dispatched_at: datetime = _DT1,
) -> TrendAlert:
    """Build a TrendAlert with sensible defaults."""
    return TrendAlert(
        alert_type=alert_type,
        poly_event_id=poly_id,
        kalshi_event_id=kalshi_id,
        spread_before=spread_before,
        spread_after=spread_after,
        message=message,
        dispatched_at=dispatched_at,
    )


# ---------------------------------------------------------------------------
# alerts CLI command
# ---------------------------------------------------------------------------


class TestAlertsHelp:
    """Tests for the alerts command help text."""

    def test_help_exits_zero(self) -> None:
        """alerts --help exits with code 0."""
        result = runner.invoke(app, ["alerts", "--help"], color=False)
        assert result.exit_code == 0

    def test_help_shows_last_option(self) -> None:
        """alerts --help mentions the --last option."""
        result = runner.invoke(app, ["alerts", "--help"], color=False)
        assert "--last" in result.output

    def test_help_shows_type_option(self) -> None:
        """alerts --help mentions the --type option."""
        result = runner.invoke(app, ["alerts", "--help"], color=False)
        assert "--type" in result.output

    def test_help_shows_format_option(self) -> None:
        """alerts --help mentions the --format option."""
        result = runner.invoke(app, ["alerts", "--help"], color=False)
        assert "--format" in result.output

    def test_alerts_in_top_level_help(self) -> None:
        """Top-level --help lists the alerts command."""
        result = runner.invoke(app, ["--help"], color=False)
        assert "alerts" in result.output


class TestAlertsWithMockedDB:
    """Tests for alerts command with mocked database layer."""

    @patch(
        "arb_scanner.cli.alert_commands._fetch_alerts",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch("arb_scanner.cli.alert_commands.load_config")
    def test_empty_alerts_table(self, mock_config: Any, mock_fetch: Any) -> None:
        """alerts with empty results shows 'No trend alerts found.'."""
        result = runner.invoke(app, ["alerts"])
        assert result.exit_code == 0
        assert "No trend alerts found." in result.output

    @patch(
        "arb_scanner.cli.alert_commands._fetch_alerts",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch("arb_scanner.cli.alert_commands.load_config")
    def test_json_format_returns_empty_list(self, mock_config: Any, mock_fetch: Any) -> None:
        """alerts --format json with empty results returns '[]'."""
        result = runner.invoke(app, ["alerts", "--format", "json"])
        assert result.exit_code == 0
        assert "[]" in result.output

    @patch("arb_scanner.cli.alert_commands._fetch_alerts", new_callable=AsyncMock)
    @patch("arb_scanner.cli.alert_commands.load_config")
    def test_json_format_with_alerts(self, mock_config: Any, mock_fetch: Any) -> None:
        """alerts --format json renders alert data as JSON."""
        mock_fetch.return_value = [_alert()]
        result = runner.invoke(app, ["alerts", "--format", "json"])
        assert result.exit_code == 0
        assert "convergence" in result.output
        assert "poly-abc" in result.output

    @patch("arb_scanner.cli.alert_commands._fetch_alerts", new_callable=AsyncMock)
    @patch("arb_scanner.cli.alert_commands.load_config")
    def test_table_format_with_alerts(self, mock_config: Any, mock_fetch: Any) -> None:
        """alerts with table format renders Markdown table."""
        mock_fetch.return_value = [_alert()]
        result = runner.invoke(app, ["alerts"])
        assert result.exit_code == 0
        assert "Trend Alerts" in result.output
        assert "convergence" in result.output

    @patch(
        "arb_scanner.cli.alert_commands.load_config",
        side_effect=RuntimeError("no config"),
    )
    def test_config_failure(self, mock_config: Any) -> None:
        """alerts exits 1 when config loading fails."""
        result = runner.invoke(app, ["alerts"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# format_alerts_table
# ---------------------------------------------------------------------------


class TestFormatAlertsTableEmpty:
    """Tests for format_alerts_table with empty input."""

    def test_empty_list(self) -> None:
        """Empty alert list returns 'No trend alerts found.'."""
        assert format_alerts_table([]) == "No trend alerts found."


class TestFormatAlertsTableHeader:
    """Tests for format_alerts_table header rendering."""

    def test_header_contains_title(self) -> None:
        """Output includes '## Trend Alerts' header."""
        output = format_alerts_table([_alert()])
        assert "## Trend Alerts" in output

    def test_header_contains_column_names(self) -> None:
        """Output includes all column headers."""
        output = format_alerts_table([_alert()])
        assert "Type" in output
        assert "Pair" in output
        assert "Before" in output
        assert "After" in output
        assert "Message" in output
        assert "Time" in output


class TestFormatAlertsTableValues:
    """Tests for value formatting in format_alerts_table."""

    def test_alert_type_rendered(self) -> None:
        """Alert type value appears in output."""
        output = format_alerts_table([_alert(alert_type=AlertType.divergence)])
        assert "divergence" in output

    def test_pair_rendered(self) -> None:
        """Poly/Kalshi pair appears in output."""
        output = format_alerts_table([_alert(poly_id="poly-x", kalshi_id="kalshi-y")])
        assert "poly-x/kalshi-y" in output

    def test_spread_before_percentage(self) -> None:
        """spread_before renders as percentage."""
        output = format_alerts_table([_alert(spread_before=Decimal("0.05"))])
        assert "5.00%" in output

    def test_spread_after_percentage(self) -> None:
        """spread_after renders as percentage."""
        output = format_alerts_table([_alert(spread_after=Decimal("0.02"))])
        assert "2.00%" in output

    def test_none_spread_shows_na(self) -> None:
        """None spread values render as 'N/A'."""
        output = format_alerts_table([_alert(spread_before=None, spread_after=None)])
        assert "N/A" in output

    def test_none_event_ids_show_na(self) -> None:
        """None event IDs render as 'N/A'."""
        output = format_alerts_table([_alert(poly_id=None, kalshi_id=None)])
        assert "N/A/N/A" in output

    def test_message_rendered(self) -> None:
        """Alert message appears in output."""
        output = format_alerts_table([_alert(message="Custom alert message")])
        assert "Custom alert message" in output

    def test_time_formatted(self) -> None:
        """Dispatched time is formatted as YYYY-MM-DD HH:MM."""
        output = format_alerts_table([_alert(dispatched_at=_DT1)])
        assert "2026-02-22 10:30" in output

    def test_multiple_alerts(self) -> None:
        """Multiple alerts each get their own row."""
        alerts = [
            _alert(alert_type=AlertType.convergence, dispatched_at=_DT1),
            _alert(alert_type=AlertType.new_high, dispatched_at=_DT2),
        ]
        output = format_alerts_table(alerts)
        assert "convergence" in output
        assert "new_high" in output
