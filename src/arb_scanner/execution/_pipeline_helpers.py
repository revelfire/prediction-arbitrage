"""Shared helpers for arb and flip auto-execution pipelines."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.models.auto_execution import AutoExecLogEntry, CriticVerdict
from arb_scanner.models.config import Settings


@dataclass
class PipelineInfra:
    """Shared infrastructure refs passed to helper functions."""

    breakers: CircuitBreakerManager
    capital: Any
    auto_repo: Any
    config: Settings
    log: structlog.stdlib.BoundLogger
    rejection_cache: dict[str, float] = field(default_factory=dict)


@dataclass
class RunCtx:
    """Per-opportunity pipeline run context."""

    arb_id: str
    log_id: str
    start_ms: int
    spread: float
    confidence: float
    source: str


def is_geoblock(error_str: str) -> bool:
    """Return True if an error message indicates geographic restriction."""
    low = error_str.lower()
    return "restricted in your region" in low or "geoblock" in low or "GEOBLOCK:" in error_str


def purge_cooldowns(cache: dict[str, float], cooldown_s: float, now: float) -> None:
    """Remove expired entries from rejection cooldown cache."""
    expired = [k for k, v in cache.items() if (now - v) >= cooldown_s]
    for k in expired:
        del cache[k]


async def get_open_positions(auto_repo: Any) -> list[dict[str, Any]]:
    """Get open positions from repository, returning empty list on error."""
    try:
        result: list[dict[str, Any]] = await auto_repo.get_open_positions()
        return result
    except Exception:
        return []


def build_entry(
    run: RunCtx,
    status: str,
    *,
    size_usd: Decimal | None = None,
    verdict: CriticVerdict | None = None,
    execution_result_id: str | None = None,
    actual_spread: Decimal | None = None,
    slippage: Decimal | None = None,
    criteria_snapshot: dict[str, Any] | None = None,
) -> AutoExecLogEntry:
    """Build an AutoExecLogEntry from run context."""
    duration = int(time.time() * 1000) - run.start_ms
    return AutoExecLogEntry(
        id=run.log_id,
        arb_id=run.arb_id,
        trigger_spread_pct=Decimal(str(run.spread)),
        trigger_confidence=Decimal(str(run.confidence)),
        size_usd=size_usd or Decimal("0"),
        critic_verdict=verdict,
        execution_result_id=execution_result_id,
        actual_spread=actual_spread,
        slippage=slippage,
        criteria_snapshot=criteria_snapshot or {},
        duration_ms=duration,
        status=status,
        source=run.source,
    )


async def persist_and_notify(entry: AutoExecLogEntry, infra: PipelineInfra) -> None:
    """Persist log entry and dispatch webhook notification."""
    await _persist_log(entry, infra)
    await _dispatch_notification(entry, infra)


async def dispatch_geoblock(arb_id: str, infra: PipelineInfra) -> None:
    """Log geographic restriction (webhook silenced to reduce noise)."""
    infra.log.warning("geoblock_detected", arb_id=arb_id)


async def record_rejection(
    run: RunCtx,
    reasons: list[str],
    infra: PipelineInfra,
) -> AutoExecLogEntry:
    """Record a rejected opportunity with logging and notification."""
    infra.rejection_cache[run.arb_id] = time.monotonic()
    has_breaker = any(r.startswith("circuit_breaker_") for r in reasons)
    status = "breaker_blocked" if has_breaker else "rejected"
    entry = build_entry(run, status, criteria_snapshot={"rejection_reasons": reasons})
    await persist_and_notify(entry, infra)
    if has_breaker:
        await _dispatch_breaker(reasons, infra)
    return entry


async def record_critic_rejection(
    run: RunCtx,
    size: Decimal,
    verdict: CriticVerdict,
    infra: PipelineInfra,
) -> AutoExecLogEntry:
    """Record a critic-rejected opportunity with logging and notification."""
    infra.rejection_cache[run.arb_id] = time.monotonic()
    entry = build_entry(run, "critic_rejected", size_usd=size, verdict=verdict)
    await persist_and_notify(entry, infra)
    return entry


# -- Internal helpers --------------------------------------------------------


async def _persist_log(entry: AutoExecLogEntry, infra: PipelineInfra) -> None:
    """Persist a log entry to the repository."""
    try:
        breaker_state = [s.model_dump(mode="json") for s in infra.breakers.get_state()]
        await infra.auto_repo.insert_log(
            log_id=entry.id,
            arb_id=entry.arb_id,
            trigger_spread_pct=entry.trigger_spread_pct,
            trigger_confidence=entry.trigger_confidence,
            criteria_snapshot=entry.criteria_snapshot,
            pre_exec_balances={
                "poly": str(infra.capital.poly_balance),
                "kalshi": str(infra.capital.kalshi_balance),
            },
            size_usd=entry.size_usd,
            critic_verdict=(
                entry.critic_verdict.model_dump(mode="json") if entry.critic_verdict else None
            ),
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
        infra.log.exception("log_persist_failed")


async def _dispatch_notification(entry: AutoExecLogEntry, infra: PipelineInfra) -> None:
    """Dispatch webhook notification only for executed trades."""
    if entry.status != "executed":
        return
    try:
        from arb_scanner.notifications.auto_exec_webhook import dispatch_auto_exec_alert

        notif = infra.config.notifications
        await dispatch_auto_exec_alert(
            entry,
            slack_url=notif.effective_auto_exec_slack,
            discord_url=notif.discord_webhook,
        )
    except Exception:
        infra.log.exception("notification_failed")


async def _dispatch_breaker(reasons: list[str], infra: PipelineInfra) -> None:
    """Log circuit breaker trips (webhook silenced to reduce noise)."""
    for reason in reasons:
        if reason.startswith("circuit_breaker_"):
            infra.log.warning("breaker_tripped", reason=reason)
