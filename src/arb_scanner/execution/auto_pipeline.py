"""Autonomous execution pipeline orchestrator."""

from __future__ import annotations

import asyncio
import time
import uuid
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.execution._auto_slippage import check_slippage
from arb_scanner.execution.auto_evaluator import evaluate_criteria
from arb_scanner.execution.auto_sizing import compute_auto_size
from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.execution.trade_critic import TradeCritic
from arb_scanner.models._auto_exec_config import AutoExecutionConfig
from arb_scanner.models.auto_execution import (
    AutoExecLogEntry,
    AutoExecMode,
    CriticVerdict,
)
from arb_scanner.models.config import Settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.auto_pipeline",
)

_ZERO = Decimal("0")


class AutoExecutionPipeline:
    """Orchestrates autonomous trade execution with safety gates.

    Args:
        config: Full application settings.
        auto_config: Auto-execution configuration.
        orchestrator: Execution orchestrator for preflight/execute.
        critic: AI trade critic.
        breakers: Circuit breaker manager.
        auto_repo: Auto-execution repository.
        capital: Capital manager.
        poly: Polymarket executor.
        kalshi: Kalshi executor.
    """

    def __init__(
        self,
        config: Settings,
        auto_config: AutoExecutionConfig,
        orchestrator: Any,
        critic: TradeCritic,
        breakers: CircuitBreakerManager,
        auto_repo: Any,
        capital: Any,
        poly: Any,
        kalshi: Any,
    ) -> None:
        """Initialize the auto-execution pipeline.

        Args:
            config: Full application settings.
            auto_config: Auto-execution configuration.
            orchestrator: Execution orchestrator.
            critic: AI trade critic.
            breakers: Circuit breaker manager.
            auto_repo: Auto-execution repository.
            capital: Capital manager.
            poly: Polymarket executor.
            kalshi: Kalshi executor.
        """
        self._config = config
        self._auto_config = auto_config
        self._orchestrator = orchestrator
        self._critic = critic
        self._breakers = breakers
        self._auto_repo = auto_repo
        self._capital = capital
        self._poly = poly
        self._kalshi = kalshi
        self._mode: AutoExecMode = auto_config.mode  # type: ignore[assignment]
        self._locks: dict[str, asyncio.Lock] = {}
        self._killed = False

    @property
    def mode(self) -> AutoExecMode:
        """Current pipeline mode."""
        return self._mode

    def set_mode(self, mode: AutoExecMode) -> None:
        """Set the pipeline mode.

        Args:
            mode: New mode (off, manual, auto).
        """
        self._mode = mode
        self._killed = mode == "off"
        logger.info("auto_pipeline_mode_changed", mode=mode)

    def kill(self) -> None:
        """Emergency kill switch -- immediately disable auto-execution."""
        self._mode = "off"
        self._killed = True
        logger.warning("auto_pipeline_killed")

    async def process_opportunity(
        self,
        opportunity: dict[str, Any],
        source: str = "unknown",
    ) -> AutoExecLogEntry | None:
        """Process an opportunity through the auto-execution pipeline.

        Args:
            opportunity: Opportunity data dict.
            source: Trigger source (arb_watch, flippening).

        Returns:
            Log entry if processed, None if skipped.
        """
        if self._mode != "auto" or self._killed:
            return None

        arb_id = opportunity.get("arb_id", str(uuid.uuid4()))
        log_id = str(uuid.uuid4())
        start_ms = int(time.time() * 1000)

        spread = float(opportunity.get("spread_pct", opportunity.get("net_spread_pct", 0)))
        confidence = float(opportunity.get("confidence", 0))

        lock = self._locks.setdefault(arb_id, asyncio.Lock())
        if lock.locked():
            logger.debug("auto_exec_skip_locked", arb_id=arb_id)
            return None

        async with lock:
            return await self._execute_pipeline(
                opportunity, arb_id, log_id, start_ms, spread, confidence, source
            )

    async def _execute_pipeline(
        self,
        opportunity: dict[str, Any],
        arb_id: str,
        log_id: str,
        start_ms: int,
        spread: float,
        confidence: float,
        source: str,
    ) -> AutoExecLogEntry | None:
        """Run the full pipeline steps.

        Args:
            opportunity: Opportunity data.
            arb_id: Ticket/opportunity ID.
            log_id: Unique log entry ID.
            start_ms: Start timestamp in ms.
            spread: Spread percentage.
            confidence: Confidence score.
            source: Trigger source.

        Returns:
            Completed log entry.
        """
        open_positions = await self._get_open_positions()
        daily_pnl = self._capital.daily_pnl

        eligible, reasons = evaluate_criteria(
            opportunity, self._auto_config, open_positions, daily_pnl, self._breakers
        )
        if not eligible:
            return await self._record_rejection(
                log_id, arb_id, spread, confidence, reasons, source, start_ms
            )

        market_exposure = self._get_market_exposure(arb_id)
        available = self._capital.total_balance
        size = compute_auto_size(
            spread,
            self._auto_config.min_spread_pct,
            self._auto_config,
            market_exposure,
            available,
        )
        if size is None:
            return await self._record_rejection(
                log_id,
                arb_id,
                spread,
                confidence,
                ["size_below_minimum"],
                source,
                start_ms,
            )

        context = self._build_market_context(opportunity, spread, confidence)
        ticket = {"arb_id": arb_id, "ticket_type": opportunity.get("ticket_type", "arbitrage")}
        verdict = await self._critic.evaluate(ticket, {}, context)

        if not verdict.approved:
            return await self._record_critic_rejection(
                log_id, arb_id, spread, confidence, size, verdict, source, start_ms
            )

        slip_ok, poly_slip, kalshi_slip = await check_slippage(
            self._poly, self._kalshi, opportunity, self._auto_config.max_slippage_pct
        )
        if not slip_ok:
            return await self._record_rejection(
                log_id,
                arb_id,
                spread,
                confidence,
                [f"slippage_exceeded: poly={float(poly_slip):.4f} kalshi={float(kalshi_slip):.4f}"],
                source,
                start_ms,
            )

        try:
            result = await self._orchestrator.execute(arb_id, size)
            self._breakers.record_success()
            duration = int(time.time() * 1000) - start_ms

            entry = AutoExecLogEntry(
                id=log_id,
                arb_id=arb_id,
                trigger_spread_pct=Decimal(str(spread)),
                trigger_confidence=Decimal(str(confidence)),
                size_usd=size,
                critic_verdict=verdict,
                execution_result_id=result.id,
                actual_spread=result.actual_spread,
                slippage=result.slippage_from_ticket,
                duration_ms=duration,
                status="executed",
                source=source,
            )
            await self._persist_log(entry)
            await self._dispatch_notification(entry)
            return entry

        except Exception as exc:
            self._breakers.record_failure()
            duration = int(time.time() * 1000) - start_ms
            logger.error("auto_exec_failed", arb_id=arb_id, error=str(exc))

            entry = AutoExecLogEntry(
                id=log_id,
                arb_id=arb_id,
                trigger_spread_pct=Decimal(str(spread)),
                trigger_confidence=Decimal(str(confidence)),
                size_usd=size,
                critic_verdict=verdict,
                status="failed",
                duration_ms=duration,
                source=source,
            )
            await self._persist_log(entry)
            await self._dispatch_notification(entry)
            return entry

    async def _get_open_positions(self) -> list[dict[str, Any]]:
        """Get open positions from repository.

        Returns:
            List of open position dicts.
        """
        try:
            result: list[dict[str, Any]] = await self._auto_repo.get_open_positions()
            return result
        except Exception:
            return []

    def _get_market_exposure(self, arb_id: str) -> Decimal:
        """Get current exposure for a specific market.

        Args:
            arb_id: Ticket ID.

        Returns:
            Current USD exposure.
        """
        exposure: Decimal = self._capital.current_exposure
        return exposure

    def _build_market_context(
        self,
        opportunity: dict[str, Any],
        spread: float,
        confidence: float,
    ) -> dict[str, Any]:
        """Build market context dict for the AI critic.

        Args:
            opportunity: Opportunity data.
            spread: Spread percentage.
            confidence: Confidence score.

        Returns:
            Context dict for critic evaluation.
        """
        return {
            "spread_pct": spread,
            "confidence": confidence,
            "category": opportunity.get("category", ""),
            "title": opportunity.get("title", opportunity.get("market_title", "")),
            "poly_yes_price": opportunity.get("poly_yes_price", 0),
            "kalshi_yes_price": opportunity.get("kalshi_yes_price", 0),
            "poly_depth": opportunity.get("poly_depth", 0),
            "kalshi_depth": opportunity.get("kalshi_depth", 0),
            "price_age_seconds": opportunity.get("price_age_seconds", 0),
            "ticket_type": opportunity.get("ticket_type", "arbitrage"),
        }

    async def _record_rejection(
        self,
        log_id: str,
        arb_id: str,
        spread: float,
        confidence: float,
        reasons: list[str],
        source: str,
        start_ms: int,
    ) -> AutoExecLogEntry:
        """Record a rejected opportunity.

        Args:
            log_id: Log entry ID.
            arb_id: Ticket/opportunity ID.
            spread: Spread pct.
            confidence: Confidence.
            reasons: Rejection reasons.
            source: Trigger source.
            start_ms: Start timestamp.

        Returns:
            Log entry.
        """
        duration = int(time.time() * 1000) - start_ms
        has_breaker = any(r.startswith("circuit_breaker_") for r in reasons)
        status = "breaker_blocked" if has_breaker else "rejected"
        entry = AutoExecLogEntry(
            id=log_id,
            arb_id=arb_id,
            trigger_spread_pct=Decimal(str(spread)),
            trigger_confidence=Decimal(str(confidence)),
            criteria_snapshot={"rejection_reasons": reasons},
            status=status,
            duration_ms=duration,
            source=source,
        )
        await self._persist_log(entry)
        await self._dispatch_notification(entry)
        if has_breaker:
            await self._dispatch_breaker_notification(reasons)
        return entry

    async def _dispatch_breaker_notification(self, reasons: list[str]) -> None:
        """Dispatch a circuit breaker trip notification.

        Args:
            reasons: Rejection reasons containing breaker info.
        """
        try:
            from arb_scanner.notifications.auto_exec_webhook import (
                dispatch_breaker_alert,
            )

            notif = self._config.notifications
            for reason in reasons:
                if reason.startswith("circuit_breaker_"):
                    parts = reason.split(": ", 1)
                    breaker_type = parts[0].removeprefix("circuit_breaker_")
                    detail = parts[1] if len(parts) > 1 else reason
                    await dispatch_breaker_alert(
                        breaker_type,
                        detail,
                        slack_url=notif.effective_auto_exec_slack,
                        discord_url=notif.discord_webhook,
                    )
        except Exception:
            logger.exception("breaker_notification_failed")

    async def _record_critic_rejection(
        self,
        log_id: str,
        arb_id: str,
        spread: float,
        confidence: float,
        size: Decimal,
        verdict: CriticVerdict,
        source: str,
        start_ms: int,
    ) -> AutoExecLogEntry:
        """Record a critic-rejected opportunity.

        Args:
            log_id: Log entry ID.
            arb_id: Ticket/opportunity ID.
            spread: Spread pct.
            confidence: Confidence.
            size: Computed size.
            verdict: Critic verdict.
            source: Trigger source.
            start_ms: Start timestamp.

        Returns:
            Log entry.
        """
        duration = int(time.time() * 1000) - start_ms
        entry = AutoExecLogEntry(
            id=log_id,
            arb_id=arb_id,
            trigger_spread_pct=Decimal(str(spread)),
            trigger_confidence=Decimal(str(confidence)),
            size_usd=size,
            critic_verdict=verdict,
            status="critic_rejected",
            duration_ms=duration,
            source=source,
        )
        await self._persist_log(entry)
        await self._dispatch_notification(entry)
        return entry

    async def _persist_log(self, entry: AutoExecLogEntry) -> None:
        """Persist a log entry to the repository.

        Args:
            entry: Log entry to persist.
        """
        try:
            breaker_state = [s.model_dump(mode="json") for s in self._breakers.get_state()]
            await self._auto_repo.insert_log(
                log_id=entry.id,
                arb_id=entry.arb_id,
                trigger_spread_pct=entry.trigger_spread_pct,
                trigger_confidence=entry.trigger_confidence,
                criteria_snapshot=entry.criteria_snapshot,
                pre_exec_balances={
                    "poly": str(self._capital.poly_balance),
                    "kalshi": str(self._capital.kalshi_balance),
                },
                size_usd=entry.size_usd,
                critic_verdict=entry.critic_verdict.model_dump(mode="json")
                if entry.critic_verdict
                else None,
                execution_result_id=entry.execution_result_id,
                actual_spread=entry.actual_spread,
                actual_pnl=entry.actual_pnl,
                slippage=entry.slippage,
                duration_ms=entry.duration_ms,
                circuit_breaker_state=breaker_state,
                status=entry.status,
                source=entry.source,
            )
        except Exception:
            logger.exception("auto_exec_log_persist_failed")

    async def _dispatch_notification(self, entry: AutoExecLogEntry) -> None:
        """Dispatch webhook notification for an auto-exec event.

        Args:
            entry: Log entry (any status: executed, rejected, failed, etc.).
        """
        try:
            from arb_scanner.notifications.auto_exec_webhook import (
                dispatch_auto_exec_alert,
            )

            notif = self._config.notifications
            await dispatch_auto_exec_alert(
                entry,
                slack_url=notif.effective_auto_exec_slack,
                discord_url=notif.discord_webhook,
            )
        except Exception:
            logger.exception("auto_exec_notification_failed")
