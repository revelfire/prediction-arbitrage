"""API routes for contract match results."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from arb_scanner.api.deps import get_repo
from arb_scanner.storage.repository import Repository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="api.matches")
router = APIRouter()


@router.get("/api/matches")
async def list_matches(
    include_expired: bool = Query(False),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    repo: Repository = Depends(get_repo),
) -> list[dict[str, Any]]:
    """Fetch cached contract match results.

    Args:
        include_expired: When True, include matches past their TTL.
        min_confidence: Minimum match confidence to include.
        repo: Injected Repository.

    Returns:
        List of match result records as dictionaries.
    """
    try:
        return await repo.get_all_matches(
            include_expired=include_expired,
            min_confidence=min_confidence,
        )
    except Exception as exc:
        logger.error("matches_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc
