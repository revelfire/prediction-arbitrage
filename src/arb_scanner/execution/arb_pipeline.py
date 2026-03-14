"""Autonomous execution pipeline for arbitrage (cross-venue) trades."""

from __future__ import annotations

import asyncio
import time
import uuid
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.execution._auto_slippage import check_slippage
from arb_scanner.execution._pipeline_helpers import (
    PipelineInfra,
    RunCtx,
    build_entry,
    dispatch_geoblock,
    get_open_positions,
    is_geoblock,
    persist_and_notify,
    purge_cooldowns,
    record_critic_rejection,
    record_rejection,
)
from arb_scanner.execution.activity_feed import push_activity
from arb_scanner.execution.arb_critic import ArbTradeCritic
from arb_scanner.execution.arb_evaluator import evaluate_arb_criteria
from arb_scanner.execution.auto_sizing import compute_auto_size
from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.models._auto_exec_config import AutoExecutionConfig
from arb_scanner.models.auto_execution import AutoExecMode
from arb_scanner.models.config import Settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.arb_pipeline",
    pipeline="arb",
)


def _push(event_type: str, arb_id: str, **fields: object) -> None:
    """Push activity event with pipeline tag, swallowing errors."""
    try:
        push_activity(event_type, arb_id, pipeline="arb", **fields)
    except Exception:
        pass


def _compute_market_exposure(
    positions: list[dict[str, Any]],
    market_id: str,
) -> Decimal:
    """Compute current USD exposure for a specific market."""
    total = Decimal("0")
    for p in positions:
        if p.get("market_id") == market_id:
            ep = Decimal(str(p.get("entry_price", 0)))
            contracts = int(p.get("size_contracts", 0))
            total += ep * contracts
    return total


