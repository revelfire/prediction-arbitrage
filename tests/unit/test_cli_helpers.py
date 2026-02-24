"""Unit tests for CLI helper functions."""

from __future__ import annotations

from typing import Any

from arb_scanner.cli._helpers import (
    _fmt_dec,
    _fmt_pct,
    determine_exit_code,
    format_report_markdown,
    render_output,
    render_table,
)


class TestDetermineExitCode:
    """Tests for determine_exit_code."""

    def test_both_venues_present(self) -> None:
        """Exit 0 when both venues have markets."""
        result: dict[str, Any] = {"markets_scanned": {"polymarket": 10, "kalshi": 5}}
        assert determine_exit_code(result) == 0

    def test_polymarket_missing(self) -> None:
        """Exit 2 when polymarket has zero markets."""
        result: dict[str, Any] = {"markets_scanned": {"polymarket": 0, "kalshi": 5}}
        assert determine_exit_code(result) == 2

    def test_kalshi_missing(self) -> None:
        """Exit 2 when kalshi has zero markets."""
        result: dict[str, Any] = {"markets_scanned": {"polymarket": 10, "kalshi": 0}}
        assert determine_exit_code(result) == 2

    def test_empty_scanned(self) -> None:
        """Exit 2 when markets_scanned is empty."""
        result: dict[str, Any] = {"markets_scanned": {}}
        assert determine_exit_code(result) == 2

    def test_missing_key(self) -> None:
        """Exit 2 when markets_scanned key is missing."""
        result: dict[str, Any] = {}
        assert determine_exit_code(result) == 2


class TestRenderOutput:
    """Tests for render_output."""

    def test_json_output(self, capsys: Any) -> None:
        """JSON format writes valid JSON to stdout."""
        result: dict[str, Any] = {
            "scan_id": "test-123",
            "markets_scanned": {"polymarket": 1, "kalshi": 1},
            "candidate_pairs": 0,
            "opportunities": [],
        }
        render_output(result, "json")
        captured = capsys.readouterr()
        assert '"scan_id": "test-123"' in captured.out

    def test_json_excludes_private_keys(self, capsys: Any) -> None:
        """JSON format excludes keys starting with underscore."""
        result: dict[str, Any] = {
            "scan_id": "test-123",
            "_raw_opps": ["should-not-appear"],
        }
        render_output(result, "json")
        captured = capsys.readouterr()
        assert "_raw_opps" not in captured.out

    def test_table_output(self, capsys: Any) -> None:
        """Table format writes header to stdout."""
        result: dict[str, Any] = {
            "scan_id": "abcdef12-3456",
            "markets_scanned": {"polymarket": 5, "kalshi": 3},
            "candidate_pairs": 2,
            "opportunities": [],
        }
        render_output(result, "table")
        captured = capsys.readouterr()
        assert "abcdef12" in captured.out
        assert "No opportunities found" in captured.out


class TestRenderTable:
    """Tests for render_table."""

    def test_with_opportunities(self, capsys: Any) -> None:
        """Table renders opportunity rows."""
        result: dict[str, Any] = {
            "scan_id": "test-id-1234",
            "markets_scanned": {"polymarket": 10, "kalshi": 8},
            "candidate_pairs": 3,
            "opportunities": [
                {
                    "id": "opp-12345678",
                    "buy": {"venue": "polymarket", "price": 0.45},
                    "sell": {"venue": "kalshi", "price": 0.60},
                    "net_spread_pct": 0.03,
                    "max_size_usd": 500,
                },
            ],
        }
        render_table(result)
        captured = capsys.readouterr()
        assert "polymarket" in captured.out
        assert "kalshi" in captured.out


class TestFormatReportMarkdown:
    """Tests for format_report_markdown."""

    def test_empty_rows(self) -> None:
        """Returns message when no rows."""
        result = format_report_markdown([])
        assert "No recent opportunities" in result

    def test_with_rows(self) -> None:
        """Returns markdown table with data."""
        rows: list[dict[str, Any]] = [
            {
                "id": "opp-001",
                "buy_venue": "polymarket",
                "sell_venue": "kalshi",
                "net_spread_pct": 0.035,
                "max_size": 200,
                "detected_at": "2026-01-01T00:00:00",
            },
        ]
        result = format_report_markdown(rows)
        assert "# Recent Opportunities" in result
        assert "polymarket" in result


class TestFmtHelpers:
    """Tests for _fmt_pct and _fmt_dec."""

    def test_fmt_pct_none(self) -> None:
        """Returns N/A for None."""
        assert _fmt_pct(None) == "N/A"

    def test_fmt_pct_value(self) -> None:
        """Returns formatted percentage."""
        assert "3" in _fmt_pct(0.03)

    def test_fmt_dec_none(self) -> None:
        """Returns N/A for None."""
        assert _fmt_dec(None) == "N/A"

    def test_fmt_dec_value(self) -> None:
        """Returns formatted decimal."""
        assert _fmt_dec(123.456) == "123"
