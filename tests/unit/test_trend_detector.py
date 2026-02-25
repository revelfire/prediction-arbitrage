"""T012 - Unit tests for the TrendDetector trend-alerting engine."""

from datetime import datetime, timezone
from decimal import Decimal

from arb_scanner.models.analytics import AlertType, TrendAlert
from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.config import TrendAlertConfig
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult
from arb_scanner.notifications.trend_detector import TrendDetector

_NOW = datetime.now(tz=timezone.utc)
_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_market(venue: Venue, event_id: str) -> Market:
    """Build a minimal Market with sensible defaults."""
    return Market(
        venue=venue,
        event_id=event_id,
        title=f"Test market {event_id}",
        description="desc",
        resolution_criteria="criteria",
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.45"),
        no_bid=Decimal("0.50"),
        no_ask=Decimal("0.55"),
        volume_24h=Decimal("1000"),
        fees_pct=Decimal("0.00"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )


def _make_match(poly_id: str, kalshi_id: str) -> MatchResult:
    """Build a minimal MatchResult for a poly/kalshi pair."""
    return MatchResult(
        poly_event_id=poly_id,
        kalshi_event_id=kalshi_id,
        match_confidence=0.95,
        resolution_equivalent=True,
        resolution_risks=[],
        safe_to_arb=True,
        reasoning="Same event.",
        matched_at=_NOW,
        ttl_expires=_FUTURE,
    )


def _make_opp(poly_id: str, kalshi_id: str, spread_pct: float) -> ArbOpportunity:
    """Build a minimal ArbOpportunity with a given spread percentage.

    Args:
        poly_id: Polymarket event identifier.
        kalshi_id: Kalshi event identifier.
        spread_pct: Net spread as a decimal fraction (e.g. 0.10 for 10%).

    Returns:
        A fully-constructed ArbOpportunity.
    """
    return ArbOpportunity(
        match=_make_match(poly_id, kalshi_id),
        poly_market=_make_market(Venue.POLYMARKET, poly_id),
        kalshi_market=_make_market(Venue.KALSHI, kalshi_id),
        buy_venue=Venue.KALSHI,
        sell_venue=Venue.POLYMARKET,
        cost_per_contract=Decimal("0.90"),
        gross_profit=Decimal(str(spread_pct)),
        net_profit=Decimal(str(spread_pct)),
        net_spread_pct=Decimal(str(spread_pct)),
        max_size=Decimal("100"),
        depth_risk=False,
        detected_at=_NOW,
    )


def _make_result(
    opps: list[ArbOpportunity],
    errors: list[str] | None = None,
) -> dict[str, object]:
    """Wrap a list of ArbOpportunity into a scan result dict.

    Args:
        opps: List of arb opportunities from a scan.
        errors: Optional list of error messages.

    Returns:
        A scan result dict with '_raw_opps' key.
    """
    result: dict[str, object] = {"_raw_opps": opps}
    if errors is not None:
        result["errors"] = errors
    return result


def _make_error_result() -> dict[str, object]:
    """Build a scan result dict that represents a scan with errors and zero opps."""
    return {"_raw_opps": [], "errors": ["connection timeout"]}


def _cfg(
    window_size: int = 5,
    cooldown_minutes: int = 15,
    convergence_threshold_pct: float = 0.25,
    divergence_threshold_pct: float = 0.50,
    max_consecutive_failures: int = 3,
    zero_opp_alert_scans: int = 5,
) -> TrendAlertConfig:
    """Build a TrendAlertConfig with test-friendly defaults."""
    return TrendAlertConfig(
        enabled=True,
        window_size=window_size,
        cooldown_minutes=cooldown_minutes,
        convergence_threshold_pct=convergence_threshold_pct,
        divergence_threshold_pct=divergence_threshold_pct,
        max_consecutive_failures=max_consecutive_failures,
        zero_opp_alert_scans=zero_opp_alert_scans,
    )


def _alert_types(alerts: list[TrendAlert]) -> list[AlertType]:
    """Extract alert types from a list of TrendAlert objects."""
    return [a.alert_type for a in alerts]


# ---------------------------------------------------------------------------
# T005 - Core structure and window management
# ---------------------------------------------------------------------------


class TestEmptyAndWindowFill:
    """Tests for window initialization and sizing."""

    def test_empty_window_no_alerts(self) -> None:
        """Ingest one scan; window has only 1 entry, should return no alerts."""
        td = TrendDetector(_cfg(window_size=5))
        opp = _make_opp("poly-1", "kalshi-1", 0.10)
        alerts = td.ingest(_make_result([opp]))
        assert alerts == []

    def test_window_fills_correctly(self) -> None:
        """Ingest window_size+1 scans; verify window does not exceed maxlen."""
        cfg = _cfg(window_size=5)
        td = TrendDetector(cfg)
        opp = _make_opp("poly-1", "kalshi-1", 0.10)
        for _ in range(cfg.window_size + 1):
            td.ingest(_make_result([opp]))
        assert len(td._window) <= cfg.window_size

    def test_pair_key_format(self) -> None:
        """_pair_key returns 'poly_id/kalshi_id' format."""
        opp = _make_opp("ABC", "XYZ", 0.05)
        assert TrendDetector._pair_key(opp) == "ABC/XYZ"

    def test_init_counters_zero(self) -> None:
        """Freshly created detector has zero failure/zero-opp counters."""
        td = TrendDetector(_cfg())
        assert td._consecutive_failures == 0
        assert td._consecutive_zero_opps == 0

    def test_empty_scan_result_no_crash(self) -> None:
        """An empty dict scan result does not crash."""
        td = TrendDetector(_cfg())
        alerts = td.ingest({})
        assert isinstance(alerts, list)

    def test_multiple_pairs_tracked(self) -> None:
        """Multiple pairs in one scan are tracked independently."""
        td = TrendDetector(_cfg())
        opps = [
            _make_opp("P1", "K1", 0.05),
            _make_opp("P2", "K2", 0.10),
        ]
        td.ingest(_make_result(opps))
        assert "P1/K1" in td._window[-1]
        assert "P2/K2" in td._window[-1]


# ---------------------------------------------------------------------------
# Rolling helpers
# ---------------------------------------------------------------------------


class TestRollingHelpers:
    """Verify rolling average, max, and pairs_in_window computations."""

    def test_rolling_avg(self) -> None:
        """rolling_avg returns the mean across all window entries."""
        td = TrendDetector(_cfg())
        td.ingest(_make_result([_make_opp("P1", "K1", 0.04)]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.06)]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.08)]))
        avg = td._rolling_avg("P1/K1")
        assert avg == Decimal("0.06")

    def test_rolling_avg_unknown_pair(self) -> None:
        """rolling_avg returns None for an unknown pair."""
        td = TrendDetector(_cfg())
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        assert td._rolling_avg("X/Y") is None

    def test_rolling_max(self) -> None:
        """rolling_max returns the max spread in the window."""
        td = TrendDetector(_cfg())
        td.ingest(_make_result([_make_opp("P1", "K1", 0.03)]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.09)]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        assert td._rolling_max("P1/K1") == Decimal("0.09")

    def test_rolling_max_unknown_pair(self) -> None:
        """rolling_max returns None for an unknown pair."""
        td = TrendDetector(_cfg())
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        assert td._rolling_max("X/Y") is None

    def test_pairs_in_window_threshold(self) -> None:
        """pairs_in_window filters by minimum occurrence count."""
        td = TrendDetector(_cfg())
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05), _make_opp("P2", "K2", 0.05)]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        assert td._pairs_in_window(3) == {"P1/K1"}
        assert "P2/K2" not in td._pairs_in_window(3)
        assert td._pairs_in_window(1) == {"P1/K1", "P2/K2"}


