"""T018 - Integration tests for the TrendDetector + alert_webhook pipeline.

Tests the end-to-end flow from ingesting scan results through the
TrendDetector to building and dispatching webhook payloads, without
depending on the watch loop.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from arb_scanner.models.analytics import AlertType, TrendAlert
from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.config import TrendAlertConfig
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult
from arb_scanner.notifications.alert_webhook import (
    build_trend_discord_payload,
    build_trend_slack_payload,
    dispatch_trend_alert,
)
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


def _make_opp(
    poly_id: str,
    kalshi_id: str,
    spread_pct: float,
) -> ArbOpportunity:
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
) -> dict[str, object]:
    """Wrap a list of ArbOpportunity into a scan result dict.

    Args:
        opps: List of arb opportunities from a scan.

    Returns:
        A scan result dict with '_raw_opps' key.
    """
    return {"_raw_opps": opps}


def _make_error_result() -> dict[str, object]:
    """Build a scan result representing a scan with errors and zero opps."""
    return {"_raw_opps": [], "errors": ["test error"]}


def _cfg(**overrides: object) -> TrendAlertConfig:
    """Build a TrendAlertConfig with test-friendly defaults.

    Defaults:
        window_size=5, cooldown_minutes=0, convergence_threshold_pct=0.25,
        divergence_threshold_pct=0.50, max_consecutive_failures=3,
        zero_opp_alert_scans=5.

    Any keyword argument overrides the corresponding field.
    """
    defaults: dict[str, object] = {
        "enabled": True,
        "window_size": 5,
        "cooldown_minutes": 0,
        "convergence_threshold_pct": 0.25,
        "divergence_threshold_pct": 0.50,
        "max_consecutive_failures": 3,
        "zero_opp_alert_scans": 5,
    }
    defaults.update(overrides)
    return TrendAlertConfig(**defaults)  # type: ignore[arg-type]


def _alert_types(alerts: list[TrendAlert]) -> list[AlertType]:
    """Extract alert types from a list of TrendAlert objects."""
    return [a.alert_type for a in alerts]


# ---------------------------------------------------------------------------
# 1. Detector -> dispatch integration
# ---------------------------------------------------------------------------


class TestDetectorWithWebhookDispatch:
    """TrendDetector produces an alert, then dispatch_trend_alert sends it."""

    @pytest.mark.asyncio()
    async def test_detector_with_webhook_dispatch(self) -> None:
        """Ingest scans to trigger convergence, then dispatch via webhook.

        Mocks ``_post_webhook`` to verify the HTTP layer is invoked
        without making real network calls.
        """
        cfg = _cfg(window_size=5, convergence_threshold_pct=0.25)
        td = TrendDetector(cfg)

        # Seed window with stable spread
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("P1", "K1", 0.10)]))

        # Drop spread enough to trigger convergence (>25% drop)
        alerts = td.ingest(
            _make_result([_make_opp("P1", "K1", 0.05)]),
        )
        convergence = [a for a in alerts if a.alert_type == AlertType.convergence]
        assert len(convergence) >= 1, "Expected at least one convergence alert"

        # Dispatch the alert through the webhook layer
        with patch(
            "arb_scanner.notifications.alert_webhook._post_webhook",
            new_callable=AsyncMock,
        ) as mock_post:
            await dispatch_trend_alert(
                convergence[0],
                slack_url="https://hooks.slack.com/test",
                discord_url="https://discord.com/api/webhooks/test",
            )
            assert mock_post.call_count == 2
            urls = [call.args[0] for call in mock_post.call_args_list]
            assert "https://hooks.slack.com/test" in urls
            assert "https://discord.com/api/webhooks/test" in urls


# ---------------------------------------------------------------------------
# 2. Full pipeline convergence
# ---------------------------------------------------------------------------


class TestFullPipelineConvergence:
    """Simulate a mini watch loop producing a convergence alert."""

    def test_full_pipeline_convergence(self) -> None:
        """Stable spread then sudden drop triggers convergence.

        Builds a Slack payload from the alert and validates its structure.
        """
        cfg = _cfg(window_size=5, convergence_threshold_pct=0.25)
        td = TrendDetector(cfg)

        # 5 stable scans at 10% spread
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("P1", "K1", 0.10)]))

        # 1 scan with much lower spread
        alerts = td.ingest(
            _make_result([_make_opp("P1", "K1", 0.05)]),
        )

        convergence = [a for a in alerts if a.alert_type == AlertType.convergence]
        assert len(convergence) >= 1

        alert = convergence[0]
        assert alert.poly_event_id == "P1"
        assert alert.kalshi_event_id == "K1"

        # Build Slack payload and validate structure
        payload = build_trend_slack_payload(alert)
        assert "text" in payload
        assert "blocks" in payload
        assert len(payload["blocks"]) == 2
        assert payload["blocks"][0]["type"] == "header"
        assert payload["blocks"][1]["type"] == "section"
        field_texts = [f["text"] for f in payload["blocks"][1]["fields"]]
        assert any("Pair" in t for t in field_texts)
        assert any("Spread Before" in t for t in field_texts)
        assert any("Spread After" in t for t in field_texts)


# ---------------------------------------------------------------------------
# 3. Full pipeline divergence
# ---------------------------------------------------------------------------


class TestFullPipelineDivergence:
    """Simulate a mini watch loop producing a divergence alert."""

    def test_full_pipeline_divergence(self) -> None:
        """Stable spread then sudden spike triggers divergence.

        Builds a Discord payload from the alert and validates structure.
        """
        cfg = _cfg(window_size=5, divergence_threshold_pct=0.50)
        td = TrendDetector(cfg)

        # 5 scans at 5% spread
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))

        # Spike to 10% (>50% rise)
        alerts = td.ingest(
            _make_result([_make_opp("P1", "K1", 0.10)]),
        )

        divergence = [a for a in alerts if a.alert_type == AlertType.divergence]
        assert len(divergence) >= 1

        alert = divergence[0]
        assert alert.poly_event_id == "P1"
        assert alert.kalshi_event_id == "K1"

        # Build Discord payload and validate structure
        payload = build_trend_discord_payload(alert)
        assert "content" in payload
        assert "embeds" in payload
        assert len(payload["embeds"]) == 1

        embed = payload["embeds"][0]
        assert embed["title"] == "Spread Diverging"
        assert isinstance(embed["color"], int)
        field_names = {f["name"] for f in embed["fields"]}
        assert "Pair" in field_names
        assert "Spread Before" in field_names
        assert "Spread After" in field_names
        assert "Message" in field_names


# ---------------------------------------------------------------------------
# 4. Trend alerting disabled flag
# ---------------------------------------------------------------------------


class TestTrendAlertingDisabled:
    """Verify detector works independently of the enabled flag."""

    def test_trend_alerting_disabled(self) -> None:
        """Detector still returns alerts when config.enabled=False.

        The watch loop is responsible for skipping detector creation
        when disabled. The detector itself is unaware of the flag.
        """
        cfg = _cfg(
            enabled=False,
            window_size=5,
            convergence_threshold_pct=0.25,
        )
        td = TrendDetector(cfg)

        # Seed and trigger convergence
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("P1", "K1", 0.10)]))

        alerts = td.ingest(
            _make_result([_make_opp("P1", "K1", 0.05)]),
        )
        convergence = [a for a in alerts if a.alert_type == AlertType.convergence]
        # Detector fires alerts regardless of enabled flag
        assert len(convergence) >= 1


# ---------------------------------------------------------------------------
# 5. No alerts during cold start
# ---------------------------------------------------------------------------


class TestNoAlertsDuringColdStart:
    """Verify window needs >= 2 entries for trend alerts."""

    def test_no_alerts_during_cold_start(self) -> None:
        """Ingesting only 1 scan returns no trend alerts.

        Health alerts are possible on the first scan, but trend-based
        alerts (convergence/divergence/new_high/disappeared) require
        at least 2 window entries.
        """
        cfg = _cfg(window_size=5)
        td = TrendDetector(cfg)

        alerts = td.ingest(
            _make_result([_make_opp("P1", "K1", 0.10)]),
        )

        # With only 1 entry, no trend alerts should fire
        trend_types = {
            AlertType.convergence,
            AlertType.divergence,
            AlertType.new_high,
            AlertType.disappeared,
        }
        triggered_trend = [a for a in alerts if a.alert_type in trend_types]
        assert triggered_trend == []


# ---------------------------------------------------------------------------
# 6. Multiple alert types same cycle
# ---------------------------------------------------------------------------


class TestMultipleAlertTypesSameCycle:
    """Verify a single scan can trigger multiple alert types."""

    def test_multiple_alert_types_same_cycle(self) -> None:
        """Construct a scan that triggers both divergence AND new_high.

        A spread that exceeds the rolling average by the divergence
        threshold AND exceeds all previous highs should fire both.
        """
        cfg = _cfg(
            window_size=5,
            divergence_threshold_pct=0.30,
        )
        td = TrendDetector(cfg)

        # Seed with stable moderate spread
        for _ in range(cfg.window_size):
            td.ingest(_make_result([_make_opp("P1", "K1", 0.05)]))

        # Spike well above the window max AND the average
        alerts = td.ingest(
            _make_result([_make_opp("P1", "K1", 0.15)]),
        )

        types = set(_alert_types(alerts))
        assert AlertType.divergence in types, "Expected divergence alert for spike above average"
        assert AlertType.new_high in types, "Expected new_high alert for exceeding previous max"


# ---------------------------------------------------------------------------
# 7. Cooldown across cycles
# ---------------------------------------------------------------------------


class TestCooldownAcrossCycles:
    """Verify cooldown blocks repeated alerts across ingest cycles."""

    def test_cooldown_across_cycles(self) -> None:
        """Trigger convergence, then verify cooldown blocks repeat.

        With cooldown_minutes=15 the second trigger is suppressed.
        With cooldown_minutes=0 both triggers pass through.
        """
        # -- Part 1: cooldown_minutes=15 blocks duplicate --
        cfg_with_cd = _cfg(
            window_size=5,
            cooldown_minutes=15,
            convergence_threshold_pct=0.25,
        )
        td1 = TrendDetector(cfg_with_cd)

        for _ in range(cfg_with_cd.window_size):
            td1.ingest(_make_result([_make_opp("P1", "K1", 0.10)]))

        alerts_n = td1.ingest(
            _make_result([_make_opp("P1", "K1", 0.05)]),
        )
        conv_n = [a for a in alerts_n if a.alert_type == AlertType.convergence]
        assert len(conv_n) >= 1, "First convergence should fire"

        # Ingest another low-spread scan immediately
        alerts_n1 = td1.ingest(
            _make_result([_make_opp("P1", "K1", 0.05)]),
        )
        conv_n1 = [a for a in alerts_n1 if a.alert_type == AlertType.convergence]
        assert len(conv_n1) == 0, "Second convergence blocked by cooldown"

        # -- Part 2: cooldown_minutes=0 allows both --
        cfg_no_cd = _cfg(
            window_size=5,
            cooldown_minutes=0,
            convergence_threshold_pct=0.25,
        )
        td2 = TrendDetector(cfg_no_cd)

        for _ in range(cfg_no_cd.window_size):
            td2.ingest(_make_result([_make_opp("P1", "K1", 0.10)]))

        alerts_a = td2.ingest(
            _make_result([_make_opp("P1", "K1", 0.05)]),
        )
        conv_a = [a for a in alerts_a if a.alert_type == AlertType.convergence]
        assert len(conv_a) >= 1, "First convergence with no cooldown"

        alerts_b = td2.ingest(
            _make_result([_make_opp("P1", "K1", 0.05)]),
        )
        conv_b = [a for a in alerts_b if a.alert_type == AlertType.convergence]
        assert len(conv_b) >= 1, "Second convergence passes with zero cooldown"


# ---------------------------------------------------------------------------
# 8. Health after consecutive failures
# ---------------------------------------------------------------------------


class TestHealthAfterConsecutiveFailures:
    """Verify health alerts fire after repeated error results."""

    def test_health_after_consecutive_failures(self) -> None:
        """Ingest N error results and assert health alert fires.

        After the threshold is reached, ingest a good result and verify
        the failure counter resets.
        """
        threshold = 3
        cfg = _cfg(max_consecutive_failures=threshold)
        td = TrendDetector(cfg)

        # Ingest error results up to the threshold
        alerts: list[TrendAlert] = []
        for _ in range(threshold):
            alerts = td.ingest(_make_error_result())

        health = [a for a in alerts if a.alert_type == AlertType.health_consecutive_failures]
        assert len(health) >= 1, f"Expected health alert after {threshold} failures"
        assert "consecutive" in health[0].message.lower()

        # Now ingest a good result
        good_opp = _make_opp("P1", "K1", 0.08)
        td.ingest(_make_result([good_opp]))

        # Verify counter reset: 2 more failures should NOT reach threshold
        for _ in range(threshold - 1):
            alerts = td.ingest(_make_error_result())

        health_after = [a for a in alerts if a.alert_type == AlertType.health_consecutive_failures]
        assert health_after == [], "Health alert should not fire after counter reset"
