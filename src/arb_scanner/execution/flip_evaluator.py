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

    if breakers.is_any_tripped():
        tripped = [s for s in breakers.get_state() if s.tripped]
        for s in tripped:
            reasons.append(f"circuit_breaker_{s.breaker_type.value}: {s.reason}")

    confidence = float(opportunity.get("confidence", 0))
    if confidence < config.min_confidence:
        reasons.append(f"confidence {confidence:.2f} < min {config.min_confidence}")

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
    if len(open_positions) >= max_pos:
        reasons.append(f"open_positions {len(open_positions)} >= max {max_pos}")

    arb_id = opportunity.get("arb_id", "")
    if arb_id and any(p.get("arb_id") == arb_id for p in open_positions):
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
