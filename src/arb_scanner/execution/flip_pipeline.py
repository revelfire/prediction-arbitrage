"""Autonomous execution pipeline for flippening (mean-reversion) trades."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.execution._pipeline_helpers import (
    PipelineInfra,
    RunCtx,
    build_entry,
    dispatch_geoblock,
    dispatch_trade_notification,
    evaluate_capital_preservation,
    is_geoblock,
    persist_and_notify,
    purge_cooldowns,
    record_critic_rejection,
    record_rejection,
    sweep_expired_arb_positions,
)
from arb_scanner.execution.activity_feed import push_activity
from arb_scanner.execution.auto_sizing import compute_auto_size
from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.execution.exit_watchdog_metrics import ExitWatchdogMetrics
from arb_scanner.execution.flip_critic import FlipTradeCritic
from arb_scanner.execution.flip_evaluator import evaluate_flip_criteria
from arb_scanner.execution.flip_exit_executor import FlipExitExecutor
from arb_scanner.execution.flip_position_repo import FlipPositionRepo
from arb_scanner.models._auto_exec_config import AutoExecutionConfig
from arb_scanner.models.auto_execution import AutoExecMode
from arb_scanner.models.config import Settings
from arb_scanner.models.execution import OrderRequest, OrderSide
from arb_scanner.models.flippening import EntrySignal, ExitSignal, FlippeningEvent

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.flip_pipeline",
    pipeline="flip",
)

_NON_COUNTING_FAILURE_MARKERS = (
    "geoblock",
    "restricted in your region",
    "api key",
    "api secret",
    "api passphrase",
    "signature",
    "private key",
    "funder",
    "invalid token",
    "missing polymarket token_id",
)


def _push(event_type: str, arb_id: str, **fields: object) -> None:
    """Push activity event with pipeline tag, swallowing errors."""
    try:
        push_activity(event_type, arb_id, pipeline="flip", **fields)
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


def _adjust_size_for_risk(
    size: Decimal,
    *,
    confidence: float,
    config: AutoExecutionConfig,
    daily_pnl: Decimal,
) -> Decimal:
    """Scale size down for low-confidence and drawdown regimes."""
    min_conf = float(config.min_confidence)
    conf = max(0.0, min(confidence, 1.0))
    conf_headroom = max(1.0 - min_conf, 0.01)
    conf_rel = max(conf - min_conf, 0.0) / conf_headroom
    conf_mult = Decimal(str(0.70 + (0.30 * min(conf_rel, 1.0))))

    loss_limit = max(float(config.daily_loss_limit_usd), 1.0)
    if daily_pnl < Decimal("0"):
        drawdown = min(abs(float(daily_pnl)) / loss_limit, 1.0)
    else:
        drawdown = 0.0
    drawdown_mult = Decimal(str(1.0 - (0.40 * drawdown)))

    adjusted = (size * conf_mult * drawdown_mult).quantize(Decimal("0.01"))
    min_size = Decimal(str(config.min_size_usd))
    if adjusted < min_size:
        return min_size
    if adjusted > size:
        return size
    return adjusted


class FlipAutoExecutionPipeline:
    """Orchestrates autonomous flippening trade execution via PolymarketExecutor."""

    def __init__(
        self,
        config: Settings,
        auto_config: AutoExecutionConfig,
        critic: FlipTradeCritic,
        breakers: CircuitBreakerManager,
        capital: Any,
        poly: Any,
        position_repo: FlipPositionRepo,
        auto_repo: Any,
        exec_repo: Any,
        exit_executor: FlipExitExecutor | None = None,
    ) -> None:
        """Initialize the flip auto-execution pipeline."""
        self._ac = auto_config
        self._critic = critic
        self._poly = poly
        self._position_repo = position_repo
        self._exec_repo = exec_repo
        self._exit_executor = exit_executor
        self._mode: AutoExecMode = auto_config.mode  # type: ignore[assignment]
        self._locks: dict[str, asyncio.Lock] = {}
        self._killed = False
        self._cooldown_s: float = float(auto_config.cooldown_seconds)
        self._recent_entry_failures: deque[int] = deque(
            maxlen=max(int(auto_config.confidence_guardrail_window_attempts), 1)
        )
        self._exit_watchdog_metrics = ExitWatchdogMetrics()
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
        logger.info("flip_pipeline_mode_changed", mode=mode)

    def get_exit_watchdog_metrics(self) -> dict[str, int]:
        """Return current flip exit watchdog counters."""
        return self._exit_watchdog_metrics.snapshot()

    def set_min_confidence(self, value: float) -> float:
        """Update runtime min-confidence threshold for this pipeline."""
        bounded = max(0.0, min(float(value), 1.0))
        self._ac.min_confidence = bounded
        logger.info("flip_min_confidence_updated", min_confidence=bounded)
        return bounded

    def get_runtime_confidence_state(self) -> dict[str, object]:
        """Return live confidence threshold and guardrail state."""
        attempts = len(self._recent_entry_failures)
        failed = sum(self._recent_entry_failures)
        fail_rate = float(failed / attempts) if attempts > 0 else 0.0
        return {
            "min_confidence": float(self._ac.min_confidence),
            "guardrail_enabled": bool(self._ac.confidence_guardrail_enabled),
            "guardrail_window_attempts": int(self._ac.confidence_guardrail_window_attempts),
            "guardrail_fail_rate": float(self._ac.confidence_guardrail_fail_rate),
            "guardrail_raise_to": float(self._ac.confidence_guardrail_raise_to),
            "recent_attempts": attempts,
            "recent_failed": failed,
            "recent_fail_rate": fail_rate,
        }

    def kill(self) -> None:
        """Emergency kill switch."""
        self._mode = "off"
        self._killed = True
        logger.warning("flip_pipeline_killed")

    async def process_opportunity(
        self,
        opportunity: dict[str, Any],
        source: str = "flippening",
    ) -> Any | None:
        """Process a flippening opportunity through the pipeline."""
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
        """Run the full flip pipeline: criteria → sizing → critic → order."""
        title = opp.get("title", opp.get("market_title", ""))
        _push("considering", run.arb_id, title=title, spread=f"{run.spread:.1%}")

        await sweep_expired_arb_positions(self._infra.auto_repo)
        positions = await self._get_flip_positions()
        daily_count = await self._get_daily_trade_count()
        eligible, reasons = evaluate_flip_criteria(
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
        market_id = str(opp.get("market_id", ""))
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
        size = _adjust_size_for_risk(
            size,
            confidence=run.confidence,
            config=self._ac,
            daily_pnl=self._infra.capital.daily_pnl,
        )

        capital_reasons = await evaluate_capital_preservation(
            market_id=market_id,
            venue_spend={"polymarket": size},
            infra=self._infra,
        )
        if capital_reasons:
            _push("capital_blocked", run.arb_id, title=title, reasons=capital_reasons)
            return await record_rejection(run, capital_reasons, self._infra, title=title)

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

        _push("placing", run.arb_id, title=title, size_usd=float(size))
        return await self._place_order(opp, run, size, verdict)

    async def _place_order(
        self,
        opp: dict[str, Any],
        run: RunCtx,
        size: Decimal,
        verdict: Any,
    ) -> Any:
        """Place a single-leg Polymarket order for a flip trade."""
        title = str(opp.get("title", opp.get("market_title", "")))
        try:
            consume_probe = getattr(self._infra.breakers, "consume_failure_probe_attempt", None)
            if callable(consume_probe):
                consume_probe()
            token_id = str(opp.get("token_id", ""))
            if not token_id:
                logger.error("flip_order_no_token_id", arb_id=run.arb_id)
                entry = build_entry(
                    run,
                    "rejected",
                    size_usd=size,
                    verdict=verdict,
                    criteria_snapshot={"rejection_reasons": ["missing_token_id"]},
                    title=title,
                )
                await persist_and_notify(entry, self._infra)
                return entry
            entry_price = Decimal(str(opp.get("entry_price", 0.5)))
            side = str(opp.get("side", "yes")).lower()
            buy_side: OrderSide = f"buy_{side}"  # type: ignore[assignment]
            contracts = int(float(size) / float(entry_price)) if entry_price > 0 else 0
            req = OrderRequest(
                venue="polymarket",
                side=buy_side,
                price=entry_price.quantize(Decimal("0.0001")),
                size_usd=size,
                size_contracts=contracts,
                token_id=token_id,
            )
            resp = await self._poly.place_order(req)
            log_status, register = self._map_response(resp)
            if register:
                await self._register_position(run.arb_id, opp, size, resp=resp)
            if resp.error_message and is_geoblock(resp.error_message):
                await dispatch_geoblock(run.arb_id, self._infra)
            criteria_snapshot: dict[str, Any] | None = None
            if log_status == "failed":
                criteria_snapshot = {
                    "execution_error": str(resp.error_message or "unknown execution error"),
                    "venue_status": str(getattr(resp, "status", "")),
                }
            entry = build_entry(
                run,
                log_status,
                size_usd=size,
                verdict=verdict,
                criteria_snapshot=criteria_snapshot,
                title=title,
            )
        except Exception as exc:
            self._infra.breakers.record_failure()
            logger.error("flip_order_failed", arb_id=run.arb_id, error=str(exc))
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
        self._update_confidence_guardrail(entry.status, arb_id=run.arb_id)
        await persist_and_notify(entry, self._infra)
        if entry.status == "executed":
            await dispatch_trade_notification(
                action="buy",
                market_title=title,
                side=str(opp.get("side", "yes")),
                size_contracts=contracts,
                price=Decimal(str(opp.get("entry_price", 0.5))),
                arb_id=run.arb_id,
                infra=self._infra,
            )
        return entry

    def _map_response(self, resp: Any) -> tuple[str, bool]:
        """Map venue response to (log_status, should_register)."""
        status = str(getattr(resp, "status", "failed"))
        if status in ("filled", "submitted"):
            self._infra.breakers.record_success()
            return "executed", True
        if status == "partially_filled":
            # Partial fills are a successful entry with smaller size.
            self._infra.breakers.record_success()
            return "partial", True
        err = getattr(resp, "error_message", None)
        if self._is_counting_failure(err):
            self._infra.breakers.record_failure()
        else:
            logger.warning(
                "flip_order_failed_non_counting",
                error_message=str(err or ""),
            )
        return "failed", False

    @staticmethod
    def _is_counting_failure(error_message: object) -> bool:
        """Return whether a failed order should increment failure breaker."""
        if error_message is None:
            return True
        msg = str(error_message).lower()
        return not any(marker in msg for marker in _NON_COUNTING_FAILURE_MARKERS)

    async def process_exit(
        self,
        exit_sig: ExitSignal,
        entry_sig: EntrySignal,
        event: FlippeningEvent,
    ) -> None:
        """Place a sell order for an open flippening position."""
        if self._mode != "auto" or self._killed or self._exit_executor is None:
            return
        try:
            await self._exit_executor.execute_exit(exit_sig, entry_sig, event)
        except Exception as exc:
            self._infra.breakers.record_failure()
            logger.error("flip_exit_failed", market_id=event.market_id)
            if is_geoblock(str(exc)):
                await dispatch_geoblock(event.market_id, self._infra)

    async def _get_flip_positions(self) -> list[dict[str, Any]]:
        """Get open flip positions from the flippening position table."""
        try:
            return await self._position_repo.get_open_positions()
        except Exception:
            return []

    async def _get_daily_trade_count(self) -> int:
        """Query today's executed trade count from audit log."""
        try:
            count: int = await self._infra.auto_repo.get_daily_trade_count()
            return count
        except Exception:
            return 0

    async def _register_position(
        self,
        arb_id: str,
        opp: dict[str, Any],
        size: Decimal,
        *,
        resp: Any = None,
    ) -> None:
        """Record an open position after successful entry."""
        ep = float(opp.get("entry_price", 0.5))
        if resp is not None and resp.fill_price is not None:
            ep = float(resp.fill_price)
        raw_hold = opp.get("max_hold_minutes")
        contracts = int(float(size) / ep) if ep > 0 else 0
        if resp is not None and resp.status == "partially_filled":
            logger.warning(
                "flip_partial_fill",
                arb_id=arb_id,
                requested_contracts=contracts,
            )
        try:
            await self._position_repo.insert_position(
                arb_id=arb_id,
                market_id=str(opp.get("market_id", arb_id)),
                token_id=str(opp.get("token_id", "")),
                side=str(opp.get("side", "yes")),
                size_contracts=contracts,
                entry_price=Decimal(str(ep)),
                entry_order_id="",
                max_hold_minutes=int(raw_hold) if raw_hold is not None else None,
                market_title=str(opp.get("title", opp.get("market_title", ""))),
                market_slug=str(opp.get("market_slug", "")),
            )
        except Exception:
            logger.warning("flip_position_register_failed", arb_id=arb_id)

    def _update_confidence_guardrail(self, status: str, *, arb_id: str) -> None:
        """Raise min-confidence when recent failure rate breaches configured guardrail."""
        if status not in {"executed", "partial", "failed"}:
            return
        window = max(int(self._ac.confidence_guardrail_window_attempts), 1)
        if self._recent_entry_failures.maxlen != window:
            self._recent_entry_failures = deque(self._recent_entry_failures, maxlen=window)
        self._recent_entry_failures.append(1 if status == "failed" else 0)
        if not self._ac.confidence_guardrail_enabled:
            return
        if len(self._recent_entry_failures) < window:
            return
        fail_rate = sum(self._recent_entry_failures) / len(self._recent_entry_failures)
        threshold = float(self._ac.confidence_guardrail_fail_rate)
        raise_to = float(self._ac.confidence_guardrail_raise_to)
        if fail_rate < threshold or float(self._ac.min_confidence) >= raise_to:
            return
        old = float(self._ac.min_confidence)
        self._ac.min_confidence = raise_to
        logger.warning(
            "flip_confidence_guardrail_raised",
            old_min_confidence=old,
            new_min_confidence=raise_to,
            recent_fail_rate=fail_rate,
            window=window,
        )
        _push(
            "confidence_guardrail_raised",
            arb_id,
            old_min_confidence=old,
            new_min_confidence=raise_to,
            recent_fail_rate=round(fail_rate, 4),
            window=window,
        )

    def _build_market_context(
        self,
        opp: dict[str, Any],
        spread: float,
        confidence: float,
    ) -> dict[str, Any]:
        """Build flip-specific market context for the critic."""
        return {
            "spread_pct": spread,
            "confidence": confidence,
            "category": opp.get("category", ""),
            "title": opp.get("title", opp.get("market_title", "")),
            "entry_price": opp.get("entry_price", 0),
            "side": opp.get("side", "YES"),
            "baseline_deviation_pct": spread,
            "market_id": opp.get("market_id", ""),
            "price_age_seconds": opp.get("price_age_seconds", 0),
        }
