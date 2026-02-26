"""API routes for WebSocket telemetry dashboard endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from arb_scanner.api.deps import get_flip_repo
from arb_scanner.storage.flippening_repository import FlippeningRepository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="api.ws_telemetry",
)
router = APIRouter()


@router.get("/api/flippening/ws-telemetry")
async def ws_telemetry_latest(
    repo: FlippeningRepository = Depends(get_flip_repo),
) -> dict[str, Any] | None:
    """Fetch the latest WS telemetry snapshot.

    Args:
        repo: Injected FlippeningRepository.

    Returns:
        Latest telemetry snapshot dict, or None if no data.
    """
    try:
        return await repo.get_ws_telemetry_latest()
    except Exception as exc:
        logger.error("ws_telemetry_latest_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/api/flippening/ws-telemetry/history")
async def ws_telemetry_history(
    hours: int = Query(24, ge=1, le=720),
    repo: FlippeningRepository = Depends(get_flip_repo),
) -> list[dict[str, Any]]:
    """Fetch WS telemetry snapshots for the given time window.

    Args:
        hours: Lookback window in hours (max 30 days).
        repo: Injected FlippeningRepository.

    Returns:
        List of telemetry snapshot dicts ordered chronologically.
    """
    since = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    try:
        return await repo.get_ws_telemetry_history(since=since)
    except Exception as exc:
        logger.error("ws_telemetry_history_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/api/flippening/ws-telemetry/events")
async def ws_telemetry_events(
    limit: int = Query(50, ge=1, le=200),
    repo: FlippeningRepository = Depends(get_flip_repo),
) -> list[dict[str, Any]]:
    """Fetch stall/reconnect events derived from telemetry snapshots.

    Args:
        limit: Maximum number of events to return.
        repo: Injected FlippeningRepository.

    Returns:
        List of event dicts ordered by time descending.
    """
    try:
        return await repo.get_ws_telemetry_events(limit=limit)
    except Exception as exc:
        logger.error("ws_telemetry_events_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc
