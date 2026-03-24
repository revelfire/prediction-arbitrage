"""Watch loop -- continuous scan cycles with deduplication and webhook alerts."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.cli.orchestrator import run_scan
from arb_scanner.models.analytics import TrendAlert
from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.config import NotificationConfig, Settings
from arb_scanner.notifications.trend_detector import TrendDetector
from arb_scanner.notifications.webhook import dispatch_webhook_batch

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="cli.watch")

_OPP_BATCH_INTERVAL_S = 600.0  # 10 minutes between ticket batches


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
    opp_buffer: list[ArbOpportunity] = []
    last_opp_flush = 0.0

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
        opp_buffer.extend(new_opps)
        for opp in new_opps:
            seen_keys.add(_opp_dedup_key(opp))

        now = time.monotonic()
        if opp_buffer and (now - last_opp_flush) >= _OPP_BATCH_INTERVAL_S:
            await _notify_new_opps(opp_buffer, notif)
            opp_buffer.clear()
            last_opp_flush = now

        if not dry_run and new_opps:
            await _feed_auto_pipeline(new_opps, config)

        if detector is not None:
            trend_alerts = detector.ingest(result)
            if trend_alerts:
                await _dispatch_trend_alerts(trend_alerts, notif)
                if not dry_run:
                    await _persist_trend_alerts(trend_alerts, config)
            logger.info("watch_trend_check", cycle=cycle, trend_alerts=len(trend_alerts))

        if not dry_run:
            await _auto_expire_tickets(config)

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
    """Dispatch a single batched webhook for new opportunities.

    Args:
        opps: New opportunities to notify about.
        notif: NotificationConfig with webhook URLs and enabled flag.
    """
    if not notif.enabled:
        return
    await dispatch_webhook_batch(
        opps,
        slack_url=notif.slack_webhook,
        discord_url=notif.discord_webhook,
    )


async def _dispatch_trend_alerts(
    alerts: list[TrendAlert],
    notif: NotificationConfig,
) -> None:
    """Log trend alerts (webhook silenced to reduce channel noise).

    Args:
        alerts: Trend alerts to log.
        notif: NotificationConfig (unused — webhooks disabled).
    """
    for alert in alerts:
        logger.info(
            "trend_alert_logged",
            alert_type=alert.alert_type.value,
            message=alert.message,
        )


async def _auto_expire_tickets(config: Settings) -> None:
    """Expire stale pending tickets (fire-and-forget).

    Args:
        config: Application settings with lifecycle config.
    """
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.ticket_repository import TicketRepository

    try:
        max_age = config.ticket_lifecycle.max_pending_hours
        async with Database(config.storage.database_url) as db:
            repo = TicketRepository(db.pool)
            expired = await repo.auto_expire(max_age_hours=max_age)
            if expired:
                logger.info("watch_tickets_expired", count=len(expired))
    except Exception:
        logger.exception("watch_ticket_expire_failed")


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


async def _feed_auto_pipeline(
    opps: list[ArbOpportunity],
    config: Settings,
) -> None:
    """Feed new opportunities to the auto-execution pipeline if available.

    Args:
        opps: New arbitrage opportunities.
        config: Application settings.
    """
    try:
        pipeline = getattr(config, "_arb_pipeline", None)
        if pipeline is None:
            return
        for opp in opps:
            opp_dict = _build_arb_opp_dict(opp)
            await pipeline.process_opportunity(opp_dict, source="arb_watch")
    except Exception:
        logger.warning("arb_pipeline_feed_failed")


def _build_arb_opp_dict(opp: ArbOpportunity) -> dict[str, object]:
    """Build execution-compatible dict from an ArbOpportunity.

    Args:
        opp: Arbitrage opportunity.

    Returns:
        Dict with leg data for slippage checks and execution.
    """
    poly_token = _extract_poly_token(opp)
    return {
        "arb_id": f"{opp.poly_market.event_id}_{opp.kalshi_market.event_id}",
        "spread_pct": float(opp.net_spread_pct),
        "confidence": float(opp.match.match_confidence),
        "category": getattr(opp, "category", ""),
        "title": opp.poly_market.title,
        "ticket_type": "arbitrage",
        "poly_market_id": opp.poly_market.event_id,
        "kalshi_market_id": opp.kalshi_market.event_id,
        "leg_1": {
            "venue": "polymarket",
            "token_id": poly_token,
            "price": float(opp.poly_market.yes_ask),
        },
        "leg_2": {
            "venue": "kalshi",
            "market_id": opp.kalshi_market.event_id,
            "price": float(opp.kalshi_market.yes_ask),
        },
    }


def _extract_poly_token(opp: ArbOpportunity) -> str:
    """Extract Polymarket CLOB token ID from raw market data.

    Args:
        opp: Arbitrage opportunity with poly_market.

    Returns:
        First token ID string, or empty string if unavailable.
    """
    import json as _json

    raw_ids = opp.poly_market.raw_data.get("clobTokenIds", "")
    if isinstance(raw_ids, list) and raw_ids:
        return str(raw_ids[0])
    if isinstance(raw_ids, str) and raw_ids:
        try:
            parsed = _json.loads(raw_ids)
            if isinstance(parsed, list) and parsed:
                return str(parsed[0])
        except (ValueError, _json.JSONDecodeError):
            pass
    return ""


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
