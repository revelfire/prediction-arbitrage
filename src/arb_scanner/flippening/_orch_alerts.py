"""Orchestrator alert dispatch: drift and discovery health."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from arb_scanner.flippening.market_classifier import (
    DiscoveryHealthSnapshot,
    check_degradation,
)
from arb_scanner.models.config import CategoryConfig, Settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.orch_alerts",
)


async def dispatch_drift_alert(config: Settings, client: httpx.AsyncClient) -> None:
    """Log schema drift (webhook silenced to reduce channel noise).

    Args:
        config: Application settings.
        client: HTTP client (unused).
    """
    logger.warning("ws_schema_drift_detected")


async def handle_discovery_health(
    health: DiscoveryHealthSnapshot,
    prev_health: DiscoveryHealthSnapshot | None,
    config: Settings,
    categories: dict[str, CategoryConfig],
    repo: Any,
    client: httpx.AsyncClient,
    dry_run: bool,
) -> None:
    """Persist health snapshot and dispatch degradation alerts.

    Args:
        health: Current discovery health snapshot.
        prev_health: Previous cycle's health snapshot.
        config: Application settings.
        categories: Active category configs.
        repo: FlippeningRepository (None if dry_run).
        client: Shared HTTP client for webhooks.
        dry_run: Whether to skip persistence and alerts.
    """
    if not dry_run and repo is not None:
        try:
            import dataclasses

            await repo.insert_discovery_health(dataclasses.asdict(health))
        except Exception:
            logger.exception("discovery_health_persist_failed")

    alerts = check_degradation(health, prev_health, config.flippening, categories)
    if not dry_run and repo is not None:
        await _persist_alerts(repo, alerts, categories, health)
    if alerts and not dry_run:
        for msg in alerts:
            logger.warning("discovery_degradation", alert=msg)


async def _persist_alerts(
    repo: Any,
    alerts: list[str],
    categories: dict[str, CategoryConfig],
    health: DiscoveryHealthSnapshot,
) -> None:
    """Persist new degradation alerts and resolve recovered categories.

    Args:
        repo: FlippeningRepository instance.
        alerts: Alert messages from check_degradation.
        categories: Active category configs.
        health: Current discovery health snapshot.
    """
    for msg in alerts:
        cat = _extract_category_from_alert(msg, categories)
        try:
            await repo.insert_discovery_alert(msg, cat)
        except Exception:
            logger.exception("discovery_alert_insert_failed")
    for cat_id in categories:
        if health.by_category.get(cat_id, 0) > 0:
            try:
                await repo.resolve_discovery_alerts(cat_id)
            except Exception:
                logger.exception("discovery_alert_resolve_failed")


def _extract_category_from_alert(
    msg: str,
    categories: dict[str, CategoryConfig],
) -> str:
    """Extract category name from an alert message string.

    Args:
        msg: The alert message.
        categories: Active category configs for matching.

    Returns:
        Matched category id or empty string.
    """
    for cat_id in categories:
        if cat_id in msg:
            return cat_id
    if "hit rate" in msg.lower():
        return "hit_rate"
    if "dropped to 0" in msg.lower():
        return "markets_zero"
    return ""
