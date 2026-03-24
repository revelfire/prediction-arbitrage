"""API routes for execution ticket management."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from arb_scanner.api.deps import get_repo, get_ticket_repo
from arb_scanner.models.ticket_action import TicketPatchBody, valid_transition
from arb_scanner.storage.repository import Repository
from arb_scanner.storage.ticket_repository import TicketRepository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="api.tickets")
router = APIRouter()

_VALID_STATUSES = frozenset({"pending", "approved", "expired", "executed", "cancelled"})
_STATUS_TO_ACTION: dict[str, str] = {
    "approved": "approve",
    "executed": "execute",
    "expired": "expire",
    "cancelled": "cancel",
}


@router.get("/api/tickets")
async def list_tickets(
    status: str | None = Query(None),
    category: str | None = Query(None),
    ticket_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    repo: TicketRepository = Depends(get_ticket_repo),
) -> list[dict[str, Any]]:
    """Fetch execution tickets with optional filters.

    Args:
        status: Filter by status.
        category: Filter by category.
        ticket_type: Filter by ticket type.
        limit: Maximum number of results.
        repo: Injected TicketRepository.

    Returns:
        List of ticket records as dictionaries.
    """
    if status and status not in _VALID_STATUSES:
        raise HTTPException(400, f"Invalid status: {status}")
    try:
        rows = await repo.get_tickets(
            status=status,
            category=category,
            ticket_type=ticket_type,
            limit=limit,
        )
        return [_parse_jsonb(r) for r in rows]
    except Exception as exc:
        logger.error("tickets_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/api/tickets/summary")
async def ticket_summary(
    days: int = Query(30, ge=1, le=365),
    repo: TicketRepository = Depends(get_ticket_repo),
) -> list[dict[str, Any]]:
    """Fetch performance summary aggregated by category.

    Args:
        days: Lookback window in days.
        repo: Injected TicketRepository.

    Returns:
        List of summary dicts.
    """
    try:
        return await repo.get_summary(days=days)
    except Exception as exc:
        logger.error("ticket_summary_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/api/tickets/{arb_id}")
async def get_ticket_detail(
    arb_id: str,
    repo: TicketRepository = Depends(get_ticket_repo),
) -> dict[str, Any]:
    """Fetch full ticket detail with actions.

    Args:
        arb_id: The ticket identifier.
        repo: Injected TicketRepository.

    Returns:
        Ticket detail with actions list.
    """
    try:
        row = await repo.get_ticket(arb_id)
    except Exception as exc:
        logger.error("ticket_detail_failed", arb_id=arb_id, error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc
    if row is None:
        raise HTTPException(404, "Ticket not found")
    detail = _parse_jsonb(row)
    try:
        detail["actions"] = await repo.get_actions(arb_id)
    except Exception:
        detail["actions"] = []
    return detail


@router.patch("/api/tickets/{arb_id}")
async def patch_ticket(
    arb_id: str,
    body: TicketPatchBody,
    repo: TicketRepository = Depends(get_ticket_repo),
) -> dict[str, Any]:
    """Update ticket status, record execution data, or add annotation.

    Args:
        arb_id: The ticket identifier.
        body: PATCH request body.
        repo: Injected TicketRepository.

    Returns:
        Updated ticket status and action id.
    """
    ticket = await repo.get_ticket(arb_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")

    current_status: str = ticket["status"]

    if body.status is not None:
        if body.status not in _VALID_STATUSES:
            raise HTTPException(400, f"Invalid status: {body.status}")
        if not valid_transition(current_status, body.status):
            raise HTTPException(
                409,
                f"Cannot transition from '{current_status}' to '{body.status}'",
            )
        await repo.update_status(arb_id, body.status)
        action_name = _STATUS_TO_ACTION.get(body.status, "annotate")
    else:
        action_name = "annotate"

    slippage = _compute_slippage(ticket, body)
    action_id = str(uuid.uuid4())
    await repo.insert_action(
        action_id=action_id,
        ticket_id=arb_id,
        action=action_name,
        actual_entry_price=body.actual_entry_price,
        actual_size_usd=body.actual_size_usd,
        slippage=slippage,
        notes=body.notes or "",
    )
    return {
        "status": body.status or current_status,
        "action_id": action_id,
    }


# --- Backward-compatible thin wrappers ---


@router.post("/api/tickets/{arb_id}/approve")
async def approve_ticket(
    arb_id: str,
    repo: TicketRepository = Depends(get_ticket_repo),
    old_repo: Repository = Depends(get_repo),
) -> dict[str, str]:
    """Approve an execution ticket (legacy endpoint).

    Args:
        arb_id: The ticket identifier.
        repo: Injected TicketRepository.
        old_repo: Injected legacy Repository.

    Returns:
        Status confirmation.
    """
    ticket = await repo.get_ticket(arb_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    if not valid_transition(ticket["status"], "approved"):
        raise HTTPException(
            409,
            f"Cannot approve ticket in '{ticket['status']}' status",
        )
    try:
        await repo.update_status(arb_id, "approved")
        await repo.insert_action(
            action_id=str(uuid.uuid4()),
            ticket_id=arb_id,
            action="approve",
        )
        return {"status": "approved"}
    except Exception as exc:
        logger.error("ticket_approve_failed", arb_id=arb_id, error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.post("/api/tickets/{arb_id}/expire")
async def expire_ticket(
    arb_id: str,
    repo: TicketRepository = Depends(get_ticket_repo),
    old_repo: Repository = Depends(get_repo),
) -> dict[str, str]:
    """Expire an execution ticket (legacy endpoint).

    Args:
        arb_id: The ticket identifier.
        repo: Injected TicketRepository.
        old_repo: Injected legacy Repository.

    Returns:
        Status confirmation.
    """
    ticket = await repo.get_ticket(arb_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    if not valid_transition(ticket["status"], "expired"):
        raise HTTPException(
            409,
            f"Cannot expire ticket in '{ticket['status']}' status",
        )
    try:
        await repo.update_status(arb_id, "expired")
        await repo.insert_action(
            action_id=str(uuid.uuid4()),
            ticket_id=arb_id,
            action="expire",
        )
        return {"status": "expired"}
    except Exception as exc:
        logger.error("ticket_expire_failed", arb_id=arb_id, error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


def _parse_jsonb(row: dict[str, Any]) -> dict[str, Any]:
    """Parse JSONB string fields in a ticket row.

    Args:
        row: Raw database row dict.

    Returns:
        Dict with parsed JSON fields.
    """
    detail = dict(row)
    for key in ("leg_1", "leg_2"):
        val = detail.get(key)
        if isinstance(val, str):
            detail[key] = json.loads(val)
    return detail


def _compute_slippage(
    ticket: dict[str, Any],
    body: TicketPatchBody,
) -> Decimal | None:
    """Compute slippage between actual and suggested entry price.

    Args:
        ticket: Current ticket data.
        body: PATCH request body with actual prices.

    Returns:
        Slippage decimal or None if not computable.
    """
    if body.actual_entry_price is None:
        return None
    leg_1 = ticket.get("leg_1")
    if isinstance(leg_1, str):
        leg_1 = json.loads(leg_1)
    if not isinstance(leg_1, dict):
        return None
    suggested = leg_1.get("price")
    if suggested is None:
        return None
    try:
        return body.actual_entry_price - Decimal(str(suggested))
    except (InvalidOperation, TypeError):
        return None
