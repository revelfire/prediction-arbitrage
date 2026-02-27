"""API routes for one-click trade execution."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from arb_scanner.api.deps import get_config, get_ticket_repo
from arb_scanner.models.config import Settings
from arb_scanner.storage.ticket_repository import TicketRepository

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="api.execution",
)
router = APIRouter(prefix="/api/execution", tags=["execution"])


class ExecuteBody(BaseModel):
    """Request body for the execute endpoint."""

    size_usd: Decimal


def _get_orchestrator(request: Request) -> Any:
    """Extract execution orchestrator from app state.

    Args:
        request: The incoming HTTP request.

    Returns:
        ExecutionOrchestrator instance.

    Raises:
        HTTPException: 503 when execution is not initialised.
    """
    orch = getattr(request.app.state, "execution_orchestrator", None)
    if orch is None:
        raise HTTPException(503, "Execution engine not available")
    return orch


@router.get("/status")
async def execution_status(
    config: Settings = Depends(get_config),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Return execution engine status and configuration summary.

    Args:
        config: Application settings.
        request: The incoming HTTP request.

    Returns:
        Status dict with config summary.
    """
    ec = config.execution
    orch = getattr(request.app.state, "execution_orchestrator", None)
    return {
        "enabled": ec.enabled,
        "initialised": orch is not None,
        "max_size_usd": float(ec.max_size_usd),
        "max_slippage_pct": ec.max_slippage_pct,
        "pct_of_balance": ec.pct_of_balance,
        "max_exposure_pct": ec.max_exposure_pct,
        "daily_loss_limit_usd": ec.daily_loss_limit_usd,
        "max_open_positions": ec.max_open_positions,
        "cooldown_after_loss_seconds": ec.cooldown_after_loss_seconds,
    }


@router.post("/preflight/{arb_id}")
async def run_preflight(
    arb_id: str,
    request: Request,
) -> dict[str, Any]:
    """Run pre-execution validation checks for a ticket.

    Args:
        arb_id: The ticket identifier.
        request: The incoming HTTP request.

    Returns:
        PreflightResult as dict.
    """
    orch = _get_orchestrator(request)
    try:
        result = await orch.preflight(arb_id)
    except Exception as exc:
        logger.error("preflight_failed", arb_id=arb_id, error=str(exc))
        raise HTTPException(500, f"Preflight failed: {exc}") from exc
    data: dict[str, Any] = result.model_dump(mode="json")
    return data


@router.post("/execute/{arb_id}")
async def execute_trade(
    arb_id: str,
    body: ExecuteBody,
    request: Request,
    ticket_repo: TicketRepository = Depends(get_ticket_repo),
) -> dict[str, Any]:
    """Execute a two-leg arbitrage trade.

    Args:
        arb_id: The ticket identifier.
        body: Request body with trade size.
        request: The incoming HTTP request.
        ticket_repo: Injected ticket repository.

    Returns:
        ExecutionResult as dict.
    """
    orch = _get_orchestrator(request)
    config: Settings = request.app.state.config
    max_size = Decimal(str(config.execution.max_size_usd))
    if body.size_usd <= 0:
        raise HTTPException(400, "size_usd must be positive")
    if body.size_usd > max_size:
        raise HTTPException(400, f"size_usd ${body.size_usd} exceeds max ${max_size}")

    ticket = await ticket_repo.get_ticket(arb_id)
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    if ticket["status"] not in ("pending", "approved"):
        raise HTTPException(409, f"Ticket status '{ticket['status']}' not eligible for execution")

    try:
        result = await orch.execute(arb_id, body.size_usd)
    except Exception as exc:
        logger.error("execution_failed", arb_id=arb_id, error=str(exc))
        raise HTTPException(500, f"Execution failed: {exc}") from exc
    data: dict[str, Any] = result.model_dump(mode="json")
    return data


@router.get("/orders/{arb_id}")
async def get_orders(
    arb_id: str,
    request: Request,
) -> list[dict[str, Any]]:
    """Get execution orders for a ticket.

    Args:
        arb_id: The ticket identifier.
        request: The incoming HTTP request.

    Returns:
        List of order dicts.
    """
    exec_repo = getattr(request.app.state, "execution_repo", None)
    if exec_repo is None:
        raise HTTPException(503, "Execution engine not available")
    try:
        orders: list[dict[str, Any]] = await exec_repo.get_orders_for_ticket(arb_id)
        return orders
    except Exception as exc:
        logger.error("orders_fetch_failed", arb_id=arb_id, error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.delete("/orders/{order_id}")
async def cancel_order(
    order_id: str,
    request: Request,
) -> dict[str, Any]:
    """Cancel a pending execution order.

    Args:
        order_id: Internal order UUID.
        request: The incoming HTTP request.

    Returns:
        Cancellation result.
    """
    orch = _get_orchestrator(request)
    try:
        ok = await orch.cancel_order(order_id)
    except Exception as exc:
        logger.error("cancel_failed", order_id=order_id, error=str(exc))
        raise HTTPException(500, f"Cancel failed: {exc}") from exc
    if not ok:
        raise HTTPException(404, "Order not found or not cancellable")
    return {"order_id": order_id, "status": "cancelled"}


@router.get("/open-orders")
async def list_open_orders(
    request: Request,
) -> list[dict[str, Any]]:
    """List all currently open execution orders.

    Args:
        request: The incoming HTTP request.

    Returns:
        List of open order dicts.
    """
    exec_repo = getattr(request.app.state, "execution_repo", None)
    if exec_repo is None:
        raise HTTPException(503, "Execution engine not available")
    try:
        orders: list[dict[str, Any]] = await exec_repo.get_open_orders()
        return orders
    except Exception as exc:
        logger.error("open_orders_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc
