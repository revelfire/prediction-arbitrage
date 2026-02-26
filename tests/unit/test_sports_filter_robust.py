"""Tests for check_degradation() and rate-limiting in sports_filter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from arb_scanner.flippening.sports_filter import (
    DiscoveryHealthSnapshot,
    _last_alert_time,
    _should_alert,
    _sport_zero_count,
    check_degradation,
)
from arb_scanner.models.config import FlippeningConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWED = ["nba", "nhl", "nfl"]


def _snap(
    total_scanned: int = 100,
    sports_found: int = 5,
    hit_rate: float = 0.05,
    by_sport: dict[str, int] | None = None,
    overrides_applied: int = 0,
    exclusions_applied: int = 0,
    unclassified_candidates: int = 0,
) -> DiscoveryHealthSnapshot:
    """Build a DiscoveryHealthSnapshot with sensible defaults."""
    return DiscoveryHealthSnapshot(
        total_scanned=total_scanned,
        sports_found=sports_found,
        hit_rate=hit_rate,
        by_sport=by_sport or {},
        overrides_applied=overrides_applied,
        exclusions_applied=exclusions_applied,
        unclassified_candidates=unclassified_candidates,
    )


def _config(
    min_hit_rate_pct: float = 0.01,
    discovery_alert_cooldown_minutes: int = 60,
) -> FlippeningConfig:
    """Build a FlippeningConfig with relevant fields set."""
    return FlippeningConfig(
        min_hit_rate_pct=min_hit_rate_pct,
        discovery_alert_cooldown_minutes=discovery_alert_cooldown_minutes,
    )


@pytest.fixture(autouse=True)
def _clear_alert_state() -> None:
    """Reset the module-level alert state dicts before every test."""
    _last_alert_time.clear()
    _sport_zero_count.clear()


# ---------------------------------------------------------------------------
# check_degradation — hit-rate tests
# ---------------------------------------------------------------------------


class TestHitRateDegradation:
    """Hit-rate alerting requires two consecutive low cycles."""

    def test_fires_on_two_consecutive_low_cycles(self) -> None:
        """Alert appears when both current and previous hit rates are below threshold."""
        cfg = _config(min_hit_rate_pct=0.05)
        previous = _snap(hit_rate=0.01)  # below 0.05
        current = _snap(hit_rate=0.02)  # below 0.05
        alerts = check_degradation(current, previous, cfg, _ALLOWED)
        assert len(alerts) == 1
        assert "0.0200" in alerts[0]
        assert "0.05" in alerts[0]
        assert "2 consecutive cycles" in alerts[0]

    def test_no_alert_on_first_low_cycle_only(self) -> None:
        """Alert does NOT fire when only the current cycle is low (previous=None)."""
        cfg = _config(min_hit_rate_pct=0.05)
        current = _snap(hit_rate=0.01)
        alerts = check_degradation(current, None, cfg, _ALLOWED)
        assert alerts == []

    def test_no_alert_when_previous_was_ok(self) -> None:
        """Alert does NOT fire when the previous cycle was above threshold."""
        cfg = _config(min_hit_rate_pct=0.05)
        previous = _snap(hit_rate=0.10)  # above 0.05 — OK
        current = _snap(hit_rate=0.01)  # below 0.05
        alerts = check_degradation(current, previous, cfg, _ALLOWED)
        assert alerts == []

    def test_no_alert_when_current_is_ok(self) -> None:
        """Alert does NOT fire when the current cycle is above threshold."""
        cfg = _config(min_hit_rate_pct=0.05)
        previous = _snap(hit_rate=0.01)  # below 0.05
        current = _snap(hit_rate=0.10)  # above 0.05 — recovered
        alerts = check_degradation(current, previous, cfg, _ALLOWED)
        assert alerts == []


# ---------------------------------------------------------------------------
# check_degradation — zero-results tests
# ---------------------------------------------------------------------------


class TestZeroResultsDegradation:
    """Sports-found dropping to zero fires when previous had results."""

    def test_fires_when_previous_had_results(self) -> None:
        """Alert fires when sports_found goes from >0 to 0."""
        cfg = _config()
        previous = _snap(sports_found=12)
        current = _snap(sports_found=0, hit_rate=0.0)
        alerts = check_degradation(current, previous, cfg, _ALLOWED)
        assert any("dropped to 0" in a for a in alerts)
        assert any("12" in a for a in alerts)

    def test_no_alert_when_previous_also_zero(self) -> None:
        """Alert does NOT fire when previous was also zero (not a new drop)."""
        cfg = _config()
        previous = _snap(sports_found=0, hit_rate=0.0)
        current = _snap(sports_found=0, hit_rate=0.0)
        alerts = check_degradation(current, previous, cfg, _ALLOWED)
        assert not any("dropped to 0" in a for a in alerts)

    def test_no_alert_on_first_cycle_with_zero(self) -> None:
        """Alert does NOT fire when there is no previous snapshot (first run)."""
        cfg = _config()
        current = _snap(sports_found=0, hit_rate=0.0)
        alerts = check_degradation(current, None, cfg, _ALLOWED)
        assert alerts == []


# ---------------------------------------------------------------------------
# check_degradation — EC-005
# ---------------------------------------------------------------------------


class TestEC005:
    """When total_scanned == 0, the API returned nothing — return [] immediately."""

    def test_returns_empty_list_when_api_returned_nothing(self) -> None:
        """No alerts when total_scanned is zero, regardless of other fields."""
        cfg = _config(min_hit_rate_pct=0.0)  # threshold that would otherwise trigger
        previous = _snap(sports_found=10)
        current = _snap(total_scanned=0, sports_found=0, hit_rate=0.0)
        alerts = check_degradation(current, previous, cfg, _ALLOWED)
        assert alerts == []

    def test_does_not_update_alert_state_for_ec005(self) -> None:
        """Rate-limit timestamps are not updated for the EC-005 early exit."""
        cfg = _config(min_hit_rate_pct=0.0)
        current = _snap(total_scanned=0, sports_found=0, hit_rate=0.0)
        check_degradation(current, None, cfg, _ALLOWED)
        # Nothing should have been recorded.
        assert "hit_rate_low" not in _last_alert_time
        assert "sports_zero_drop" not in _last_alert_time


# ---------------------------------------------------------------------------
# check_degradation — per-sport dropout
# ---------------------------------------------------------------------------


class TestPerSportDropout:
    """Alert when a specific sport returns 0 results for 3 consecutive cycles."""

    def test_fires_after_three_consecutive_zero_cycles(self) -> None:
        cfg = _config()
        for _ in range(2):
            check_degradation(_snap(by_sport={"nhl": 2}), None, cfg, _ALLOWED)
        alerts = check_degradation(_snap(by_sport={"nhl": 2}), None, cfg, _ALLOWED)
        assert any("'nba'" in a and "3 consecutive" in a for a in alerts)

    def test_no_alert_before_three_cycles(self) -> None:
        cfg = _config()
        check_degradation(_snap(by_sport={"nhl": 2}), None, cfg, _ALLOWED)
        alerts = check_degradation(_snap(by_sport={"nhl": 2}), None, cfg, _ALLOWED)
        assert not any("'nba'" in a for a in alerts)

    def test_resets_on_nonzero(self) -> None:
        cfg = _config()
        # 2 zero cycles for nba
        check_degradation(_snap(by_sport={"nhl": 2}), None, cfg, _ALLOWED)
        check_degradation(_snap(by_sport={"nhl": 2}), None, cfg, _ALLOWED)
        # nba comes back
        check_degradation(_snap(by_sport={"nba": 1, "nhl": 2}), None, cfg, _ALLOWED)
        # 2 more zero cycles — not yet 3 consecutive
        check_degradation(_snap(by_sport={"nhl": 2}), None, cfg, _ALLOWED)
        alerts = check_degradation(_snap(by_sport={"nhl": 2}), None, cfg, _ALLOWED)
        assert not any("'nba'" in a for a in alerts)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """_should_alert suppresses duplicate alerts within cooldown."""

    def test_first_call_fires(self) -> None:
        """The first alert for a category is always allowed."""
        assert _should_alert("test_cat", cooldown_minutes=60) is True

    def test_second_call_within_cooldown_suppressed(self) -> None:
        """A second alert within the cooldown window is suppressed."""
        _should_alert("test_cat", cooldown_minutes=60)
        assert _should_alert("test_cat", cooldown_minutes=60) is False

    def test_second_call_after_cooldown_fires(self) -> None:
        """An alert fires again once the cooldown has elapsed."""
        # Backdate the last alert time so the cooldown has expired.
        _last_alert_time["test_cat"] = datetime.now(tz=UTC) - timedelta(minutes=61)
        assert _should_alert("test_cat", cooldown_minutes=60) is True

    def test_different_categories_are_independent(self) -> None:
        """Rate limiting is per-category; different categories do not interfere."""
        _should_alert("cat_a", cooldown_minutes=60)
        # cat_b has never fired — it should be allowed.
        assert _should_alert("cat_b", cooldown_minutes=60) is True

    def test_check_degradation_respects_rate_limit(self) -> None:
        """check_degradation emits an alert the first time, then not again."""
        cfg = _config(min_hit_rate_pct=0.05, discovery_alert_cooldown_minutes=60)
        previous = _snap(sports_found=10)
        current = _snap(sports_found=0, hit_rate=0.0)

        first = check_degradation(current, previous, cfg, _ALLOWED)
        assert len(first) >= 1  # at least the zero-drop alert

        second = check_degradation(current, previous, cfg, _ALLOWED)
        # The zero-drop alert must be suppressed by rate-limiting.
        assert not any("dropped to 0" in a for a in second)

    def test_hit_rate_alert_respects_rate_limit(self) -> None:
        """hit_rate_low is suppressed on the second invocation within cooldown."""
        cfg = _config(min_hit_rate_pct=0.05, discovery_alert_cooldown_minutes=60)
        previous = _snap(hit_rate=0.01)
        current = _snap(hit_rate=0.02)

        first = check_degradation(current, previous, cfg, _ALLOWED)
        assert any("2 consecutive cycles" in a for a in first)

        second = check_degradation(current, previous, cfg, _ALLOWED)
        assert not any("2 consecutive cycles" in a for a in second)