# ---------------------------------------------------------------------------
# T006 - Convergence detection
# ---------------------------------------------------------------------------


class TestConvergence:
    """Tests for convergence (spread narrowing) detection."""

    def test_convergence_detected(self) -> None:
        """Spreads around 10% then drop to 5% (>25% drop). Expect convergence."""
        cfg = _cfg(window_size=5, convergence_threshold_pct=0.25)
        td = TrendDetector(cfg)
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.10)]))
        alerts = td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.05)]))
        types = _alert_types(alerts)
        assert AlertType.convergence in types

    def test_convergence_not_detected_small_drop(self) -> None:
        """Spread drops from 10% to 9% (only 10% drop). No convergence alert."""
        cfg = _cfg(window_size=5, convergence_threshold_pct=0.25)
        td = TrendDetector(cfg)
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.10)]))
        alerts = td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.09)]))
        types = _alert_types(alerts)
        assert AlertType.convergence not in types

    def test_convergence_event_ids(self) -> None:
        """Convergence alert carries correct poly/kalshi event IDs."""
        cfg = _cfg(window_size=3, convergence_threshold_pct=0.10, cooldown_minutes=0)
        td = TrendDetector(cfg)
        for _ in range(3):
            td.ingest(_make_result([_make_opp("PX", "KX", 0.20)]))
        alerts = td.ingest(_make_result([_make_opp("PX", "KX", 0.01)]))
        conv = [a for a in alerts if a.alert_type == AlertType.convergence]
        assert len(conv) >= 1
        assert conv[0].poly_event_id == "PX"
        assert conv[0].kalshi_event_id == "KX"

    def test_convergence_message_format(self) -> None:
        """Convergence alert message contains 'converging' keyword."""
        cfg = _cfg(window_size=3, convergence_threshold_pct=0.10, cooldown_minutes=0)
        td = TrendDetector(cfg)
        for _ in range(3):
            td.ingest(_make_result([_make_opp("P1", "K1", 0.20)]))
        alerts = td.ingest(_make_result([_make_opp("P1", "K1", 0.01)]))
        conv = [a for a in alerts if a.alert_type == AlertType.convergence]
        assert len(conv) >= 1
        assert "converging" in conv[0].message.lower()


