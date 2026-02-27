"""Pre-execution slippage check for auto-execution pipeline."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.auto_slippage",
)

_ZERO = Decimal("0")


async def check_slippage(
    poly_executor: Any,
    kalshi_executor: Any,
    ticket: dict[str, Any],
    max_slippage_pct: float,
) -> tuple[bool, Decimal, Decimal]:
    """Re-fetch live prices and compare to detection-time prices.

    Args:
        poly_executor: Polymarket executor instance.
        kalshi_executor: Kalshi executor instance.
        ticket: Execution ticket with leg data.
        max_slippage_pct: Maximum allowed slippage percentage.

    Returns:
        Tuple of (passed, poly_slippage, kalshi_slippage).
    """
    max_slip = Decimal(str(max_slippage_pct))

    poly_slip = _ZERO
    kalshi_slip = _ZERO

    try:
        leg1 = ticket.get("leg_1", {})
        leg2 = ticket.get("leg_2", {})
        if isinstance(leg1, str):
            import json

            leg1 = json.loads(leg1)
        if isinstance(leg2, str):
            import json

            leg2 = json.loads(leg2)

        poly_leg = leg1 if leg1.get("venue") == "polymarket" else leg2
        kalshi_leg = leg1 if leg1.get("venue") == "kalshi" else leg2

        token_id = poly_leg.get("token_id", "")
        ticker = kalshi_leg.get("ticker", "")

        if token_id and poly_executor.is_configured():
            book = await poly_executor.get_book_depth(token_id)
            if book:
                best_ask = _best_price(book.get("asks", []))
                expected = Decimal(str(poly_leg.get("price", 0)))
                if expected > 0 and best_ask > 0:
                    poly_slip = abs(best_ask - expected) / expected

        if ticker and kalshi_executor.is_configured():
            book = await kalshi_executor.get_book_depth(ticker)
            if book:
                best_ask = _best_price(book.get("asks", []))
                expected = Decimal(str(kalshi_leg.get("price", 0)))
                if expected > 0 and best_ask > 0:
                    kalshi_slip = abs(best_ask - expected) / expected

    except Exception as exc:
        logger.warning("slippage_check_error", error=str(exc))
        return True, _ZERO, _ZERO

    passed = poly_slip <= max_slip and kalshi_slip <= max_slip
    if not passed:
        logger.warning(
            "slippage_exceeded",
            poly=float(poly_slip),
            kalshi=float(kalshi_slip),
            max=float(max_slip),
        )
    return passed, poly_slip, kalshi_slip


def _best_price(levels: list[dict[str, Any]]) -> Decimal:
    """Extract best price from order book levels.

    Args:
        levels: List of {price, size} dicts.

    Returns:
        Best (lowest ask) price, or zero.
    """
    if not levels:
        return _ZERO
    try:
        prices = [Decimal(str(lv.get("price", 0))) for lv in levels]
        return min(p for p in prices if p > 0) if prices else _ZERO
    except (ValueError, TypeError):
        return _ZERO