class ArbAutoExecutionPipeline:
    """Orchestrates autonomous arb trade execution via ExecutionOrchestrator."""

    def __init__(
        self,
        config: Settings,
        auto_config: AutoExecutionConfig,
        orchestrator: Any,
        critic: ArbTradeCritic,
        breakers: CircuitBreakerManager,
        capital: Any,
        poly: Any,
        kalshi: Any,
        auto_repo: Any,
    ) -> None:
        """Initialize the arb auto-execution pipeline."""
        self._ac = auto_config
        self._orchestrator = orchestrator
        self._critic = critic
        self._poly = poly
        self._kalshi = kalshi
        self._mode: AutoExecMode = auto_config.mode  # type: ignore[assignment]
        self._locks: dict[str, asyncio.Lock] = {}
        self._killed = False
        self._cooldown_s: float = float(auto_config.cooldown_seconds)
        self._infra = PipelineInfra(
            breakers=breakers,
            capital=capital,
            auto_repo=auto_repo,
            config=config,
            log=logger,
        )

    @property
    def mode(self) -> AutoExecMode:
        """Current pipeline mode."""
        return self._mode

    def set_mode(self, mode: AutoExecMode) -> None:
        """Set pipeline operation mode."""
        self._mode = mode
        self._killed = mode == "off"
        logger.info("arb_pipeline_mode_changed", mode=mode)

    def set_min_confidence(self, value: float) -> float:
        """Update runtime min-confidence threshold for this pipeline."""
        bounded = max(0.0, min(float(value), 1.0))
        self._ac.min_confidence = bounded
        logger.info("arb_min_confidence_updated", min_confidence=bounded)
        return bounded

    def kill(self) -> None:
        """Emergency kill switch."""
        self._mode = "off"
        self._killed = True
        logger.warning("arb_pipeline_killed")

    async def process_opportunity(
        self,
        opportunity: dict[str, Any],
        source: str = "arb_watch",
    ) -> Any | None:
        """Process an arb opportunity through the pipeline."""
        if self._mode != "auto" or self._killed:
            return None
        arb_id = opportunity.get("arb_id", str(uuid.uuid4()))
        now = time.monotonic()
        purge_cooldowns(self._infra.rejection_cache, self._cooldown_s, now)
        cached = self._infra.rejection_cache.get(arb_id)
        if cached is not None and (now - cached) < self._cooldown_s:
            return None
        spread = float(opportunity.get("spread_pct", opportunity.get("net_spread_pct", 0)))
        run = RunCtx(
            arb_id=arb_id,
            log_id=str(uuid.uuid4()),
            start_ms=int(time.time() * 1000),
            spread=spread,
            confidence=float(opportunity.get("confidence", 0)),
            source=source,
        )
        lock = self._locks.setdefault(arb_id, asyncio.Lock())
        if lock.locked():
            return None
        async with lock:
            return await self._execute(opportunity, run)

    async def _execute(self, opp: dict[str, Any], run: RunCtx) -> Any | None:
        """Run the full arb pipeline: criteria → sizing → critic → slippage → order."""
        title = opp.get("title", opp.get("market_title", ""))
        _push("considering", run.arb_id, title=title, spread=f"{run.spread:.1%}")

        positions = await get_open_positions(self._infra.auto_repo)
        daily_count = await self._get_daily_trade_count()
        eligible, reasons = evaluate_arb_criteria(
            opp,
            self._ac,
            positions,
            self._infra.capital.daily_pnl,
            self._infra.breakers,
            daily_trade_count=daily_count,
        )
        if not eligible:
            _push("criteria_failed", run.arb_id, title=title, reasons=reasons)
            return await record_rejection(run, reasons, self._infra, title=title)

        _push("criteria_passed", run.arb_id, title=title)
        await self._infra.capital.refresh_balances()
        market_id = str(opp.get("arb_id", ""))
        market_exposure = _compute_market_exposure(positions, market_id)
        size = compute_auto_size(
            run.spread,
            self._ac.min_spread_pct,
            self._ac,
            market_exposure,
            self._infra.capital.total_balance,
        )
        if size is None:
            _push("size_rejected", run.arb_id, title=title)
            return await record_rejection(
                run,
                ["size_below_minimum"],
                self._infra,
                title=title,
            )

        ctx = self._build_market_context(opp, run.spread, run.confidence)
        verdict = await self._critic.evaluate({"arb_id": run.arb_id}, ctx)
        if not verdict.approved:
            _push("critic_rejected", run.arb_id, title=title, reasoning=verdict.reasoning)
            return await record_critic_rejection(
                run,
                size,
                verdict,
                self._infra,
                title=title,
            )

        slip_ok, poly_s, kalshi_s = await check_slippage(
            self._poly,
            self._kalshi,
            opp,
            self._ac.max_slippage_pct,
        )
        if not slip_ok:
            _push("slippage_failed", run.arb_id, title=title)
            reason = f"slippage_exceeded: poly={float(poly_s):.4f} kalshi={float(kalshi_s):.4f}"
            return await record_rejection(run, [reason], self._infra, title=title)

        _push("placing", run.arb_id, title=title, size_usd=float(size))
        return await self._place_order(opp, run, size, verdict)

    async def _place_order(
        self,
        opp: dict[str, Any],
        run: RunCtx,
        size: Decimal,
        verdict: Any,
    ) -> Any:
        """Execute two-leg arb order through the orchestrator."""
        title = str(opp.get("title", opp.get("market_title", "")))
        try:
            consume_probe = getattr(self._infra.breakers, "consume_failure_probe_attempt", None)
            if callable(consume_probe):
                consume_probe()
            result = await self._orchestrator.execute(run.arb_id, size)
            status_map = {"complete": "executed", "partial": "partial", "failed": "failed"}
            log_status = status_map.get(result.status, "failed")
            if log_status == "executed":
                self._infra.breakers.record_success()
            else:
                self._infra.breakers.record_failure()
            if result.error_message == "GEOBLOCK":
                await dispatch_geoblock(run.arb_id, self._infra)
            criteria_snapshot: dict[str, Any] | None = None
            if result.error_message:
                criteria_snapshot = {"execution_error": str(result.error_message)}
                if log_status != "executed":
                    criteria_snapshot["execution_status"] = str(result.status)
            entry = build_entry(
                run,
                log_status,
                size_usd=size,
                verdict=verdict,
                execution_result_id=result.id,
                actual_spread=result.actual_spread,
                slippage=result.slippage_from_ticket,
                criteria_snapshot=criteria_snapshot,
                title=title,
            )
        except Exception as exc:
            self._infra.breakers.record_failure()
            logger.error("arb_order_failed", arb_id=run.arb_id, error=str(exc))
            if is_geoblock(str(exc)):
                await dispatch_geoblock(run.arb_id, self._infra)
            entry = build_entry(
                run,
                "failed",
                size_usd=size,
                verdict=verdict,
                criteria_snapshot={"execution_error": str(exc)},
                title=title,
            )

        _push(f"placed_{entry.status}", run.arb_id, title=opp.get("title", ""))
        await persist_and_notify(entry, self._infra)
        return entry

    async def _get_daily_trade_count(self) -> int:
        """Query today's executed trade count from audit log."""
        try:
            count: int = await self._infra.auto_repo.get_daily_trade_count()
            return count
        except Exception:
            return 0

    def _build_market_context(
        self,
        opp: dict[str, Any],
        spread: float,
        confidence: float,
    ) -> dict[str, Any]:
        """Build arb-specific market context for the critic."""
        return {
            "spread_pct": spread,
            "confidence": confidence,
            "category": opp.get("category", ""),
            "title": opp.get("title", opp.get("market_title", "")),
            "poly_yes_price": opp.get("poly_yes_price", 0),
            "kalshi_yes_price": opp.get("kalshi_yes_price", 0),
            "poly_depth": opp.get("poly_depth", 0),
            "kalshi_depth": opp.get("kalshi_depth", 0),
            "price_age_seconds": opp.get("price_age_seconds", 0),
        }