# ---------------------------------------------------------------------------
# T007 - Divergence detection
# ---------------------------------------------------------------------------


class TestDivergence:
    """Tests for divergence (spread widening) detection."""

    def test_divergence_detected(self) -> None:
        """Spreads around 5% then jump to 10% (>50% rise). Expect divergence."""
        cfg = _cfg(window_size=5, divergence_threshold_pct=0.50)
        td = TrendDetector(cfg)
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.05)]))
        alerts = td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.10)]))
        types = _alert_types(alerts)
        assert AlertType.divergence in types

    def test_divergence_not_detected_small_rise(self) -> None:
        """Spread rises from 5% to 6% (20% rise). No divergence alert."""
        cfg = _cfg(window_size=5, divergence_threshold_pct=0.50)
        td = TrendDetector(cfg)
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.05)]))
        alerts = td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.06)]))
        types = _alert_types(alerts)
        assert AlertType.divergence not in types

    def test_divergence_message_format(self) -> None:
        """Divergence alert message contains 'diverging' keyword."""
        cfg = _cfg(window_size=3, divergence_threshold_pct=0.10, cooldown_minutes=0)
        td = TrendDetector(cfg)
        for _ in range(3):
            td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        alerts = td.ingest(_make_result([_make_opp("P1", "K1", 0.50)]))
        div = [a for a in alerts if a.alert_type == AlertType.divergence]
        assert len(div) >= 1
        assert "diverging" in div[0].message.lower()


# ---------------------------------------------------------------------------
# T008 - New high detection
# ---------------------------------------------------------------------------


