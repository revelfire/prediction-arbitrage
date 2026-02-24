"""T055 - Unit tests for match-audit CLI output formatting.

Tests the format_matches_table function from the reporter module,
including filtering by confidence level and include-expired flag behavior.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from arb_scanner.notifications.reporter import format_matches_table

_NOW = datetime.now(tz=timezone.utc)
_FUTURE = _NOW + timedelta(hours=48)
_PAST = _NOW - timedelta(hours=48)


def _make_match_dict(
    *,
    poly_id: str = "poly-001",
    kalshi_id: str = "kalshi-001",
    confidence: float = 0.92,
    equivalent: bool = True,
    safe: bool = True,
    reasoning: str = "Same underlying event.",
    ttl_expires: datetime | None = None,
) -> dict[str, Any]:
    """Build a match result dict as returned by the repository."""
    return {
        "poly_event_id": poly_id,
        "kalshi_event_id": kalshi_id,
        "match_confidence": confidence,
        "resolution_equivalent": equivalent,
        "resolution_risks": ["minor wording"],
        "safe_to_arb": safe,
        "reasoning": reasoning,
        "matched_at": _NOW,
        "ttl_expires": ttl_expires or _FUTURE,
    }


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------


class TestEmptyResults:
    """Tests for when no match results exist."""

    def test_empty_matches_message(self) -> None:
        """Verify empty matches produce the expected message."""
        output = format_matches_table([])
        assert output == "No match results found.\n"


# ---------------------------------------------------------------------------
# Table formatting
# ---------------------------------------------------------------------------


class TestTableFormatting:
    """Tests for the ASCII table rendering of match results."""

    def test_header_present(self) -> None:
        """Verify the table header row is in the output."""
        matches = [_make_match_dict()]
        output = format_matches_table(matches)
        assert "POLY_ID" in output
        assert "KALSHI_ID" in output
        assert "CONF" in output

    def test_match_data_in_output(self) -> None:
        """Verify match data appears in the formatted output."""
        matches = [_make_match_dict(poly_id="poly-abc", kalshi_id="kalshi-xyz")]
        output = format_matches_table(matches)
        assert "poly-abc" in output
        assert "kalshi-xyz" in output

    def test_confidence_formatted(self) -> None:
        """Verify confidence is rendered as a float."""
        matches = [_make_match_dict(confidence=0.85)]
        output = format_matches_table(matches)
        assert "0.85" in output

    def test_equivalent_yes(self) -> None:
        """Verify resolution_equivalent=True renders as Y."""
        matches = [_make_match_dict(equivalent=True)]
        output = format_matches_table(matches)
        lines = output.strip().split("\n")
        data_line = lines[2]
        assert " Y " in data_line

    def test_equivalent_no(self) -> None:
        """Verify resolution_equivalent=False renders as N."""
        matches = [_make_match_dict(equivalent=False, safe=False)]
        output = format_matches_table(matches)
        lines = output.strip().split("\n")
        data_line = lines[2]
        assert " N " in data_line

    def test_reasoning_truncated(self) -> None:
        """Verify long reasoning strings are truncated."""
        long_reason = "A" * 50
        matches = [_make_match_dict(reasoning=long_reason)]
        output = format_matches_table(matches)
        assert "..." in output
        assert long_reason not in output

    def test_multiple_rows(self) -> None:
        """Verify multiple matches produce multiple data rows."""
        matches = [
            _make_match_dict(poly_id="p1", kalshi_id="k1"),
            _make_match_dict(poly_id="p2", kalshi_id="k2"),
        ]
        output = format_matches_table(matches)
        assert "p1" in output
        assert "p2" in output


# ---------------------------------------------------------------------------
# Expired flag behavior
# ---------------------------------------------------------------------------


class TestExpiredFlag:
    """Tests for the expired column display."""

    def test_non_expired_shows_no(self) -> None:
        """Verify non-expired matches show 'no' in the expired column."""
        matches = [_make_match_dict(ttl_expires=_FUTURE)]
        output = format_matches_table(matches)
        lines = output.strip().split("\n")
        data_line = lines[2]
        assert "no" in data_line.lower()

    def test_expired_shows_yes(self) -> None:
        """Verify expired matches show 'YES' in the expired column."""
        matches = [_make_match_dict(ttl_expires=_PAST)]
        output = format_matches_table(matches)
        lines = output.strip().split("\n")
        data_line = lines[2]
        assert "YES" in data_line


# ---------------------------------------------------------------------------
# Confidence filtering (at the reporter level, data is pre-filtered)
# ---------------------------------------------------------------------------


class TestConfidenceFiltering:
    """Tests demonstrating that confidence values render correctly."""

    def test_low_confidence_visible(self) -> None:
        """Verify a low-confidence match renders its confidence value."""
        matches = [_make_match_dict(confidence=0.30)]
        output = format_matches_table(matches)
        assert "0.30" in output

    def test_high_confidence_visible(self) -> None:
        """Verify a high-confidence match renders its confidence value."""
        matches = [_make_match_dict(confidence=0.99)]
        output = format_matches_table(matches)
        assert "0.99" in output


# ---------------------------------------------------------------------------
# Ticket table formatting
# ---------------------------------------------------------------------------


class TestTicketsTable:
    """Tests for the format_tickets_table function."""

    def test_empty_tickets_message(self) -> None:
        """Verify empty ticket list produces expected message."""
        from arb_scanner.notifications.reporter import format_tickets_table

        output = format_tickets_table([])
        assert output == "No execution tickets found.\n"

    def test_ticket_data_in_output(self) -> None:
        """Verify ticket data appears in formatted output."""
        from decimal import Decimal

        from arb_scanner.notifications.reporter import format_tickets_table

        tickets = [
            {
                "arb_id": "abc-123-def-456",
                "status": "pending",
                "expected_cost": Decimal("87.00"),
                "expected_profit": Decimal("5.00"),
                "created_at": _NOW,
            }
        ]
        output = format_tickets_table(tickets)
        assert "abc-123-def-456" in output
        assert "pending" in output
        assert "87.00" in output
        assert "5.00" in output
