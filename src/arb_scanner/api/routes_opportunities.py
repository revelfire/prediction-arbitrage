"""API routes for arbitrage opportunities and pair data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from arb_scanner.api.deps import get_analytics_repo, get_repo
from arb_scanner.storage.analytics_repository import AnalyticsRepository
from arb_scanner.storage.repository import Repository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="api.opportunities")
router = APIRouter()


@router.get("/api/opportunities")
async def list_opportunities(
    limit: int = Query(50, ge=1, le=500),
    since: str | None = Query(None),
    repo: Repository = Depends(get_repo),
    analytics_repo: AnalyticsRepository = Depends(get_analytics_repo),
) -> list[dict[str, Any]]:
    """Fetch recent arbitrage opportunities.

    Args:
        limit: Maximum number of results to return.
        since: ISO-format datetime to filter from.
        repo: Injected Repository.
        analytics_repo: Injected AnalyticsRepository.

    Returns:
        List of opportunity records as dictionaries.
    """
    try:
        if since:
            since_dt = datetime.fromisoformat(since)
            return await analytics_repo.get_opportunities_date_range(since_dt, None, limit)
        return await repo.get_recent_opportunities(limit)
    except Exception as exc:
        logger.error("opportunities_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/api/pairs/summaries")
async def pair_summaries(
    hours: int = Query(24, ge=1, le=720),
    top: int = Query(10, ge=1, le=100),
    repo: AnalyticsRepository = Depends(get_analytics_repo),
) -> list[dict[str, Any]]:
    """Fetch aggregated pair statistics.

    Args:
        hours: Lookback window in hours.
        top: Maximum number of pairs to return.
        repo: Injected AnalyticsRepository.

    Returns:
        List of pair summary dictionaries.
    """
    try:
        since = datetime.now(tz=UTC) - timedelta(hours=hours)
        summaries = await repo.get_pair_summaries(since)
        return [s.model_dump() for s in summaries[:top]]
    except Exception as exc:
        logger.error("pair_summaries_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/api/pairs/{poly_id}/{kalshi_id}/history")
async def pair_history(
    poly_id: str,
    kalshi_id: str,
    hours: int = Query(24, ge=1, le=720),
    repo: AnalyticsRepository = Depends(get_analytics_repo),
) -> list[dict[str, Any]]:
    """Fetch spread history for a specific pair.

    Args:
        poly_id: Polymarket event ID.
        kalshi_id: Kalshi event ID.
        hours: Lookback window in hours.
        repo: Injected AnalyticsRepository.

    Returns:
        List of spread snapshot dictionaries.
    """
    try:
        since = datetime.now(tz=UTC) - timedelta(hours=hours)
        snapshots = await repo.get_spread_history(poly_id, kalshi_id, since)
        return [s.model_dump() for s in snapshots]
    except Exception as exc:
        logger.error("pair_history_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc
