"""Orchestrator telemetry: stall detection, schema drift, and persistence."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from arb_scanner.flippening._orch_alerts import dispatch_drift_alert
from arb_scanner.flippening.orderbook_cache import OrderBookCache
from arb_scanner.flippening.ws_telemetry import WsTelemetry
from arb_scanner.models.config import Settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.orch_telemetry",
)

STALL_THRESHOLD = 3
DRIFT_ALERT_COOLDOWN = 3600.0  # 1 hour


async def check_telemetry(
    telemetry: WsTelemetry,
    book_cache: OrderBookCache,
    config: Settings,
    repo: Any,
    client: httpx.AsyncClient,
    dry_run: bool,
    now: float,
    last_persist: float,
    stall_count: int,
    last_stall_received: int,
    last_drift_alert: float,
    stream: Any,
) -> tuple[int, int, float]:
    """Check telemetry health: persist, drift alerts, stall detection.

    Args:
        telemetry: WS telemetry tracker.
        book_cache: Order book cache.
        config: Application settings.
        repo: FlippeningRepository (None if dry_run).
        client: HTTP client for webhooks.
        dry_run: Whether to skip persistence.
        now: Current loop time.
        last_persist: Last persist timestamp.
        stall_count: Consecutive stall intervals.
        last_stall_received: cum_received at last stall check.
        last_drift_alert: Timestamp of last drift alert.
        stream: Price stream (for forced reconnect).

    Returns:
        Updated (stall_count, last_stall_received, last_drift_alert).
    """
    flip_cfg = config.flippening
    logged = telemetry.should_log(flip_cfg.ws_telemetry_interval_seconds)

    if logged:
        stall_count, last_stall_received = _handle_stall(
            telemetry,
            stall_count,
            last_stall_received,
            stream,
        )
        last_drift_alert = await _handle_schema_drift(
            telemetry,
            flip_cfg.ws_schema_match_pct,
            now,
            last_drift_alert,
            config,
            client,
            dry_run,
        )

    persist_interval = flip_cfg.ws_telemetry_persist_interval_seconds
    if not dry_run and repo is not None and now - last_persist > persist_interval:
        snap = telemetry.snapshot()
        snap["book_cache_hit_rate"] = book_cache.cache_hit_rate
        try:
            await repo.insert_ws_telemetry(snap)
        except Exception:
            logger.exception("ws_telemetry_persist_failed")

    return stall_count, last_stall_received, last_drift_alert


def _handle_stall(
    telemetry: WsTelemetry,
    stall_count: int,
    last_stall_received: int,
    stream: Any,
) -> tuple[int, int]:
    """Detect and handle WebSocket stalls."""
    if telemetry.cum_received == last_stall_received:
        stall_count += 1
        if stall_count >= 2:
            logger.warning("ws_stall_detected", stalls=stall_count)
        if stall_count >= STALL_THRESHOLD:
            logger.error("ws_stall_reconnect", stalls=stall_count)
            stall_count = 0
    else:
        stall_count = 0
    return stall_count, telemetry.cum_received


async def _handle_schema_drift(
    telemetry: WsTelemetry,
    threshold: float,
    now: float,
    last_drift_alert: float,
    config: Settings,
    client: httpx.AsyncClient,
    dry_run: bool,
) -> float:
    """Check for schema drift and dispatch alerts if needed."""
    if telemetry.check_drift(threshold):
        if now - last_drift_alert > DRIFT_ALERT_COOLDOWN:
            logger.warning("ws_schema_drift", match_rate=telemetry.schema_match_rate)
            if not dry_run:
                await dispatch_drift_alert(config, client)
            return now
    return last_drift_alert