class TestNewHigh:
    """Tests for new-high spread detection."""

    def test_new_high_detected(self) -> None:
        """Window max is 8%, current is 10%. Expect new_high alert."""
        cfg = _cfg(window_size=5, cooldown_minutes=0)
        td = TrendDetector(cfg)
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.08)]))
        alerts = td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.10)]))
        types = _alert_types(alerts)
        assert AlertType.new_high in types

    def test_new_high_not_detected_equal(self) -> None:
        """Window max is 10%, current is 10%. No new_high (must exceed)."""
        cfg = _cfg(window_size=5)
        td = TrendDetector(cfg)
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.10)]))
        alerts = td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.10)]))
        types = _alert_types(alerts)
        assert AlertType.new_high not in types

    def test_new_high_not_detected_lower(self) -> None:
        """Current spread below previous max does not trigger new_high."""
        cfg = _cfg(window_size=5)
        td = TrendDetector(cfg)
        td.ingest(_make_result([_make_opp("P1", "K1", 0.10)]))
        alerts = td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        types = _alert_types(alerts)
        assert AlertType.new_high not in types

    def test_new_high_only_compares_previous(self) -> None:
        """New pair with no prior data does not trigger new_high."""
        cfg = _cfg(window_size=5)
        td = TrendDetector(cfg)
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        alerts = td.ingest(_make_result([_make_opp("P2", "K2", 0.99)]))
        highs = [a for a in alerts if a.alert_type == AlertType.new_high]
        assert highs == []

    def test_new_high_spread_fields(self) -> None:
        """new_high alert has correct spread_before (prev max) and spread_after."""
        cfg = _cfg(window_size=5, cooldown_minutes=0)
        td = TrendDetector(cfg)
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.08)]))
        alerts = td.ingest(_make_result([_make_opp("P1", "K1", 0.12)]))
        highs = [a for a in alerts if a.alert_type == AlertType.new_high]
        assert len(highs) == 1
        assert highs[0].spread_before == Decimal("0.08")
        assert highs[0].spread_after == Decimal("0.12")

    def test_new_high_message_format(self) -> None:
        """new_high alert message contains 'New high' text."""
        cfg = _cfg(window_size=3, cooldown_minutes=0)
        td = TrendDetector(cfg)
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        alerts = td.ingest(_make_result([_make_opp("P1", "K1", 0.12)]))
        highs = [a for a in alerts if a.alert_type == AlertType.new_high]
        assert len(highs) >= 1
        assert "New high" in highs[0].message


# ---------------------------------------------------------------------------
# T009 - Disappeared detection
# ---------------------------------------------------------------------------


class TestDisappeared:
    """Tests for pair-disappeared detection."""

    def test_disappeared_detected(self) -> None:
        """Pair present in 4 of 5 scans, then absent. Expect disappeared alert."""
        cfg = _cfg(window_size=5, cooldown_minutes=0)
        td = TrendDetector(cfg)
        opp = _make_opp("poly-1", "kalshi-1", 0.10)
        for _ in range(4):
            td.ingest(_make_result([opp]))
        alerts = td.ingest(_make_result([]))
        types = _alert_types(alerts)
        assert AlertType.disappeared in types

    def test_disappeared_not_fired_low_count(self) -> None:
        """Pair in only 2 of previous scans, then absent. No disappeared (needs >=3)."""
        cfg = _cfg(window_size=5)
        td = TrendDetector(cfg)
        opp = _make_opp("poly-1", "kalshi-1", 0.10)
        for _ in range(2):
            td.ingest(_make_result([opp]))
        for _ in range(2):
            td.ingest(_make_result([]))
        alerts = td.ingest(_make_result([]))
        types = _alert_types(alerts)
        assert AlertType.disappeared not in types

    def test_disappeared_last_known_spread(self) -> None:
        """Disappeared alert uses the most recent spread as spread_before."""
        cfg = _cfg(window_size=10, cooldown_minutes=0)
        td = TrendDetector(cfg)
        td.ingest(_make_result([_make_opp("P1", "K1", 0.03)]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.07)]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.09)]))
        alerts = td.ingest(_make_result([]))
        dis = [a for a in alerts if a.alert_type == AlertType.disappeared]
        assert len(dis) == 1
        assert dis[0].spread_before == Decimal("0.09")
        assert dis[0].spread_after is None

    def test_disappeared_count_in_message(self) -> None:
        """Disappeared alert message includes the occurrence count."""
        cfg = _cfg(window_size=10, cooldown_minutes=0)
        td = TrendDetector(cfg)
        for _ in range(5):
            td.ingest(_make_result([_make_opp("P1", "K1", 0.06)]))
        alerts = td.ingest(_make_result([]))
        dis = [a for a in alerts if a.alert_type == AlertType.disappeared]
        assert len(dis) == 1
        assert "5 recent scans" in dis[0].message


