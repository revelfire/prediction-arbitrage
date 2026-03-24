"""Shared helpers for arb and flip auto-execution pipelines."""

from __future__ import annotations

from datetime import UTC, datetime
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


async def get_risk_positions(auto_repo: Any) -> list[dict[str, Any]]:
    """Get cross-pipeline open positions for exposure checks."""
    getter = getattr(auto_repo, "get_risk_positions", None)
    if getter is None:
        return await get_open_positions(auto_repo)
    try:
        result: list[dict[str, Any]] = await getter()
        return result
    except Exception:
        return []


async def sweep_expired_arb_positions(auto_repo: Any) -> None:
    """Best-effort cleanup of expired arb placeholder positions."""
    sweeper = getattr(auto_repo, "abandon_expired", None)
    if sweeper is None:
        return
    try:
        await sweeper()
    except Exception:
        return


async def evaluate_capital_preservation(
    *,
    market_id: str,
    venue_spend: dict[str, Decimal],
    infra: PipelineInfra,
) -> list[str]:
    """Evaluate repo-backed capital preservation limits for auto execution."""
    cfg = infra.config.execution
    reasons: list[str] = []

    reserve = Decimal(str(cfg.min_reserve_usd))
    poly_spend = venue_spend.get("polymarket", Decimal("0"))
    kalshi_spend = venue_spend.get("kalshi", Decimal("0"))
    if poly_spend > 0:
        poly_after = infra.capital.poly_balance - poly_spend
        if poly_after < reserve:
            reasons.append(
                f"capital_reserve_polymarket: ${poly_after:.2f} below ${reserve:.2f}"
            )
    if kalshi_spend > 0:
        kalshi_after = infra.capital.kalshi_balance - kalshi_spend
        if kalshi_after < reserve:
            reasons.append(f"capital_reserve_kalshi: ${kalshi_after:.2f} below ${reserve:.2f}")

    total_balance = infra.capital.total_balance
    if total_balance <= Decimal("0"):
        reasons.append("capital_total_balance_unavailable")
        return reasons

    positions = await get_risk_positions(infra.auto_repo)
    current_exposure = sum((_position_exposure(p) for p in positions), Decimal("0"))
    proposed_exposure = sum(venue_spend.values(), Decimal("0"))
    exposure_cap = total_balance * Decimal(str(cfg.max_exposure_pct))
    if (current_exposure + proposed_exposure) > exposure_cap:
        reasons.append(
            f"capital_exposure_limit: ${(current_exposure + proposed_exposure):.2f} "
            f"over ${exposure_cap:.2f}"
        )

    open_count = len(positions)
    if open_count >= int(cfg.max_open_positions):
        reasons.append(
            f"capital_open_positions_limit: {open_count}/{int(cfg.max_open_positions)} open"
        )

    concentration_cap = total_balance * Decimal(str(cfg.max_per_market_pct))
    market_exposure = sum(
        (_position_exposure(p) for p in positions if _position_market_key(p) == market_id),
        Decimal("0"),
    )
    if (market_exposure + proposed_exposure) > concentration_cap:
        reasons.append(
            f"capital_market_concentration: ${(market_exposure + proposed_exposure):.2f} "
            f"over ${concentration_cap:.2f}"
        )

    pnl = await _get_today_realized_pnl(infra.auto_repo, fallback=infra.capital.daily_pnl)
    daily_limit = Decimal(str(cfg.daily_loss_limit_usd))
    if pnl <= -daily_limit:
        reasons.append(f"capital_daily_loss_limit: ${pnl:.2f} <= -${daily_limit:.2f}")

    cooldown_remaining = await _get_cooldown_remaining(infra.auto_repo, cfg.cooldown_after_loss_seconds)
    if cooldown_remaining > 0:
        reasons.append(f"capital_loss_cooldown: {cooldown_remaining}s remaining")

    return reasons


