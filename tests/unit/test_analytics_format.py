"""Tests for analytics formatting functions: format_spread_history and format_stats_report."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from arb_scanner.models.analytics import PairSummary, ScanHealthSummary, SpreadSnapshot
from arb_scanner.notifications.reporter import format_spread_history, format_stats_report

_DT1 = datetime(2026, 2, 22, 10, 0, tzinfo=timezone.utc)
_DT2 = datetime(2026, 2, 22, 14, 0, tzinfo=timezone.utc)
_DT3 = datetime(2026, 2, 22, 18, 0, tzinfo=timezone.utc)


def _snapshot(
    *,
    detected_at: datetime = _DT1,
    spread: str = "0.035",
    annualized: str | None = "0.80",
    depth_risk: bool = False,
    max_size: str = "200",
) -> SpreadSnapshot:
    """Build a SpreadSnapshot with sensible defaults."""
    return SpreadSnapshot(
        detected_at=detected_at,
        net_spread_pct=Decimal(spread),
        annualized_return=Decimal(annualized) if annualized is not None else None,
        depth_risk=depth_risk,
        max_size=Decimal(max_size),
    )


def _pair_summary(
    *,
    poly_id: str = "poly-abc-123",
    kalshi_id: str = "KXABC-123",
    peak: str = "0.05",
    min_spread: str = "0.01",
    avg: str = "0.03",
    detections: int = 10,
    first_seen: datetime = _DT1,
    last_seen: datetime = _DT3,
) -> PairSummary:
    """Build a PairSummary with sensible defaults."""
    return PairSummary(
        poly_event_id=poly_id,
        kalshi_event_id=kalshi_id,
        peak_spread=Decimal(peak),
        min_spread=Decimal(min_spread),
        avg_spread=Decimal(avg),
        total_detections=detections,
        first_seen=first_seen,
        last_seen=last_seen,
    )


def _health(
    *,
    hour: datetime = _DT1,
    scans: int = 5,
    duration: float = 12.3,
    llm: int = 10,
    opps: int = 3,
    errors: int = 0,
) -> ScanHealthSummary:
    """Build a ScanHealthSummary with sensible defaults."""
    return ScanHealthSummary(
        hour=hour,
        scan_count=scans,
        avg_duration_s=duration,
        total_llm_calls=llm,
        total_opps=opps,
        total_errors=errors,
    )


# ---------------------------------------------------------------------------
# format_spread_history
# ---------------------------------------------------------------------------


class TestFormatSpreadHistoryHeader:
    """Tests for header and column rendering in format_spread_history."""

    def test_header_contains_pair_label(self) -> None:
        """Header line includes the pair label."""
        output = format_spread_history("abc / xyz", [_snapshot()])
        assert "abc / xyz" in output

    def test_header_contains_data_point_count(self) -> None:
        """Header shows the number of data points."""
        snaps = [_snapshot(detected_at=_DT1), _snapshot(detected_at=_DT2)]
        output = format_spread_history("p / k", snaps)
        assert "2 data points" in output

    def test_column_headers_present(self) -> None:
        """Column headers for all fields are present."""
        output = format_spread_history("p / k", [_snapshot()])
        assert "DETECTED_AT" in output
        assert "NET_SPREAD" in output
        assert "ANNUALIZED" in output
        assert "DEPTH_RISK" in output
        assert "MAX_SIZE" in output


class TestFormatSpreadHistoryValues:
    """Tests for value formatting in format_spread_history."""

    def test_percentage_formatting(self) -> None:
        """Net spread is formatted as a percentage (e.g. 3.50%)."""
        output = format_spread_history("p / k", [_snapshot(spread="0.035")])
        assert "3.50%" in output

    def test_depth_risk_yes(self) -> None:
        """depth_risk=True renders as 'Yes'."""
        output = format_spread_history("p / k", [_snapshot(depth_risk=True)])
        assert "Yes" in output

    def test_depth_risk_no(self) -> None:
        """depth_risk=False renders as 'No'."""
        output = format_spread_history("p / k", [_snapshot(depth_risk=False)])
        assert "No" in output

    def test_annualized_none_shows_na(self) -> None:
        """annualized_return=None renders as 'N/A'."""
        output = format_spread_history("p / k", [_snapshot(annualized=None)])
        assert "N/A" in output

    def test_annualized_value_formatted(self) -> None:
        """annualized_return renders as percentage (e.g. 80.0%)."""
        output = format_spread_history("p / k", [_snapshot(annualized="0.80")])
        assert "80.0%" in output


class TestFormatSpreadHistoryEdgeCases:
    """Tests for edge cases in format_spread_history."""

    def test_empty_list_returns_no_data(self) -> None:
        """Empty snapshot list returns '(no data)'."""
        output = format_spread_history("p / k", [])
        assert "(no data)" in output

    def test_single_item(self) -> None:
        """Single snapshot renders exactly one data row."""
        output = format_spread_history("p / k", [_snapshot()])
        lines = output.strip().split("\n")
        # header line, column header line, separator, one data row
        assert len(lines) == 4


# ---------------------------------------------------------------------------
# format_stats_report
# ---------------------------------------------------------------------------


class TestFormatStatsReportPairs:
    """Tests for the pairs section of format_stats_report."""

    def test_pairs_header_present(self) -> None:
        """Output includes the 'Top Pairs' section header."""
        output = format_stats_report([_pair_summary()], [_health()])
        assert "Top Pairs" in output

    def test_empty_summaries_shows_no_data(self) -> None:
        """Empty summaries list shows '(no data)' in pairs section."""
        output = format_stats_report([], [_health()])
        assert "(no data)" in output

    def test_top_n_limit(self) -> None:
        """top_n limits the number of pairs displayed."""
        summaries = [_pair_summary(poly_id=f"poly-{i}", kalshi_id=f"kalshi-{i}") for i in range(5)]
        output = format_stats_report(summaries, [_health()], top_n=2)
        # Only first 2 pairs should appear
        assert "poly-0" in output
        assert "poly-1" in output
        assert "poly-2" not in output

    def test_id_truncation_long_ids(self) -> None:
        """IDs longer than 20 chars are truncated with ellipsis."""
        long_id = "poly-very-long-event-id-that-exceeds-20-characters"
        output = format_stats_report([_pair_summary(poly_id=long_id)], [_health()])
        assert "..." in output
        assert long_id not in output


class TestFormatStatsReportHealth:
    """Tests for the health section of format_stats_report."""

    def test_health_header_present(self) -> None:
        """Output includes the 'Scanner Health' section header."""
        output = format_stats_report([_pair_summary()], [_health()])
        assert "Scanner Health" in output

    def test_empty_health_shows_no_data(self) -> None:
        """Empty health list shows '(no data)' in health section."""
        output = format_stats_report([_pair_summary()], [])
        assert "Scanner Health" in output
        assert "(no data)" in output

    def test_two_sections_present(self) -> None:
        """Both pairs and health sections appear in a complete report."""
        output = format_stats_report([_pair_summary()], [_health()])
        assert "Top Pairs" in output
        assert "Scanner Health" in output
