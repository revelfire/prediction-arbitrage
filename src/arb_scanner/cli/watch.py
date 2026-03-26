"""Watch loop -- continuous scan cycles with deduplication and webhook alerts."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.cli.orchestrator import run_scan
from arb_scanner.models.analytics import TrendAlert
from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.config import NotificationConfig, Settings
from arb_scanner.models.market import Market, Venue
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
    buy_market, sell_market = (
        (opp.poly_market, opp.kalshi_market)
        if opp.buy_venue == Venue.POLYMARKET
        else (opp.kalshi_market, opp.poly_market)
    )
    buy_side = "yes"
    sell_side = "no"
    return {
        "arb_id": opp.id,
        "spread_pct": float(opp.net_spread_pct),
        "confidence": float(opp.match.match_confidence),
        "category": getattr(opp, "category", ""),
        "title": opp.poly_market.title,
        "ticket_type": "arbitrage",
        "poly_market_id": opp.poly_market.event_id,
        "kalshi_market_id": opp.kalshi_market.event_id,
        "poly_yes_price": float(opp.poly_market.yes_ask),
        "kalshi_yes_price": float(opp.kalshi_market.yes_ask),
        # The watch loop only has quote snapshots, not live L2 depth. Use a
        # contracts proxy from venue volume and quote price so the arb critic
        # does not mistake missing fields for zero-liquidity markets.
        "poly_depth": _estimate_contract_depth(opp.poly_market, side="yes"),
        "kalshi_depth": _estimate_contract_depth(opp.kalshi_market, side="yes"),
        "price_age_seconds": _price_age_seconds(opp),
        "leg_1": _build_entry_leg(buy_market, side=buy_side),
        "leg_2": _build_entry_leg(sell_market, side=sell_side),
    }


def _build_entry_leg(market: Market, *, side: str) -> dict[str, object]:
    """Build a direction-aware entry leg for arb auto-execution.

    Args:
        market: Venue market model.
        side: "yes" or "no".

    Returns:
        Normalized leg dict for slippage and execution prechecks.
    """
    price = market.yes_ask if side == "yes" else market.no_ask
    leg: dict[str, object] = {
        "venue": market.venue.value,
        "action": "buy",
        "side": side,
        "price": float(price),
        "market_id": market.event_id,
    }
    if market.venue == Venue.POLYMARKET:
        leg["token_id"] = _extract_poly_token(market, side=side)
    else:
        leg["ticker"] = _extract_kalshi_ticker(market)
    return leg


def _extract_poly_token(market: Market, *, side: str) -> str:
    """Extract the side-appropriate Polymarket CLOB token id."""
    import json as _json

    raw_ids = market.raw_data.get("clobTokenIds", "")
    token_ids: list[str] = []
    if isinstance(raw_ids, list) and raw_ids:
        token_ids = [str(token_id) for token_id in raw_ids if str(token_id)]
    elif isinstance(raw_ids, str) and raw_ids:
        try:
            parsed = _json.loads(raw_ids)
            if isinstance(parsed, list) and parsed:
                token_ids = [str(token_id) for token_id in parsed if str(token_id)]
        except (ValueError, _json.JSONDecodeError):
            pass
    if not token_ids:
        return ""
    index = 0 if side == "yes" else 1
    return token_ids[index] if index < len(token_ids) else token_ids[0]


def _extract_kalshi_ticker(market: Market) -> str:
    """Extract the executable Kalshi ticker for a market."""
    ticker = market.raw_data.get("ticker")
    if isinstance(ticker, str) and ticker:
        return ticker
    return market.event_id


def _estimate_contract_depth(market: Market, *, side: str) -> int:
    """Approximate contracts of available depth from 24h volume and price."""
    price = market.yes_ask if side == "yes" else market.no_ask
    if price <= 0:
        return 0
    try:
        return max(int(market.volume_24h / price), 0)
    except (ArithmeticError, ValueError):
        return 0


def _price_age_seconds(opp: ArbOpportunity) -> int:
    """Return the age in seconds of the stalest venue quote for an opp."""
    now = datetime.now(tz=UTC)
    ages = []
    for market in (opp.poly_market, opp.kalshi_market):
        delta = now - market.last_updated
        ages.append(max(int(delta.total_seconds()), 0))
    return max(ages, default=0)


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
