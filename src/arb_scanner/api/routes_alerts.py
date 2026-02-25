"""API routes for trend alerts."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from arb_scanner.api.deps import get_analytics_repo
from arb_scanner.storage.analytics_repository import AnalyticsRepository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="api.alerts")
router = APIRouter()


@router.get("/api/alerts")
async def list_alerts(
    limit: int = Query(20, ge=1, le=200),
    alert_type: str | None = Query(None, alias="type"),
    repo: AnalyticsRepository = Depends(get_analytics_repo),
) -> list[dict[str, Any]]:
    """Fetch recent trend alerts with optional type filter.

    Args:
        limit: Maximum number of alerts to return.
        alert_type: Optional filter by alert type value.
        repo: Injected AnalyticsRepository.

    Returns:
        List of trend alert dictionaries.
    """
    try:
        alerts = await repo.get_recent_alerts(limit=limit, alert_type=alert_type)
        return [a.model_dump() for a in alerts]
    except Exception as exc:
        logger.error("alerts_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc
