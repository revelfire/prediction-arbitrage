"""Watch loop -- continuous scan cycles with deduplication and webhook alerts."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.cli.orchestrator import run_scan
from arb_scanner.models.analytics import TrendAlert
from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.config import NotificationConfig, Settings
from arb_scanner.notifications.alert_webhook import dispatch_trend_alert
from arb_scanner.notifications.trend_detector import TrendDetector
from arb_scanner.notifications.webhook import dispatch_webhook

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="cli.watch")


async def run_watch(
    config: Settings,
    stop_event: asyncio.Event,
    *,
    dry_run: bool = False,
) -> None:
    """Run continuous scan cycles with webhook alerts for new opportunities.

    Repeats scans at the configured interval. Tracks previously-seen
    opportunity IDs to avoid re-alerting. Fires webhooks for new
    opportunities that exceed min_spread_to_notify_pct.

    Args:
        config: Application settings.
        stop_event: Set this event to trigger graceful shutdown.
        dry_run: Use fixture data instead of live APIs.
    """
    seen_keys: set[str] = set()
    detector = TrendDetector(config.trend_alerts) if config.trend_alerts.enabled else None
    interval = config.scanning.interval_seconds
    notif = config.notifications
    min_spread = Decimal(str(notif.min_spread_to_notify_pct))
    cycle = 0

    while not stop_event.is_set():
        cycle += 1
        logger.info("watch_cycle_start", cycle=cycle)
        try:
            result = await run_scan(config, dry_run=dry_run)
        except Exception:
            logger.exception("watch_scan_error", cycle=cycle)
            await _interruptible_sleep(interval, stop_event)
            continue

        new_opps = _extract_new_opps(result, seen_keys, min_spread)
        await _notify_new_opps(new_opps, notif)
        for opp in new_opps:
            seen_keys.add(_opp_dedup_key(opp))

        if detector is not None:
            trend_alerts = detector.ingest(result)
            if trend_alerts:
                await _dispatch_trend_alerts(trend_alerts, notif)
                if not dry_run:
                    await _persist_trend_alerts(trend_alerts, config)
            logger.info("watch_trend_check", cycle=cycle, trend_alerts=len(trend_alerts))

        logger.info("watch_cycle_done", cycle=cycle, new_alerts=len(new_opps))
        await _interruptible_sleep(interval, stop_event)


def _opp_dedup_key(opp: ArbOpportunity) -> str:
    """Deterministic dedup key from the market pair and direction."""
    return f"{opp.poly_market.event_id}|{opp.kalshi_market.event_id}|{opp.buy_venue.value}"


def _extract_new_opps(
    result: dict[str, Any],
    seen_keys: set[str],
    min_spread: Decimal,
) -> list[ArbOpportunity]:
    """Extract unseen opportunities exceeding the minimum spread.

    Args:
        result: Scan result dict from run_scan (includes _raw_opps).
        seen_keys: Set of previously-alerted dedup keys.
        min_spread: Minimum net spread pct to trigger alerts.

    Returns:
        List of new ArbOpportunity objects to alert on.
    """
    raw_opps: list[ArbOpportunity] = result.get("_raw_opps", [])
    return [
        opp
        for opp in raw_opps
        if _opp_dedup_key(opp) not in seen_keys and opp.net_spread_pct >= min_spread
    ]


async def _notify_new_opps(
    opps: list[ArbOpportunity],
    notif: NotificationConfig,
) -> None:
    """Dispatch webhook notifications for new opportunities.

    Args:
        opps: New opportunities to notify about.
        notif: NotificationConfig with webhook URLs and enabled flag.
    """
    if not notif.enabled:
        return
    for opp in opps:
        await dispatch_webhook(
            opp,
            slack_url=notif.slack_webhook,
            discord_url=notif.discord_webhook,
        )


async def _dispatch_trend_alerts(
    alerts: list[TrendAlert],
    notif: NotificationConfig,
) -> None:
    """Dispatch trend alert webhooks.

    Args:
        alerts: Trend alerts to dispatch.
        notif: NotificationConfig with webhook URLs and enabled flag.
    """
    if not notif.enabled:
        return
    for alert in alerts:
        await dispatch_trend_alert(
            alert,
            slack_url=notif.slack_webhook,
            discord_url=notif.discord_webhook,
        )


async def _persist_trend_alerts(
    alerts: list[TrendAlert],
    config: Settings,
) -> None:
    """Persist trend alerts to DB (fire-and-forget).

    Args:
        alerts: Trend alerts to persist.
        config: Application settings with database URL.
    """
    from arb_scanner.storage.analytics_repository import AnalyticsRepository
    from arb_scanner.storage.db import Database

    try:
        async with Database(config.storage.database_url) as db:
            repo = AnalyticsRepository(db.pool)
            for alert in alerts:
                await repo.insert_trend_alert(alert)
    except Exception:
        logger.exception("trend_alert_persist_failed")


async def _interruptible_sleep(seconds: int, stop_event: asyncio.Event) -> None:
    """Sleep for the given duration, but wake early if stop_event is set.

    Args:
        seconds: Maximum sleep duration in seconds.
        stop_event: Event that triggers early wake-up.
    """
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
