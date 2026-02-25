"""API routes for scanner health metrics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from arb_scanner.api.deps import get_analytics_repo
from arb_scanner.storage.analytics_repository import AnalyticsRepository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="api.health")
router = APIRouter()


@router.get("/api/health")
async def health_metrics(
    hours: int = Query(24, ge=1, le=720),
    repo: AnalyticsRepository = Depends(get_analytics_repo),
) -> list[dict[str, Any]]:
    """Fetch hourly scanner health summaries.

    Args:
        hours: Lookback window in hours.
        repo: Injected AnalyticsRepository.

    Returns:
        List of scan health summary dictionaries.
    """
    try:
        since = datetime.now(tz=UTC) - timedelta(hours=hours)
        summaries = await repo.get_scan_health(since)
        return [s.model_dump() for s in summaries]
    except Exception as exc:
        logger.error("health_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/api/health/scans")
async def recent_scans(
    limit: int = Query(20, ge=1, le=200),
    repo: AnalyticsRepository = Depends(get_analytics_repo),
) -> list[dict[str, Any]]:
    """Fetch recent scan log entries.

    Args:
        limit: Maximum number of scan logs to return.
        repo: Injected AnalyticsRepository.

    Returns:
        List of scan log records as dictionaries.
    """
    try:
        return await repo.get_recent_scan_logs(limit)
    except Exception as exc:
        logger.error("scans_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc
