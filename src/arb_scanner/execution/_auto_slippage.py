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
        import json

        legs: list[dict[str, Any]] = []
        for key in ("leg_1", "leg_2"):
            raw = ticket.get(key, {})
            leg = json.loads(raw) if isinstance(raw, str) else raw
            if leg and leg.get("action", "").lower().startswith("buy"):
                legs.append(leg)

        for leg in legs:
            venue = str(leg.get("venue", "")).lower()
            expected = Decimal(str(leg.get("price", 0)))
            if expected <= 0:
                continue

            if venue == "polymarket" and poly_executor.is_configured():
                token_id = str(leg.get("token_id", "")).strip()
                if token_id:
                    book = await poly_executor.get_book_depth(token_id)
                    if book:
                        best_ask = _best_price(book.get("asks", []))
                        if best_ask > 0:
                            poly_slip = max(poly_slip, abs(best_ask - expected) / expected)

            elif venue == "kalshi" and kalshi_executor.is_configured():
                ticker = str(leg.get("ticker", "")).strip()
                if ticker:
                    book = await kalshi_executor.get_book_depth(ticker)
                    if book:
                        side = _resolve_side(leg)
                        asks_key = "asks_yes" if side == "yes" else "asks_no"
                        best_ask = _best_price(book.get(asks_key, book.get("asks", [])))
                        if best_ask > 0:
                            kalshi_slip = max(kalshi_slip, abs(best_ask - expected) / expected)

    except Exception as exc:
        logger.warning("slippage_check_error_fail_closed", error=str(exc))
        return False, _ZERO, _ZERO

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


def _resolve_side(leg: dict[str, Any]) -> str:
    """Resolve YES/NO side from a ticket leg."""
    side = str(leg.get("side", "")).lower().strip()
    if side in ("yes", "no"):
        return side
    action = str(leg.get("action", "")).lower()
    if " no" in action or action.endswith("no"):
        return "no"
    return "yes"
