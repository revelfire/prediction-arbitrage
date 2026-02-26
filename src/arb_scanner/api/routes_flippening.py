"""API routes for flippening engine data."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from arb_scanner.api.deps import get_flip_repo
from arb_scanner.storage.flippening_repository import FlippeningRepository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="api.flippening",
)
router = APIRouter()


@router.get("/api/flippenings/active")
async def list_active(
    limit: int = Query(50, ge=1, le=200),
    repo: FlippeningRepository = Depends(get_flip_repo),
) -> list[dict[str, Any]]:
    """Fetch active (open) flippening signals.

    Args:
        limit: Maximum number of signals to return.
        repo: Injected FlippeningRepository.

    Returns:
        List of active signal dictionaries.
    """
    try:
        return await repo.get_active_signals(limit=limit)
    except Exception as exc:
        logger.error("active_flippenings_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/api/flippenings/history")
async def list_history(
    limit: int = Query(50, ge=1, le=200),
    sport: str | None = Query(None),
    category: str | None = Query(None),
    category_type: str | None = Query(None),
    repo: FlippeningRepository = Depends(get_flip_repo),
) -> list[dict[str, Any]]:
    """Fetch flippening signal history.

    Args:
        limit: Maximum number of records.
        sport: Optional sport filter (legacy, same as category for sports).
        category: Optional category filter.
        category_type: Optional category type filter.
        repo: Injected FlippeningRepository.

    Returns:
        List of completed signal records.
    """
    effective_cat = category or sport
    try:
        return await repo.get_history(
            limit=limit,
            sport=effective_cat,
            category_type=category_type,
        )
    except Exception as exc:
        logger.error("history_flippenings_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/api/flippenings/stats")
async def get_stats(
    sport: str | None = Query(None),
    category: str | None = Query(None),
    category_type: str | None = Query(None),
    since: str | None = Query(None),
    repo: FlippeningRepository = Depends(get_flip_repo),
) -> list[dict[str, Any]]:
    """Fetch aggregated flippening statistics.

    Args:
        sport: Optional sport filter (legacy, same as category for sports).
        category: Optional category filter.
        category_type: Optional category type filter.
        since: Optional ISO 8601 start date.
        repo: Injected FlippeningRepository.

    Returns:
        Stats dictionary.
    """
    effective_cat = category or sport
    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(
                since.replace("Z", "+00:00"),
            )
        except ValueError as exc:
            raise HTTPException(
                400,
                f"Invalid date format: {since}",
            ) from exc
    try:
        return await repo.get_stats(
            sport=effective_cat,
            since=since_dt,
            category_type=category_type,
        )
    except Exception as exc:
        logger.error("stats_flippenings_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/api/flippenings/discovery-health")
async def discovery_health(
    limit: int = Query(20, ge=1, le=200),
    repo: FlippeningRepository = Depends(get_flip_repo),
) -> list[dict[str, Any]]:
    """Fetch recent discovery health snapshots.

    Args:
        limit: Maximum number of snapshots to return.
        repo: Injected FlippeningRepository.

    Returns:
        List of discovery health snapshot dicts.
    """
    try:
        return await repo.get_discovery_health(limit=limit)
    except Exception as exc:
        logger.error("discovery_health_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/api/flippenings/ws-health")
async def ws_health(
    limit: int = Query(20, ge=1, le=200),
    repo: FlippeningRepository = Depends(get_flip_repo),
) -> list[dict[str, Any]]:
    """Fetch recent WebSocket telemetry snapshots.

    Args:
        limit: Maximum number of snapshots to return.
        repo: Injected FlippeningRepository.

    Returns:
        List of WS telemetry snapshot dicts.
    """
    try:
        return await repo.get_ws_telemetry(limit=limit)
    except Exception as exc:
        logger.error("ws_health_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc
