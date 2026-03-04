"""API routes for one-click trade execution."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from arb_scanner.api.deps import get_config, get_ticket_repo
from arb_scanner.execution.capital_manager import CapitalManager
from arb_scanner.models.config import Settings
from arb_scanner.models.execution import BalancesResponse, ConstraintStatus
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


def _get_capital_manager(request: Request) -> CapitalManager:
    """Extract capital manager from app state.

    Args:
        request: The incoming HTTP request.

    Returns:
        CapitalManager instance.

    Raises:
        HTTPException: 503 when capital manager is not initialised.
    """
    cm: CapitalManager | None = getattr(request.app.state, "capital_manager", None)
    if cm is None:
        raise HTTPException(503, "Capital manager not available")
    return cm


def _build_constraints(cm: CapitalManager) -> list[ConstraintStatus]:
    """Collect constraint check results from capital manager.

    Args:
        cm: The capital manager instance.

    Returns:
        List of ConstraintStatus with pass/fail for each check.
    """
    constraints: list[ConstraintStatus] = []
    suggested = cm.suggest_size()
    passed, msg = cm.check_venue_reserve(suggested)
    constraints.append(ConstraintStatus(name="Venue Reserve", ok=passed, detail=msg))
    _exp, _rem, exp_blocked = cm.check_exposure()
    constraints.append(
        ConstraintStatus(
            name="Exposure Cap",
            ok=not exp_blocked,
            detail=f"Exposure ${_exp:.2f}, remaining ${_rem:.2f}",
        )
    )
    pnl_val, limit, pnl_blocked = cm.check_daily_pnl()
    constraints.append(
        ConstraintStatus(
            name="Daily P&L Limit",
            ok=not pnl_blocked,
            detail=f"P&L ${pnl_val:.2f} / limit ${limit:.2f}",
        )
    )
    active, secs = cm.check_cooldown()
    constraints.append(
        ConstraintStatus(
            name="Post-Loss Cooldown",
            ok=not active,
            detail=f"{secs}s remaining" if active else "No cooldown",
        )
    )
    count, max_pos, pos_blocked = cm.check_open_positions()
    constraints.append(
        ConstraintStatus(
            name="Open Positions",
            ok=not pos_blocked,
            detail=f"{count} / {max_pos}",
        )
    )
    return constraints


@router.get("/balances")
async def get_balances(request: Request) -> dict[str, Any]:
    """Return live venue balances, exposure, and constraint status.

    Args:
        request: The incoming HTTP request.

    Returns:
        BalancesResponse as dict.
    """
    cm = _get_capital_manager(request)
    logger.info("balances_endpoint_called")
    try:
        await asyncio.wait_for(cm.refresh_balances(), timeout=8.0)
    except TimeoutError:
        logger.warning("balance_refresh_timeout", timeout_seconds=8)
    except Exception as exc:
        logger.warning(
            "balance_refresh_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
    try:
        exposure, remaining, _blocked = cm.check_exposure()
        pos_count, _max, _pos_blocked = cm.check_open_positions()
        resp = BalancesResponse(
            poly_balance=cm.poly_balance,
            kalshi_balance=cm.kalshi_balance,
            total_balance=cm.total_balance,
            suggested_size_usd=cm.suggest_size(),
            current_exposure=exposure,
            remaining_capacity=remaining,
            daily_pnl=cm.daily_pnl,
            open_positions=pos_count,
            constraints=_build_constraints(cm),
        )
    except Exception as exc:
        logger.error("balances_build_failed", error=str(exc))
        raise HTTPException(500, f"Failed to build balances: {exc}") from exc
    data: dict[str, Any] = resp.model_dump(mode="json")
    return data


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


@router.get("/flip-position/{arb_id}")
async def get_flip_position(
    arb_id: str,
    request: Request,
) -> dict[str, Any]:
    """Get the open flippening position for a ticket, if any.

    Args:
        arb_id: The ticket identifier.
        request: The incoming HTTP request.

    Returns:
        Position dict with string-coerced values, or 404 if none.
    """
    position_repo = getattr(request.app.state, "flip_position_repo", None)
    if position_repo is None:
        raise HTTPException(503, "Position tracking not available")
    position = await position_repo.get_position_by_arb_id(arb_id)
    if position is None:
        raise HTTPException(404, "No open position for this ticket")
    return {k: (str(v) if v is not None else None) for k, v in position.items()}


@router.post("/flip-exit/{arb_id}")
async def flip_exit(
    arb_id: str,
    request: Request,
) -> dict[str, Any]:
    """Manually trigger exit for an open flippening position.

    Places a sell order at entry price (limit), regardless of auto/manual mode.

    Args:
        arb_id: The ticket identifier.
        request: The incoming HTTP request.

    Returns:
        Exit order result dict with order_id, status, market_id.
    """
    position_repo = getattr(request.app.state, "flip_position_repo", None)
    exit_executor = getattr(request.app.state, "flip_exit_executor", None)
    if position_repo is None or exit_executor is None:
        raise HTTPException(503, "Exit execution not available")

    position = await position_repo.get_position_by_arb_id(arb_id)
    if position is None or position.get("status") != "open":
        raise HTTPException(404, "No open position for this ticket")

    event, entry_sig, exit_sig = _build_manual_exit_signals(arb_id, position)
    try:
        order_id = await exit_executor.execute_exit(exit_sig, entry_sig, event)
    except Exception as exc:
        logger.error("manual_flip_exit_failed", arb_id=arb_id, error=str(exc))
        raise HTTPException(500, f"Exit execution failed: {exc}") from exc
    if order_id is None:
        raise HTTPException(500, "Exit order was not placed — position lookup by market_id failed")
    return {"order_id": order_id, "status": "submitted", "market_id": event.market_id}


def _build_manual_exit_signals(
    arb_id: str,
    position: dict[str, Any],
) -> tuple[Any, Any, Any]:
    """Build synthetic flippening models for a manual (operator-triggered) exit.

    Args:
        arb_id: Ticket identifier used as event/signal ID.
        position: Open position dict from FlipPositionRepo.

    Returns:
        Tuple of (FlippeningEvent, EntrySignal, ExitSignal).
    """
    from datetime import datetime, timezone
    from decimal import Decimal

    from arb_scanner.models.flippening import (
        EntrySignal,
        ExitReason,
        ExitSignal,
        FlippeningEvent,
        SpikeDirection,
    )

    entry_price = Decimal(str(position["entry_price"]))
    market_id = str(position["market_id"])
    now = datetime.now(timezone.utc)

    event = FlippeningEvent(
        id=arb_id,
        market_id=market_id,
        market_title=market_id,
        baseline_yes=entry_price,
        spike_price=entry_price,
        spike_magnitude_pct=Decimal("0"),
        spike_direction=SpikeDirection.FAVORITE_DROP,
        confidence=Decimal("1"),
        sport="",
        detected_at=now,
    )
    entry_sig = EntrySignal(
        event_id=arb_id,
        side=str(position["side"]),
        entry_price=entry_price,
        target_exit_price=entry_price,
        stop_loss_price=entry_price,
        suggested_size_usd=Decimal("0"),
        expected_profit_pct=Decimal("0"),
        max_hold_minutes=0,
        created_at=now,
    )
    exit_sig = ExitSignal(
        event_id=arb_id,
        side=str(position["side"]),
        exit_price=entry_price,
        exit_reason=ExitReason.RESOLUTION,
        realized_pnl=Decimal("0"),
        realized_pnl_pct=Decimal("0"),
        hold_minutes=Decimal("0"),
        created_at=now,
    )
    return event, entry_sig, exit_sig


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
