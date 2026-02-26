"""Tests for the flip-discover CLI command."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from arb_scanner.cli.app import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DISCOVER_RESULT: dict[str, Any] = {
    "total_scanned": 500,
    "markets_found": 12,
    "hit_rate": 0.024,
    "by_category": {"nba": 7, "nfl": 5},
    "by_category_type": {"sport": 12},
    "overrides_applied": 1,
    "exclusions_applied": 0,
    "unclassified_candidates": 3,
    "unclassified_sample": [],
    "matched": [
        {
            "event_id": "abc123def456",
            "title": "Lakers vs Celtics",
            "category": "nba",
            "category_type": "sport",
            "classification_method": "slug",
            "token_id": "tok-1",
        },
        {
            "event_id": "xyz789uvw012",
            "title": "Chiefs vs Eagles",
            "category": "nfl",
            "category_type": "sport",
            "classification_method": "tag",
            "token_id": "tok-2",
        },
    ],
}


# ---------------------------------------------------------------------------
# Table output tests
# ---------------------------------------------------------------------------


class TestFlipDiscoverTable:
    """Tests for flip-discover table output format."""

    def test_table_output_shows_summary(self) -> None:
        """Table output includes total scanned and sports found."""
        with (
            patch("arb_scanner.cli.flippening_commands.load_config") as mock_cfg,
            patch("arb_scanner.cli.flippening_commands.asyncio") as mock_asyncio,
        ):
            mock_cfg.return_value.flippening.sports = ["nba", "nfl"]
            mock_asyncio.run.return_value = _DISCOVER_RESULT
            result = runner.invoke(app, ["flip-discover"])

        assert result.exit_code == 0
        assert "500" in result.output
        assert "12" in result.output
        assert "nba" in result.output
        assert "nfl" in result.output

    def test_table_output_shows_hit_rate(self) -> None:
        """Table output includes formatted hit rate."""
        with (
            patch("arb_scanner.cli.flippening_commands.load_config") as mock_cfg,
            patch("arb_scanner.cli.flippening_commands.asyncio") as mock_asyncio,
        ):
            mock_cfg.return_value.flippening.sports = ["nba"]
            mock_asyncio.run.return_value = _DISCOVER_RESULT
            result = runner.invoke(app, ["flip-discover"])

        assert result.exit_code == 0
        assert "2.40%" in result.output

    def test_verbose_shows_matched_markets(self) -> None:
        """--verbose flag prints each matched market."""
        with (
            patch("arb_scanner.cli.flippening_commands.load_config") as mock_cfg,
            patch("arb_scanner.cli.flippening_commands.asyncio") as mock_asyncio,
        ):
            mock_cfg.return_value.flippening.sports = ["nba", "nfl"]
            mock_asyncio.run.return_value = _DISCOVER_RESULT
            result = runner.invoke(app, ["flip-discover", "--verbose"])

        assert result.exit_code == 0
        assert "abc123def456" in result.output
        assert "Lakers vs Celtics" in result.output
        assert "slug" in result.output

    def test_sports_filter_passed_to_run_discover(self) -> None:
        """--sports option passes sport filter to run_discover."""
        with (
            patch("arb_scanner.cli.flippening_commands.load_config") as mock_cfg,
            patch("arb_scanner.cli.flippening_commands.asyncio") as mock_asyncio,
        ):
            mock_cfg.return_value.flippening.sports = ["nba", "nfl"]
            mock_asyncio.run.return_value = _DISCOVER_RESULT
            result = runner.invoke(app, ["flip-discover", "--sports", "nba"])

        assert result.exit_code == 0
        # The coroutine passed to asyncio.run should be the run_discover call
        assert mock_asyncio.run.called

    def test_config_load_failure_exits_code_1(self) -> None:
        """Exits with code 1 when config cannot be loaded."""
        with patch(
            "arb_scanner.cli.flippening_commands.load_config",
            side_effect=FileNotFoundError("no config"),
        ):
            result = runner.invoke(app, ["flip-discover"])

        assert result.exit_code == 1

    def test_discover_failure_exits_code_1(self) -> None:
        """Exits with code 1 when run_discover raises."""
        with (
            patch("arb_scanner.cli.flippening_commands.load_config") as mock_cfg,
            patch("arb_scanner.cli.flippening_commands.asyncio") as mock_asyncio,
        ):
            mock_cfg.return_value.flippening.sports = ["nba"]
            mock_asyncio.run.side_effect = RuntimeError("network error")
            result = runner.invoke(app, ["flip-discover"])

        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# JSON output tests
# ---------------------------------------------------------------------------


class TestFlipDiscoverJson:
    """Tests for flip-discover json output format."""

    def test_json_format_is_valid_json(self) -> None:
        """--format json produces parseable JSON output."""
        with (
            patch("arb_scanner.cli.flippening_commands.load_config") as mock_cfg,
            patch("arb_scanner.cli.flippening_commands.asyncio") as mock_asyncio,
        ):
            mock_cfg.return_value.flippening.sports = ["nba", "nfl"]
            mock_asyncio.run.return_value = _DISCOVER_RESULT
            result = runner.invoke(app, ["flip-discover", "--format", "json"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["total_scanned"] == 500

    def test_json_format_includes_all_fields(self) -> None:
        """JSON output contains all expected keys."""
        with (
            patch("arb_scanner.cli.flippening_commands.load_config") as mock_cfg,
            patch("arb_scanner.cli.flippening_commands.asyncio") as mock_asyncio,
        ):
            mock_cfg.return_value.flippening.sports = ["nba"]
            mock_asyncio.run.return_value = _DISCOVER_RESULT
            result = runner.invoke(app, ["flip-discover", "--format", "json"])

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        expected_keys = {
            "total_scanned",
            "markets_found",
            "hit_rate",
            "by_category",
            "overrides_applied",
            "exclusions_applied",
            "unclassified_candidates",
            "matched",
        }
        assert expected_keys.issubset(parsed.keys())

    def test_json_matched_markets_structure(self) -> None:
        """JSON matched list entries have required fields."""
        with (
            patch("arb_scanner.cli.flippening_commands.load_config") as mock_cfg,
            patch("arb_scanner.cli.flippening_commands.asyncio") as mock_asyncio,
        ):
            mock_cfg.return_value.flippening.sports = ["nba", "nfl"]
            mock_asyncio.run.return_value = _DISCOVER_RESULT
            result = runner.invoke(app, ["flip-discover", "--format", "json"])

        parsed = json.loads(result.output)
        first = parsed["matched"][0]
        assert "event_id" in first
        assert "category" in first
        assert "classification_method" in first
        assert "title" in first


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestRunDiscover:
    """Unit tests for run_discover async helper."""

    def test_run_discover_returns_expected_keys(self) -> None:
        """run_discover coroutine returns dict with all required keys."""
        import asyncio

        from arb_scanner.cli._flip_discover_helpers import run_discover
        from arb_scanner.flippening.market_classifier import DiscoveryHealthSnapshot
        from arb_scanner.models.config import CategoryConfig
        from arb_scanner.models.flippening import CategoryMarket
        from arb_scanner.models.market import Market

        mock_market = MagicMock(spec=Market)
        mock_market.event_id = "abc123def456xyz"
        mock_market.title = "Lakers vs Celtics"
        mock_market.raw_data = {"groupItemTitle": "Lakers vs Celtics"}

        mock_sm = MagicMock(spec=CategoryMarket)
        mock_sm.market = mock_market
        mock_sm.category = "nba"
        mock_sm.category_type = "sport"
        mock_sm.classification_method = "slug"
        mock_sm.token_id = "tok_abc123"

        mock_health = DiscoveryHealthSnapshot(
            total_scanned=100,
            markets_found=5,
            hit_rate=0.05,
            by_category={"nba": 5},
            by_category_type={"sport": 5},
            overrides_applied=0,
            exclusions_applied=0,
            unclassified_candidates=2,
        )

        mock_config = MagicMock()
        mock_config.flippening = MagicMock()
        categories = {"nba": CategoryConfig(category_type="sport")}

        with (
            patch("arb_scanner.cli._flip_discover_helpers.PolymarketClient") as mock_client_cls,
            patch(
                "arb_scanner.cli._flip_discover_helpers.classify_markets",
                return_value=([mock_sm], mock_health),
            ),
        ):
            mock_client = AsyncMock()
            mock_client.fetch_markets = AsyncMock(return_value=[])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = asyncio.run(run_discover(mock_config, categories))

        assert result["total_scanned"] == 100
        assert result["markets_found"] == 5
        assert result["by_category"] == {"nba": 5}
        assert len(result["matched"]) == 1
        assert result["matched"][0]["event_id"] == "abc123def456"


class TestRenderDiscoverTable:
    """Unit tests for render_discover_table helper."""

    def test_renders_summary_lines(self, capsys: Any) -> None:
        """render_discover_table writes summary stats to stdout."""
        from arb_scanner.cli._flip_discover_helpers import render_discover_table

        render_discover_table(_DISCOVER_RESULT, verbose=False)
        captured = capsys.readouterr()
        assert "500" in captured.out
        assert "12" in captured.out
        assert "nba" in captured.out

    def test_verbose_includes_market_rows(self, capsys: Any) -> None:
        """render_discover_table in verbose mode includes matched market rows."""
        from arb_scanner.cli._flip_discover_helpers import render_discover_table

        render_discover_table(_DISCOVER_RESULT, verbose=True)
        captured = capsys.readouterr()
        assert "abc123def456" in captured.out
        assert "Lakers" in captured.out

    def test_non_verbose_excludes_market_rows(self, capsys: Any) -> None:
        """render_discover_table without verbose omits per-market table."""
        from arb_scanner.cli._flip_discover_helpers import render_discover_table

        render_discover_table(_DISCOVER_RESULT, verbose=False)
        captured = capsys.readouterr()
        assert "abc123def456" not in captured.out
