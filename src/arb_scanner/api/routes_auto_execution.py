"""API routes for autonomous execution pipeline."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from arb_scanner.api.deps import get_auto_exec_repo, get_config
from arb_scanner.execution.activity_feed import get_history, push_activity, subscribe, unsubscribe
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
    ac = config.auto_execution
    arb_pipeline = getattr(request.app.state, "arb_pipeline", None)
    mode = arb_pipeline.mode if arb_pipeline else "off"

    result: dict[str, Any] = {
        "enabled": ac.enabled,
        "mode": mode,
        "initialised": arb_pipeline is not None,
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
        "arb_breakers": [],
        "flip_breakers": [],
    }

    # Per-pipeline breaker state
    arb_breakers = getattr(request.app.state, "arb_breakers", None)
    flip_breakers = getattr(request.app.state, "flip_breakers", None)
    if arb_breakers:
        result["arb_breakers"] = [s.model_dump(mode="json") for s in arb_breakers.get_state()]
    if flip_breakers:
        result["flip_breakers"] = [s.model_dump(mode="json") for s in flip_breakers.get_state()]

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
    flip = getattr(request.app.state, "flip_pipeline", None)
    if flip is not None:
        flip.set_mode(body.mode)
    push_activity("mode_changed", "system", pipeline="system", mode=body.mode)
    if body.mode == "auto" and flip is not None:
        db = getattr(request.app.state, "db", None)
        if db is not None:
            asyncio.ensure_future(_refeed_active_signals(flip, db.pool))
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
    flip = getattr(request.app.state, "flip_pipeline", None)
    if flip is not None:
        flip.kill()
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
    request: Request,
    repo: Any = Depends(get_auto_exec_repo),
) -> list[dict[str, Any]]:
    """Return all open positions (arb + flippening).

    Args:
        request: The incoming HTTP request.
        repo: Auto-execution repository.

    Returns:
        List of position dicts with string-coerced values.
    """
    positions: list[dict[str, Any]] = []
    try:
        arb_rows: list[dict[str, Any]] = await repo.get_open_positions()
        for row in arb_rows:
            row["pipeline_type"] = "arb"
        positions.extend(arb_rows)
    except Exception as exc:
        logger.error("auto_exec_positions_failed", error=str(exc))

    flip_repo = getattr(request.app.state, "flip_position_repo", None)
    if flip_repo is not None:
        try:
            flip_rows = await flip_repo.get_orphaned_positions()
            for row in flip_rows:
                d = {k: (str(v) if v is not None else None) for k, v in dict(row).items()}
                d["pipeline_type"] = "flip"
                positions.append(d)
        except Exception as exc:
            logger.error("flip_positions_failed", error=str(exc))

    if not positions:
        return []
    return positions


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


@router.get("/activity")
async def activity_history(limit: int = 60) -> list[dict[str, Any]]:
    """Return recent pipeline activity events (newest last).

    Args:
        limit: Max events to return.

    Returns:
        List of activity event dicts.
    """
    return get_history(limit=limit)


@router.get("/activity-stream")
async def activity_stream() -> StreamingResponse:
    """SSE endpoint streaming live auto-execution pipeline activity.

    Returns:
        StreamingResponse with text/event-stream content type.
    """
    return StreamingResponse(
        _stream_activity(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_activity() -> AsyncGenerator[str, None]:
    """Generate SSE events from the shared activity feed.

    Yields:
        Formatted SSE message strings.
    """
    q = subscribe()
    try:
        # Send history snapshot first
        history = get_history(limit=30)
        yield f"event: history\ndata: {json.dumps(history)}\n\n"
        # Then stream live events
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=20.0)
                yield f"event: activity\ndata: {json.dumps(event)}\n\n"
            except TimeoutError:
                yield "event: heartbeat\ndata: {}\n\n"
    finally:
        unsubscribe(q)


async def _refeed_active_signals(flip: Any, pool: Any) -> None:
    """Re-feed active flip signals missed while mode was off.

    Args:
        flip: FlipAutoExecutionPipeline instance.
        pool: asyncpg connection pool.
    """
    from arb_scanner.storage._flippening_queries import GET_REFEEDABLE_SIGNALS

    try:
        rows = await pool.fetch(GET_REFEEDABLE_SIGNALS, 20)
        if not rows:
            push_activity("refeed_empty", "system", pipeline="flip")
            return
        push_activity("refeed_start", "system", pipeline="flip", count=len(rows))
        fed = 0
        for row in rows:
            opp: dict[str, object] = {
                "arb_id": str(row["event_id"]),
                "spread_pct": float(row["spike_magnitude"]),
                "confidence": float(row["confidence"]),
                "category": row["category"] or "",
                "title": row["market_title"] or "",
                "ticket_type": "flippening",
                "market_id": row["market_id"],
                "token_id": row["token_id"] or "",
                "side": row["side"],
                "entry_price": float(row["entry_price"]),
                "market_slug": "",
            }
            result = await flip.process_opportunity(opp, source="refeed")
            if result is not None:
                fed += 1
        push_activity("refeed_done", "system", pipeline="flip", fed=fed)
    except Exception:
        logger.warning("refeed_active_signals_failed")


def _require_pipeline(request: Request) -> Any:
    """Extract auto-execution pipeline from app state.

    Args:
        request: The incoming HTTP request.

    Returns:
        Pipeline instance (arb by default).

    Raises:
        HTTPException: 503 when pipeline not initialised.
    """
    pipeline = getattr(request.app.state, "arb_pipeline", None)
    if pipeline is None:
        raise HTTPException(503, "Auto-execution pipeline not available")
    return pipeline
