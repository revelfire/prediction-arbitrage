"""Tests for parse_iso_datetime in arb_scanner.cli._helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import typer

from arb_scanner.cli._helpers import parse_iso_datetime


class TestValidInputs:
    """Tests for valid ISO 8601 inputs."""

    def test_date_only(self) -> None:
        """Date-only string parses to midnight UTC of that date."""
        result = parse_iso_datetime("2026-02-20")
        assert result == datetime(2026, 2, 20, tzinfo=timezone.utc)

    def test_date_only_time_is_midnight(self) -> None:
        """Date-only string sets time components to zero."""
        result = parse_iso_datetime("2026-02-20")
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0

    def test_datetime_with_time(self) -> None:
        """Datetime string preserves the time component."""
        result = parse_iso_datetime("2026-02-20T14:30:00")
        assert result == datetime(2026, 2, 20, 14, 30, 0, tzinfo=timezone.utc)

    def test_datetime_with_timezone(self) -> None:
        """Datetime with explicit UTC offset is parsed correctly."""
        result = parse_iso_datetime("2026-02-20T14:30:00+00:00")
        assert result == datetime(2026, 2, 20, 14, 30, 0, tzinfo=timezone.utc)

    def test_result_always_timezone_aware(self) -> None:
        """All parsed results have tzinfo set (timezone-aware)."""
        for value in ["2026-02-20", "2026-02-20T14:30:00", "2026-02-20T14:30:00+00:00"]:
            result = parse_iso_datetime(value)
            assert result.tzinfo is not None, f"Expected tzinfo for input '{value}'"


class TestInvalidInputs:
    """Tests for inputs that should raise typer.BadParameter."""

    def test_not_a_date(self) -> None:
        """Completely invalid string raises BadParameter."""
        with pytest.raises(typer.BadParameter, match="Invalid ISO 8601"):
            parse_iso_datetime("not-a-date")

    def test_slash_format(self) -> None:
        """Slash-separated date format raises BadParameter."""
        with pytest.raises(typer.BadParameter, match="Invalid ISO 8601"):
            parse_iso_datetime("2026/02/20")

    def test_empty_string(self) -> None:
        """Empty string raises BadParameter."""
        with pytest.raises(typer.BadParameter, match="Invalid ISO 8601"):
            parse_iso_datetime("")
