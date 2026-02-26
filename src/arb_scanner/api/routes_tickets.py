"""API routes for execution ticket management."""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from arb_scanner.api.deps import get_repo
from arb_scanner.storage.repository import Repository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="api.tickets")
router = APIRouter()

_VALID_STATUSES = frozenset({"pending", "approved", "expired"})


@router.get("/api/tickets")
async def list_tickets(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    repo: Repository = Depends(get_repo),
) -> list[dict[str, Any]]:
    """Fetch execution tickets with optional status filter.

    Args:
        status: Filter by status (pending, approved, expired) or None for all.
        limit: Maximum number of results.
        repo: Injected Repository.

    Returns:
        List of ticket records as dictionaries.
    """
    if status and status not in _VALID_STATUSES:
        raise HTTPException(400, f"Invalid status: {status}")
    try:
        return await repo.get_tickets_by_status(status, limit)
    except Exception as exc:
        logger.error("tickets_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/api/tickets/{arb_id}")
async def get_ticket_detail(
    arb_id: str,
    repo: Repository = Depends(get_repo),
) -> dict[str, Any]:
    """Fetch full ticket detail with opportunity and market data.

    Args:
        arb_id: The arbitrage opportunity ID.
        repo: Injected Repository.

    Returns:
        Ticket detail with venue links and market info.
    """
    try:
        row = await repo.get_ticket_detail(arb_id)
    except Exception as exc:
        logger.error("ticket_detail_failed", arb_id=arb_id, error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc
    if row is None:
        raise HTTPException(404, "Ticket not found")
    return _enrich_ticket_detail(row)


@router.post("/api/tickets/{arb_id}/approve")
async def approve_ticket(
    arb_id: str,
    repo: Repository = Depends(get_repo),
) -> dict[str, str]:
    """Approve an execution ticket.

    Args:
        arb_id: The arbitrage opportunity ID referencing the ticket.
        repo: Injected Repository.

    Returns:
        Status confirmation dictionary.
    """
    try:
        await repo.update_ticket_status(arb_id, "approved")
        return {"status": "approved"}
    except Exception as exc:
        logger.error("ticket_approve_failed", arb_id=arb_id, error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.post("/api/tickets/{arb_id}/expire")
async def expire_ticket(
    arb_id: str,
    repo: Repository = Depends(get_repo),
) -> dict[str, str]:
    """Expire an execution ticket.

    Args:
        arb_id: The arbitrage opportunity ID referencing the ticket.
        repo: Injected Repository.

    Returns:
        Status confirmation dictionary.
    """
    try:
        await repo.update_ticket_status(arb_id, "expired")
        return {"status": "expired"}
    except Exception as exc:
        logger.error("ticket_expire_failed", arb_id=arb_id, error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


def _enrich_ticket_detail(row: dict[str, Any]) -> dict[str, Any]:
    """Add venue URLs and parse JSONB fields.

    Args:
        row: Raw database row dict.

    Returns:
        Enriched detail dict with venue links.
    """
    detail = dict(row)

    # Parse JSONB leg columns
    for key in ("leg_1", "leg_2"):
        val = detail.get(key)
        if isinstance(val, str):
            detail[key] = json.loads(val)

    # Parse raw_data JSONB
    for key in ("poly_raw_data", "kalshi_raw_data"):
        val = detail.get(key)
        if isinstance(val, str):
            detail[key] = json.loads(val)

    # Build Polymarket URL from slug in raw_data
    poly_raw = detail.get("poly_raw_data") or {}
    poly_slug = poly_raw.get("slug", "") if isinstance(poly_raw, dict) else ""
    poly_event_id = detail.get("poly_event_id", "")
    if poly_slug:
        detail["poly_url"] = f"https://polymarket.com/event/{poly_slug}"
    elif poly_event_id:
        detail["poly_url"] = f"https://polymarket.com/event/{poly_event_id}"
    else:
        detail["poly_url"] = None

    # Build Kalshi URL from event_id (ticker)
    kalshi_event_id = detail.get("kalshi_event_id", "")
    if kalshi_event_id:
        detail["kalshi_url"] = f"https://kalshi.com/markets/{kalshi_event_id}"
    else:
        detail["kalshi_url"] = None

    # Drop bulky raw_data from response
    detail.pop("poly_raw_data", None)
    detail.pop("kalshi_raw_data", None)

    return detail