# ---------------------------------------------------------------------------
# T010 - Health detection
# ---------------------------------------------------------------------------


class TestHealthAlerts:
    """Tests for health-related alerts (consecutive failures, zero opps)."""

    def test_health_consecutive_failures(self) -> None:
        """Ingest 3 scans with errors and zero opps. Expect health alert on 3rd."""
        cfg = _cfg(max_consecutive_failures=3)
        td = TrendDetector(cfg)
        alerts_all: list[TrendAlert] = []
        for _ in range(3):
            alerts_all = td.ingest(_make_error_result())
        types = _alert_types(alerts_all)
        assert AlertType.health_consecutive_failures in types

    def test_health_zero_opps(self) -> None:
        """Ingest 5 scans with zero opps (no errors). Expect health alert on 5th."""
        cfg = _cfg(zero_opp_alert_scans=5)
        td = TrendDetector(cfg)
        alerts_all: list[TrendAlert] = []
        for _ in range(5):
            alerts_all = td.ingest(_make_result([]))
        types = _alert_types(alerts_all)
        assert AlertType.health_zero_opps in types

    def test_health_counters_reset(self) -> None:
        """After 2 failure scans, a good scan resets counter. 2 more failures: no alert."""
        cfg = _cfg(max_consecutive_failures=3)
        td = TrendDetector(cfg)
        td.ingest(_make_error_result())
        td.ingest(_make_error_result())
        opp = _make_opp("poly-1", "kalshi-1", 0.10)
        td.ingest(_make_result([opp]))
        td.ingest(_make_error_result())
        alerts = td.ingest(_make_error_result())
        types = _alert_types(alerts)
        assert AlertType.health_consecutive_failures not in types

    def test_health_alerts_null_ids(self) -> None:
        """Health alerts have None for poly/kalshi event IDs and spread fields."""
        cfg = _cfg(max_consecutive_failures=1, cooldown_minutes=0)
        td = TrendDetector(cfg)
        alerts = td.ingest(_make_error_result())
        health = [a for a in alerts if a.alert_type == AlertType.health_consecutive_failures]
        assert len(health) == 1
        assert health[0].poly_event_id is None
        assert health[0].kalshi_event_id is None
        assert health[0].spread_before is None
        assert health[0].spread_after is None

    def test_errors_with_opps_not_failure(self) -> None:
        """Errors WITH opportunities should not count as a failure."""
        cfg = _cfg(max_consecutive_failures=2, cooldown_minutes=0)
        td = TrendDetector(cfg)
        for _ in range(3):
            td.ingest(_make_result([_make_opp("P1", "K1", 0.05)], errors=["partial err"]))
        alerts = td.ingest(_make_result([_make_opp("P1", "K1", 0.05)], errors=["partial err"]))
        health = [a for a in alerts if a.alert_type == AlertType.health_consecutive_failures]
        assert health == []

    def test_zero_opps_reset_on_opps(self) -> None:
        """Finding opps resets the zero-opp counter."""
        cfg = _cfg(zero_opp_alert_scans=3, cooldown_minutes=0)
        td = TrendDetector(cfg)
        td.ingest(_make_result([]))
        td.ingest(_make_result([]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))  # resets
        td.ingest(_make_result([]))
        alerts = td.ingest(_make_result([]))
        zero = [a for a in alerts if a.alert_type == AlertType.health_zero_opps]
        assert zero == []

    def test_health_on_first_scan(self) -> None:
        """Health alerts can fire even on first scan (< 2 window entries)."""
        cfg = _cfg(max_consecutive_failures=1, cooldown_minutes=0)
        td = TrendDetector(cfg)
        alerts = td.ingest(_make_error_result())
        types = _alert_types(alerts)
        assert AlertType.health_consecutive_failures in types


