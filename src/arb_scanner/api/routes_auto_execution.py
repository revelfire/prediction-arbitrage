"""API routes for autonomous execution pipeline."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from arb_scanner.api.deps import get_auto_exec_repo, get_config
from arb_scanner.models.auto_execution import AutoExecMode
from arb_scanner.models.config import Settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="api.auto_execution",
)
router = APIRouter(prefix="/api/auto-execution", tags=["auto-execution"])


class ModeBody(BaseModel):
    """Request body for mode changes."""

    mode: AutoExecMode


class BreakerResetBody(BaseModel):
    """Request body for circuit breaker reset."""

    breaker_type: str


@router.get("/status")
async def auto_exec_status(
    request: Request,
    config: Settings = Depends(get_config),
) -> dict[str, Any]:
    """Return auto-execution status and configuration.

    Args:
        request: The incoming HTTP request.
        config: Application settings.

    Returns:
        Status dict.
    """
    pipeline = getattr(request.app.state, "auto_pipeline", None)
    ac = config.auto_execution

    result: dict[str, Any] = {
        "enabled": ac.enabled,
        "mode": pipeline.mode if pipeline else "off",
        "initialised": pipeline is not None,
        "config": {
            "min_spread_pct": ac.min_spread_pct,
            "max_spread_pct": ac.max_spread_pct,
            "min_confidence": ac.min_confidence,
            "max_size_usd": ac.max_size_usd,
            "base_size_usd": ac.base_size_usd,
            "daily_loss_limit_usd": ac.daily_loss_limit_usd,
            "max_daily_trades": ac.max_daily_trades,
            "max_slippage_pct": ac.max_slippage_pct,
        },
        "critic": {
            "enabled": ac.critic.enabled,
            "model": ac.critic.model,
        },
        "circuit_breakers": [],
    }

    breakers = getattr(request.app.state, "circuit_breakers", None)
    if breakers:
        result["circuit_breakers"] = [s.model_dump(mode="json") for s in breakers.get_state()]

    return result


@router.post("/enable")
async def enable_auto_exec(
    body: ModeBody,
    request: Request,
) -> dict[str, Any]:
    """Enable or change auto-execution mode.

    Args:
        body: Mode change request.
        request: The incoming HTTP request.

    Returns:
        Updated status.
    """
    pipeline = _require_pipeline(request)
    pipeline.set_mode(body.mode)
    logger.info("auto_exec_mode_set", mode=body.mode)
    return {"mode": pipeline.mode, "status": "ok"}


@router.post("/disable")
async def disable_auto_exec(
    request: Request,
) -> dict[str, Any]:
    """Kill switch -- immediately disable auto-execution.

    Args:
        request: The incoming HTTP request.

    Returns:
        Confirmation dict.
    """
    pipeline = _require_pipeline(request)
    pipeline.kill()
    logger.warning("auto_exec_killed")
    return {"mode": "off", "status": "killed"}


@router.get("/log")
async def auto_exec_log(
    limit: int = 50,
    repo: Any = Depends(get_auto_exec_repo),
) -> list[dict[str, Any]]:
    """Return recent auto-execution audit log.

    Args:
        limit: Maximum entries to return.
        repo: Auto-execution repository.

    Returns:
        List of log entry dicts.
    """
    try:
        result: list[dict[str, Any]] = await repo.list_log(limit=limit)
        return result
    except Exception as exc:
        logger.error("auto_exec_log_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/positions")
async def auto_exec_positions(
    repo: Any = Depends(get_auto_exec_repo),
) -> list[dict[str, Any]]:
    """Return currently open auto-execution positions.

    Args:
        repo: Auto-execution repository.

    Returns:
        List of position dicts.
    """
    try:
        result: list[dict[str, Any]] = await repo.get_open_positions()
        return result
    except Exception as exc:
        logger.error("auto_exec_positions_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/stats")
async def auto_exec_stats(
    days: int = 7,
    repo: Any = Depends(get_auto_exec_repo),
) -> dict[str, Any]:
    """Return auto-execution performance statistics.

    Args:
        days: Time window in days.
        repo: Auto-execution repository.

    Returns:
        Stats dict.
    """
    try:
        stats = await repo.get_daily_stats(days=days)
        return {k: str(v) if hasattr(v, "quantize") else v for k, v in stats.items()}
    except Exception as exc:
        logger.error("auto_exec_stats_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.post("/circuit-breaker/reset")
async def reset_circuit_breaker(
    body: BreakerResetBody,
    request: Request,
) -> dict[str, Any]:
    """Manually reset a circuit breaker.

    Args:
        body: Breaker type to reset.
        request: The incoming HTTP request.

    Returns:
        Updated breaker states.
    """
    breakers = getattr(request.app.state, "circuit_breakers", None)
    if breakers is None:
        raise HTTPException(503, "Auto-execution not initialised")

    bt = body.breaker_type
    if bt == "anomaly":
        breakers.reset_anomaly()
    elif bt == "all":
        breakers.reset_all()
    else:
        raise HTTPException(400, f"Unknown breaker type: {bt}")

    logger.info("circuit_breaker_manual_reset", breaker_type=bt)
    return {
        "status": "reset",
        "breaker_type": bt,
        "circuit_breakers": [s.model_dump(mode="json") for s in breakers.get_state()],
    }


def _require_pipeline(request: Request) -> Any:
    """Extract auto-execution pipeline from app state.

    Args:
        request: The incoming HTTP request.

    Returns:
        AutoExecutionPipeline instance.

    Raises:
        HTTPException: 503 when pipeline not initialised.
    """
    pipeline = getattr(request.app.state, "auto_pipeline", None)
    if pipeline is None:
        raise HTTPException(503, "Auto-execution pipeline not available")
    return pipeline