def build_entry(
    run: RunCtx,
    status: str,
    *,
    size_usd: Decimal | None = None,
    verdict: CriticVerdict | None = None,
    execution_result_id: str | None = None,
    actual_spread: Decimal | None = None,
    actual_pnl: Decimal | None = None,
    slippage: Decimal | None = None,
    criteria_snapshot: dict[str, Any] | None = None,
    title: str = "",
) -> AutoExecLogEntry:
    """Build an AutoExecLogEntry from run context."""
    duration = int(time.time() * 1000) - run.start_ms
    snap = criteria_snapshot or {}
    if title:
        snap["title"] = title
    return AutoExecLogEntry(
        id=run.log_id,
        arb_id=run.arb_id,
        trigger_spread_pct=Decimal(str(run.spread)),
        trigger_confidence=Decimal(str(run.confidence)),
        size_usd=size_usd or Decimal("0"),
        critic_verdict=verdict,
        execution_result_id=execution_result_id,
        actual_spread=actual_spread,
        actual_pnl=actual_pnl,
        slippage=slippage,
        criteria_snapshot=snap,
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
    *,
    title: str = "",
) -> AutoExecLogEntry:
    """Record a rejected opportunity with logging and notification."""
    infra.rejection_cache[run.arb_id] = time.monotonic()
    has_breaker = any(r.startswith("circuit_breaker_") for r in reasons)
    status = "breaker_blocked" if has_breaker else "rejected"
    entry = build_entry(
        run,
        status,
        criteria_snapshot={"rejection_reasons": reasons},
        title=title,
    )
    await persist_and_notify(entry, infra)
    if has_breaker:
        await _dispatch_breaker(reasons, infra)
    return entry


async def record_critic_rejection(
    run: RunCtx,
    size: Decimal,
    verdict: CriticVerdict,
    infra: PipelineInfra,
    *,
    title: str = "",
) -> AutoExecLogEntry:
    """Record a critic-rejected opportunity with logging and notification."""
    infra.rejection_cache[run.arb_id] = time.monotonic()
    entry = build_entry(
        run,
        "critic_rejected",
        size_usd=size,
        verdict=verdict,
        title=title,
    )
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


async def dispatch_trade_notification(
    *,
    action: str,
    market_title: str,
    side: str,
    size_contracts: int,
    price: Decimal,
    arb_id: str,
    pnl: Decimal | None = None,
    infra: PipelineInfra,
) -> None:
    """Dispatch a BUY/SELL/CLOSED trade notification to Slack.

    Args:
        action: 'buy', 'sell', or 'closed'.
        market_title: Human-readable market name.
        side: 'yes' or 'no'.
        size_contracts: Number of contracts.
        price: Execution price.
        arb_id: Execution ticket ID.
        pnl: Realized P&L (for closed trades).
        infra: Pipeline infrastructure with config.
    """
    try:
        from arb_scanner.notifications.trade_webhook import dispatch_trade_alert

        notif = infra.config.notifications
        await dispatch_trade_alert(
            action=action,
            market_title=market_title,
            side=side,
            size_contracts=size_contracts,
            price=price,
            arb_id=arb_id,
            pnl=pnl,
            slack_url=notif.effective_auto_exec_slack,
            dashboard_url=notif.dashboard_url,
            auth_token=infra.config.dashboard.auth_token or "",
        )
    except Exception:
        infra.log.exception("trade_notification_failed", action=action)


async def _dispatch_breaker(reasons: list[str], infra: PipelineInfra) -> None:
    """Log circuit breaker trips (webhook silenced to reduce noise)."""
    for reason in reasons:
        if reason.startswith("circuit_breaker_"):
            infra.log.warning("breaker_tripped", reason=reason)


def _position_market_key(position: dict[str, Any]) -> str:
    """Return the normalized market key for a stored open position."""
    for key in ("market_id", "arb_id", "poly_market_id", "kalshi_ticker"):
        value = position.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _position_exposure(position: dict[str, Any]) -> Decimal:
    """Return the stored USD exposure for a position row."""
    entry_cost = position.get("entry_cost_usd")
    if entry_cost not in (None, ""):
        return Decimal(str(entry_cost))
    entry_price = Decimal(str(position.get("entry_price", 0) or 0))
    size_contracts = Decimal(str(position.get("size_contracts", 0) or 0))
    return entry_price * size_contracts


async def _get_today_realized_pnl(auto_repo: Any, *, fallback: Decimal) -> Decimal:
    """Fetch today's realized P&L with a capital-manager fallback."""
    getter = getattr(auto_repo, "get_today_realized_pnl", None)
    if getter is None:
        return fallback
    try:
        return await getter()
    except Exception:
        return fallback


async def _get_cooldown_remaining(auto_repo: Any, cooldown_seconds: int) -> int:
    """Fetch remaining loss cooldown based on the latest realized loss."""
    getter = getattr(auto_repo, "get_latest_realized_loss", None)
    if getter is None:
        return 0
    try:
        loss = await getter()
    except Exception:
        return 0
    if not loss:
        return 0
    created_at = loss.get("created_at")
    if not isinstance(created_at, datetime):
        return 0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    elapsed = (datetime.now(tz=UTC) - created_at).total_seconds()
    if elapsed >= cooldown_seconds:
        return 0
    return max(int(cooldown_seconds - elapsed), 0)
