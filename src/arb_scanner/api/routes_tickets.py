"""API routes for execution ticket management."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

from arb_scanner.api.deps import get_repo
from arb_scanner.storage.repository import Repository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="api.tickets")
router = APIRouter()


@router.get("/api/tickets")
async def list_tickets(
    repo: Repository = Depends(get_repo),
) -> list[dict[str, Any]]:
    """Fetch pending execution tickets.

    Args:
        repo: Injected Repository.

    Returns:
        List of pending ticket records as dictionaries.
    """
    try:
        return await repo.get_pending_tickets()
    except Exception as exc:
        logger.error("tickets_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


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
