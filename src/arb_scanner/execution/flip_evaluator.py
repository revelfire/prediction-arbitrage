"""Criteria evaluator for flippening auto-execution eligibility."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.models._auto_exec_config import AutoExecutionConfig

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.flip_evaluator",
    pipeline="flip",
)


def evaluate_flip_criteria(
    opportunity: dict[str, Any],
    config: AutoExecutionConfig,
    open_positions: list[dict[str, Any]],
    daily_pnl: Decimal,
    breakers: CircuitBreakerManager,
    daily_trade_count: int = 0,
) -> tuple[bool, list[str]]:
    """Check all flippening auto-execution eligibility criteria.

    Does NOT check spread bounds — large deviation IS the signal for
    flippening mean-reversion trades.

    Args:
        opportunity: The flippening opportunity dict.
        config: Auto-execution configuration.
        open_positions: Currently open auto-exec positions.
        daily_pnl: Today's cumulative P&L.
        breakers: Circuit breaker manager.
        daily_trade_count: Number of executed trades today (UTC).

    Returns:
        Tuple of (eligible, rejection_reasons).
    """
    reasons: list[str] = []

    reasons.extend(breakers.get_blocking_reasons(allow_failure_probe=True))

    confidence = float(opportunity.get("confidence", 0))
    spread = float(opportunity.get("spread_pct", opportunity.get("net_spread_pct", 0)))
    eff_min_conf, conf_ctx = _effective_min_confidence(
        base_min_conf=config.min_confidence,
        spread=spread,
        min_spread=config.min_spread_pct,
        daily_pnl=daily_pnl,
        daily_loss_limit=config.daily_loss_limit_usd,
        open_positions=len(open_positions),
        max_open_positions=config.max_open_positions,
    )
    if confidence < eff_min_conf:
        reasons.append(
            "confidence "
            f"{confidence:.2f} < min {eff_min_conf:.2f} "
            f"(base={conf_ctx['base_min_conf']:.2f}, "
            f"spread_bonus={conf_ctx['spread_bonus']:.2f}, "
            f"drawdown_penalty={conf_ctx['drawdown_penalty']:.2f}, "
            f"load_penalty={conf_ctx['load_penalty']:.2f})"
        )

    category = opportunity.get("category", "")
    if config.allowed_categories and category not in config.allowed_categories:
        reasons.append(f"category '{category}' not in allowed list")
    if config.blocked_categories and category in config.blocked_categories:
        reasons.append(f"category '{category}' is blocked")

    loss_limit = Decimal(str(config.daily_loss_limit_usd))
    if daily_pnl < -loss_limit:
        reasons.append(
            f"daily_pnl ${float(daily_pnl):.2f} exceeds loss limit ${float(loss_limit):.2f}"
        )

    max_pos = config.max_open_positions
    active_positions = [p for p in open_positions if p.get("status") == "open"]
    if len(active_positions) >= max_pos:
        reasons.append(f"open_positions {len(active_positions)} >= max {max_pos}")

    arb_id = opportunity.get("arb_id", "")
    if arb_id and any(
        p.get("arb_id") == arb_id and p.get("status") == "open" for p in open_positions
    ):
        reasons.append(f"duplicate position for {arb_id}")

    ticket_type = str(opportunity.get("ticket_type", ""))
    if (
        config.allowed_ticket_types
        and ticket_type
        and ticket_type not in config.allowed_ticket_types
    ):
        reasons.append(f"ticket_type '{ticket_type}' not in allowed list")

    if config.max_daily_trades > 0 and daily_trade_count >= config.max_daily_trades:
        reasons.append(f"daily_trades {daily_trade_count} >= max {config.max_daily_trades}")

    eligible = len(reasons) == 0
    if not eligible:
        logger.info("flip_criteria_failed", reasons=reasons)
    return eligible, reasons


def _effective_min_confidence(
    *,
    base_min_conf: float,
    spread: float,
    min_spread: float,
    daily_pnl: Decimal,
    daily_loss_limit: float,
    open_positions: int,
    max_open_positions: int,
) -> tuple[float, dict[str, float]]:
    """Compute adaptive min-confidence threshold for entry gating.

    Low-risk, high-deviation setups get a modest confidence discount so the
    engine can execute more often. Drawdown and position-load apply penalties
    to protect capital during stressed periods.
    """
    spread_delta = max(spread - min_spread, 0.0)
    spread_span = max(0.40 - min_spread, 0.05)
    spread_score = min(spread_delta / spread_span, 1.0)
    spread_bonus = 0.10 * spread_score

    loss_limit = max(float(daily_loss_limit), 1.0)
    drawdown_ratio = (
        min(float(abs(daily_pnl)) / loss_limit, 1.0) if daily_pnl < Decimal("0") else 0.0
    )
    drawdown_penalty = 0.15 * drawdown_ratio

    if max_open_positions > 0:
        load_ratio = min(open_positions / max_open_positions, 1.0)
    else:
        load_ratio = 0.0
    load_penalty = 0.05 * load_ratio

    raw = base_min_conf - spread_bonus + drawdown_penalty + load_penalty
    floor = max(0.50, base_min_conf - 0.15)
    ceiling = min(0.95, base_min_conf + 0.20)
    effective = max(floor, min(raw, ceiling))
    return effective, {
        "base_min_conf": base_min_conf,
        "spread_bonus": spread_bonus,
        "drawdown_penalty": drawdown_penalty,
        "load_penalty": load_penalty,
    }
