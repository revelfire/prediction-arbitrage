"""Unit tests for the CLI app commands via typer CliRunner."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from arb_scanner.cli.app import app

runner = CliRunner()


class TestScanCommand:
    """Tests for the scan command."""

    @patch("arb_scanner.cli.app.run_scan", new_callable=AsyncMock)
    @patch("arb_scanner.cli.app.load_config_safe")
    def test_dry_run_success(self, mock_config: Any, mock_scan: Any) -> None:
        """Dry-run scan exits 0 with valid JSON output."""
        mock_scan.return_value = {
            "scan_id": "test-id",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "markets_scanned": {"polymarket": 5, "kalshi": 3},
            "candidate_pairs": 2,
            "opportunities": [],
        }
        result = runner.invoke(app, ["scan", "--dry-run"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["scan_id"] == "test-id"

    @patch("arb_scanner.cli.app.load_config_safe", side_effect=RuntimeError("no config"))
    def test_config_failure(self, mock_config: Any) -> None:
        """Scan exits 1 when config loading fails."""
        result = runner.invoke(app, ["scan", "--dry-run"])
        assert result.exit_code == 1

    @patch("arb_scanner.cli.app.run_scan", new_callable=AsyncMock)
    @patch("arb_scanner.cli.app.load_config_safe")
    def test_partial_failure(self, mock_config: Any, mock_scan: Any) -> None:
        """Scan exits 2 when one venue has zero markets."""
        mock_scan.return_value = {
            "scan_id": "test-id",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "markets_scanned": {"polymarket": 0, "kalshi": 3},
            "candidate_pairs": 0,
            "opportunities": [],
        }
        result = runner.invoke(app, ["scan", "--dry-run"])
        assert result.exit_code == 2

    @patch("arb_scanner.cli.app.run_scan", new_callable=AsyncMock)
    @patch("arb_scanner.cli.app.load_config_safe")
    def test_table_output(self, mock_config: Any, mock_scan: Any) -> None:
        """Scan with --output table renders ASCII table."""
        mock_scan.return_value = {
            "scan_id": "abcdef12-0000",
            "markets_scanned": {"polymarket": 5, "kalshi": 3},
            "candidate_pairs": 0,
            "opportunities": [],
        }
        result = runner.invoke(app, ["scan", "--dry-run", "--output", "table"])
        assert result.exit_code == 0
        assert "abcdef12" in result.output

    @patch(
        "arb_scanner.cli.app.run_scan",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    )
    @patch("arb_scanner.cli.app.load_config_safe")
    def test_scan_error(self, mock_config: Any, mock_scan: Any) -> None:
        """Scan exits 1 when run_scan raises."""
        result = runner.invoke(app, ["scan", "--dry-run"])
        assert result.exit_code == 1


class TestHelpText:
    """Tests for CLI help output."""

    def test_top_level_help(self) -> None:
        """Top-level --help shows all commands."""
        result = runner.invoke(app, ["--help"], color=False)
        assert result.exit_code == 0
        assert "scan" in result.output
        assert "watch" in result.output
        assert "report" in result.output
        assert "match-audit" in result.output
        assert "migrate" in result.output
        assert "serve" in result.output

    def test_scan_help(self) -> None:
        """Scan --help shows options."""
        result = runner.invoke(app, ["scan", "--help"], color=False)
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--min-spread" in result.output
        assert "--output" in result.output

    def test_watch_help(self) -> None:
        """Watch --help shows options."""
        result = runner.invoke(app, ["watch", "--help"], color=False)
        assert result.exit_code == 0
        assert "--interval" in result.output
        assert "--min-spread" in result.output

    def test_report_help(self) -> None:
        """Report --help shows options."""
        result = runner.invoke(app, ["report", "--help"], color=False)
        assert result.exit_code == 0
        assert "--last" in result.output
        assert "--format" in result.output

    def test_match_audit_help(self) -> None:
        """Match-audit --help shows options."""
        result = runner.invoke(app, ["match-audit", "--help"], color=False)
        assert result.exit_code == 0
        assert "--include-expired" in result.output
        assert "--min-confidence" in result.output

    def test_migrate_help(self) -> None:
        """Migrate --help shows description."""
        result = runner.invoke(app, ["migrate", "--help"], color=False)
        assert result.exit_code == 0
        assert "migration" in result.output.lower()

    def test_serve_help(self) -> None:
        """Serve --help shows options."""
        result = runner.invoke(app, ["serve", "--help"], color=False)
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--no-db" in result.output


class TestMigrateCommand:
    """Tests for the migrate command."""

    @patch("arb_scanner.cli.app.load_config", side_effect=RuntimeError("no config"))
    def test_config_failure(self, mock_config: Any) -> None:
        """Migrate exits 1 when config loading fails."""
        result = runner.invoke(app, ["migrate"])
        assert result.exit_code == 1


class TestServeCommand:
    """Tests for the serve command."""

    @patch("arb_scanner.cli.app.load_config_safe", side_effect=RuntimeError("no config"))
    def test_config_failure(self, mock_config: Any) -> None:
        """Serve exits 1 when config loading fails."""
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 1

    @patch("uvicorn.run")
    @patch("arb_scanner.api.app.create_app")
    @patch("arb_scanner.cli.app.load_config_safe")
    def test_serve_starts_uvicorn(
        self, mock_config: Any, mock_create_app: Any, mock_uvicorn: Any
    ) -> None:
        """Serve calls uvicorn.run with the FastAPI app and default host/port."""
        fake_app = MagicMock()
        mock_create_app.return_value = fake_app
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 0
        mock_create_app.assert_called_once_with(
            mock_config.return_value, no_db=False, flip_watch=False
        )
        mock_uvicorn.assert_called_once_with(fake_app, host="0.0.0.0", port=8060)

    @patch("uvicorn.run")
    @patch("arb_scanner.api.app.create_app")
    @patch("arb_scanner.cli.app.load_config_safe")
    def test_serve_custom_host_port(
        self, mock_config: Any, mock_create_app: Any, mock_uvicorn: Any
    ) -> None:
        """Serve passes custom --host and --port to uvicorn."""
        fake_app = MagicMock()
        mock_create_app.return_value = fake_app
        result = runner.invoke(app, ["serve", "--host", "127.0.0.1", "--port", "9000"])
        assert result.exit_code == 0
        mock_uvicorn.assert_called_once_with(fake_app, host="127.0.0.1", port=9000)

    @patch("uvicorn.run")
    @patch("arb_scanner.api.app.create_app")
    @patch("arb_scanner.cli.app.load_config_safe")
    def test_serve_no_db_flag(
        self, mock_config: Any, mock_create_app: Any, mock_uvicorn: Any
    ) -> None:
        """Serve passes --no-db to create_app."""
        fake_app = MagicMock()
        mock_create_app.return_value = fake_app
        result = runner.invoke(app, ["serve", "--no-db"])
        assert result.exit_code == 0
        mock_create_app.assert_called_once_with(
            mock_config.return_value, no_db=True, flip_watch=False
        )
        mock_uvicorn.assert_called_once_with(fake_app, host="0.0.0.0", port=8060)