# ---------------------------------------------------------------------------
# T011 - Cooldown filtering
# ---------------------------------------------------------------------------


class TestCooldown:
    """Tests for cooldown-based deduplication of alerts."""

    def test_cooldown_blocks_duplicate(self) -> None:
        """Trigger convergence twice rapidly; second is blocked by cooldown."""
        cfg = _cfg(window_size=5, cooldown_minutes=15, convergence_threshold_pct=0.25)
        td = TrendDetector(cfg)
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.10)]))
        alerts1 = td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.05)]))
        convergence1 = [a for a in alerts1 if a.alert_type == AlertType.convergence]
        assert len(convergence1) >= 1
        alerts2 = td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.05)]))
        convergence2 = [a for a in alerts2 if a.alert_type == AlertType.convergence]
        assert len(convergence2) == 0

    def test_cooldown_zero_allows_all(self) -> None:
        """With cooldown_minutes=0, both triggers should pass (no blocking)."""
        cfg = _cfg(window_size=5, cooldown_minutes=0, convergence_threshold_pct=0.25)
        td = TrendDetector(cfg)
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.10)]))
        alerts1 = td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.05)]))
        convergence1 = [a for a in alerts1 if a.alert_type == AlertType.convergence]
        assert len(convergence1) >= 1
        alerts2 = td.ingest(_make_result([_make_opp("poly-1", "kalshi-1", 0.05)]))
        convergence2 = [a for a in alerts2 if a.alert_type == AlertType.convergence]
        assert len(convergence2) >= 1

    def test_cooldown_different_pairs_independent(self) -> None:
        """Cooldown is per (alert_type, pair). Different pairs fire independently."""
        cfg = _cfg(window_size=5, cooldown_minutes=60)
        td = TrendDetector(cfg)
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))
        td.ingest(_make_result([_make_opp("P1", "K1", 0.05), _make_opp("P2", "K2", 0.05)]))
        alerts = td.ingest(_make_result([_make_opp("P1", "K1", 0.12), _make_opp("P2", "K2", 0.12)]))
        highs = [a for a in alerts if a.alert_type == AlertType.new_high]
        assert len(highs) == 2

    def test_cooldown_different_types_independent(self) -> None:
        """Cooldown for convergence does not block divergence for same pair.

        Verifies the cooldown key includes alert_type, so firing one type
        does not suppress a different type for the same market pair.
        """
        cfg = _cfg(
            window_size=10,
            convergence_threshold_pct=0.10,
            divergence_threshold_pct=0.10,
            cooldown_minutes=60,
        )
        td = TrendDetector(cfg)
        # Seed with stable spread
        for _ in range(4):
            td.ingest(_make_result([_make_opp("P1", "K1", 0.10)]))
        # Trigger convergence
        alerts_conv = td.ingest(_make_result([_make_opp("P1", "K1", 0.01)]))
        conv = [a for a in alerts_conv if a.alert_type == AlertType.convergence]
        assert len(conv) == 1

        # Convergence cooldown should be set, but divergence cooldown should not
        assert ("convergence", "P1/K1") in td._cooldowns
        assert ("divergence", "P1/K1") not in td._cooldowns

    def test_alert_dispatched_at_is_utc(self) -> None:
        """All alert timestamps use UTC."""
        cfg = _cfg(max_consecutive_failures=1, cooldown_minutes=0)
        td = TrendDetector(cfg)
        alerts = td.ingest(_make_error_result())
        assert len(alerts) >= 1
        assert alerts[0].dispatched_at.tzinfo is not None
