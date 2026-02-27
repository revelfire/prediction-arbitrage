"""Integration tests for the watch loop with deduplication and webhook alerting."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from arb_scanner.cli.watch import _extract_new_opps, _opp_dedup_key, run_watch
from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.config import (
    ArbThresholds,
    ClaudeConfig,
    FeeSchedule,
    FeesConfig,
    NotificationConfig,
    ScanConfig,
    Settings,
    StorageConfig,
)
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult

_NOW = datetime.now(tz=timezone.utc)


def _make_settings(interval: int = 1) -> Settings:
    """Build a minimal Settings for watch tests."""
    return Settings(
        storage=StorageConfig(database_url="postgresql://localhost/unused"),
        fees=FeesConfig(
            polymarket=FeeSchedule(taker_fee_pct=Decimal("0.0"), fee_model="on_winnings"),
            kalshi=FeeSchedule(
                taker_fee_pct=Decimal("0.07"),
                fee_model="per_contract",
                fee_cap=Decimal("0.07"),
            ),
        ),
        claude=ClaudeConfig(api_key="test-key"),
        arb_thresholds=ArbThresholds(min_net_spread_pct=Decimal("0.01")),
        scanning=ScanConfig(interval_seconds=interval),
        notifications=NotificationConfig(
            enabled=True,
            slack_webhook="https://hooks.slack.com/test",
            min_spread_to_notify_pct=Decimal("0.01"),
        ),
    )


def _make_opp(
    opp_id: str = "opp-001",
    spread: str = "0.05",
    poly_eid: str = "poly-001",
    kalshi_eid: str = "kalshi-001",
) -> ArbOpportunity:
    """Build a test ArbOpportunity with configurable id and spread."""
    match = MatchResult(
        poly_event_id=poly_eid,
        kalshi_event_id=kalshi_eid,
        match_confidence=0.95,
        resolution_equivalent=True,
        resolution_risks=[],
        safe_to_arb=True,
        reasoning="Test",
        matched_at=_NOW,
        ttl_expires=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    poly = Market(
        venue=Venue.POLYMARKET,
        event_id=poly_eid,
        title="Test market",
        description="desc",
        resolution_criteria="criteria",
        yes_bid=Decimal("0.60"),
        yes_ask=Decimal("0.62"),
        no_bid=Decimal("0.33"),
        no_ask=Decimal("0.35"),
        volume_24h=Decimal("10000"),
        fees_pct=Decimal("0.00"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )
    kalshi = Market(
        venue=Venue.KALSHI,
        event_id=kalshi_eid,
        title="Test market",
        description="desc",
        resolution_criteria="criteria",
        yes_bid=Decimal("0.60"),
        yes_ask=Decimal("0.65"),
        no_bid=Decimal("0.30"),
        no_ask=Decimal("0.35"),
        volume_24h=Decimal("5000"),
        fees_pct=Decimal("0.07"),
        fee_model="per_contract",
        last_updated=_NOW,
    )
    return ArbOpportunity(
        id=opp_id,
        match=match,
        poly_market=poly,
        kalshi_market=kalshi,
        buy_venue=Venue.POLYMARKET,
        sell_venue=Venue.KALSHI,
        cost_per_contract=Decimal("0.90"),
        gross_profit=Decimal("0.10"),
        net_profit=Decimal("0.03"),
        net_spread_pct=Decimal(spread),
        max_size=Decimal("100"),
        annualized_return=Decimal("0.34"),
        depth_risk=False,
        detected_at=_NOW,
    )


def _scan_result(opps: list[ArbOpportunity]) -> dict[str, Any]:
    """Build a mock scan result dict containing raw opportunities."""
    return {
        "scan_id": "test-scan",
        "timestamp": _NOW.isoformat(),
        "markets_scanned": {"polymarket": 10, "kalshi": 10},
        "candidate_pairs": 5,
        "opportunities": [],
        "_raw_opps": opps,
    }


class TestExtractNewOpps:
    """Tests for the _extract_new_opps helper."""

    def test_returns_new_opp_above_threshold(self) -> None:
        """Opportunity above min spread that has not been seen is returned."""
        opp = _make_opp("opp-1", "0.05")
        result = _extract_new_opps(_scan_result([opp]), set(), Decimal("0.01"))
        assert len(result) == 1
        assert result[0].id == "opp-1"

    def test_filters_already_seen(self) -> None:
        """Previously-seen opportunity dedup keys are excluded."""
        opp = _make_opp("opp-1", "0.05")
        seen = {_opp_dedup_key(opp)}
        result = _extract_new_opps(_scan_result([opp]), seen, Decimal("0.01"))
        assert len(result) == 0

    def test_filters_below_threshold(self) -> None:
        """Opportunities below min spread are excluded."""
        opp = _make_opp("opp-1", "0.005")
        result = _extract_new_opps(_scan_result([opp]), set(), Decimal("0.01"))
        assert len(result) == 0


class TestWatchLoop:
    """Tests for the full watch loop with mocked scan and webhook."""

    @pytest.mark.asyncio()
    async def test_runs_multiple_cycles_and_stops(self) -> None:
        """Watch loop should run multiple cycles and stop on event."""
        cycle_count = 0
        opp = _make_opp("opp-1")

        async def mock_scan(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal cycle_count
            cycle_count += 1
            if cycle_count >= 3:
                stop_event.set()
            return _scan_result([opp])

        config = _make_settings(interval=0)
        stop_event = asyncio.Event()

        with (
            patch("arb_scanner.cli.watch.run_scan", side_effect=mock_scan),
            patch("arb_scanner.cli.watch.dispatch_webhook", new_callable=AsyncMock),
        ):
            await run_watch(config, stop_event, dry_run=True)

        assert cycle_count >= 3

    @pytest.mark.asyncio()
    async def test_deduplication_across_cycles(self) -> None:
        """Same opportunity ID should only trigger webhook once."""
        cycle_count = 0
        opp = _make_opp("opp-dup")
        webhook_mock = AsyncMock()

        async def mock_scan(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal cycle_count
            cycle_count += 1
            if cycle_count >= 3:
                stop_event.set()
            return _scan_result([opp])

        config = _make_settings(interval=0)
        stop_event = asyncio.Event()

        with (
            patch("arb_scanner.cli.watch.run_scan", side_effect=mock_scan),
            patch("arb_scanner.cli.watch.dispatch_webhook", webhook_mock),
        ):
            await run_watch(config, stop_event, dry_run=True)

        # Webhook called only once for the first appearance
        assert webhook_mock.call_count == 1

    @pytest.mark.asyncio()
    async def test_new_opp_triggers_webhook(self) -> None:
        """A new opportunity should trigger a webhook dispatch."""
        cycle_count = 0
        webhook_mock = AsyncMock()

        async def mock_scan(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal cycle_count
            cycle_count += 1
            opp = _make_opp(
                f"opp-{cycle_count}",
                poly_eid=f"poly-{cycle_count}",
                kalshi_eid=f"kalshi-{cycle_count}",
            )
            if cycle_count >= 2:
                stop_event.set()
            return _scan_result([opp])

        config = _make_settings(interval=0)
        stop_event = asyncio.Event()

        with (
            patch("arb_scanner.cli.watch.run_scan", side_effect=mock_scan),
            patch("arb_scanner.cli.watch.dispatch_webhook", webhook_mock),
        ):
            await run_watch(config, stop_event, dry_run=True)

        # Each cycle has a new unique opp, so webhook fires each time
        assert webhook_mock.call_count == 2

    @pytest.mark.asyncio()
    async def test_scan_error_continues_loop(self) -> None:
        """A scan error should be logged but the loop continues."""
        cycle_count = 0

        async def mock_scan(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal cycle_count
            cycle_count += 1
            if cycle_count == 1:
                raise RuntimeError("boom")
            if cycle_count >= 3:
                stop_event.set()
            return _scan_result([])

        config = _make_settings(interval=0)
        stop_event = asyncio.Event()

        with (
            patch("arb_scanner.cli.watch.run_scan", side_effect=mock_scan),
            patch("arb_scanner.cli.watch.dispatch_webhook", new_callable=AsyncMock),
        ):
            await run_watch(config, stop_event, dry_run=True)

        assert cycle_count >= 3
