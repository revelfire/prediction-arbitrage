"""Order book liquidity validation for pre-execution checks."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from arb_scanner.execution.base import contracts_from_usd, estimate_vwap
from arb_scanner.models.config import ExecutionConfig
from arb_scanner.models.execution import LiquidityResult

_ZERO = Decimal("0")
_ONE = Decimal("1")


def validate_liquidity(
    poly_book: dict[str, Any],
    kalshi_book: dict[str, Any],
    size_usd: Decimal,
    price_poly: Decimal,
    price_kalshi: Decimal,
    config: ExecutionConfig,
    *,
    check_poly: bool = True,
    check_kalshi: bool = True,
) -> LiquidityResult:
    """Validate order book depth can absorb the requested trade size.

    Walks both order books to compute VWAP and estimate slippage.

    Args:
        poly_book: Polymarket order book with bids/asks.
        kalshi_book: Kalshi order book with bids/asks.
        size_usd: Requested trade size in USD.
        price_poly: Top-of-book price for the Polymarket leg.
        price_kalshi: Top-of-book price for the Kalshi leg.
        config: Execution configuration.

    Returns:
        LiquidityResult with slippage estimates and pass/fail.
    """
    warnings: list[str] = []
    max_slip = Decimal(str(config.max_slippage_pct))
    min_depth = config.min_book_depth_contracts

    poly_contracts = contracts_from_usd(size_usd, price_poly) if check_poly else 0
    kalshi_contracts = contracts_from_usd(size_usd, price_kalshi) if check_kalshi else 0

    poly_asks = _extract_levels(poly_book, "asks")
    kalshi_asks = _extract_levels(kalshi_book, "asks")

    poly_vwap, poly_depth = estimate_vwap(poly_asks, poly_contracts)
    kalshi_vwap, kalshi_depth = estimate_vwap(kalshi_asks, kalshi_contracts)

    # Probe book depth independently from trade size so we don't reject
    # thin-trade-size tickets against markets with plenty of liquidity.
    _, poly_book_depth = estimate_vwap(poly_asks, min_depth) if min_depth > 0 else (_ZERO, 0)
    _, kalshi_book_depth = estimate_vwap(kalshi_asks, min_depth) if min_depth > 0 else (_ZERO, 0)

    poly_slippage = _ZERO
    if poly_vwap > _ZERO and price_poly > _ZERO:
        poly_slippage = (poly_vwap - price_poly) / price_poly

    kalshi_slippage = _ZERO
    if kalshi_vwap > _ZERO and price_kalshi > _ZERO:
        kalshi_slippage = (kalshi_vwap - price_kalshi) / price_kalshi

    passed = True

    if check_poly and poly_slippage > max_slip:
        warnings.append(f"Polymarket slippage {poly_slippage:.4f} exceeds max {max_slip:.4f}")
        passed = False
    if check_kalshi and kalshi_slippage > max_slip:
        warnings.append(f"Kalshi slippage {kalshi_slippage:.4f} exceeds max {max_slip:.4f}")
        passed = False
    if check_poly and poly_book_depth < min_depth:
        warnings.append(f"Polymarket depth {poly_book_depth} contracts < min {min_depth}")
        passed = False
    if check_kalshi and kalshi_book_depth < min_depth:
        warnings.append(f"Kalshi depth {kalshi_book_depth} contracts < min {min_depth}")
        passed = False

    if check_poly and check_kalshi:
        max_absorbable = _compute_max_absorbable(
            poly_asks, kalshi_asks, price_poly, price_kalshi, max_slip
        )
    elif check_poly:
        max_absorbable = _compute_single_venue_absorbable(poly_asks, price_poly, max_slip)
    elif check_kalshi:
        max_absorbable = _compute_single_venue_absorbable(kalshi_asks, price_kalshi, max_slip)
    else:
        max_absorbable = _ZERO

    return LiquidityResult(
        poly_vwap=poly_vwap,
        kalshi_vwap=kalshi_vwap,
        poly_slippage=poly_slippage,
        kalshi_slippage=kalshi_slippage,
        poly_depth_contracts=poly_book_depth,
        kalshi_depth_contracts=kalshi_book_depth,
        max_absorbable_usd=max_absorbable,
        passed=passed,
        warnings=warnings,
    )


def _extract_levels(book: dict[str, Any], side: str) -> list[dict[str, Any]]:
    """Extract price levels from an order book side.

    Args:
        book: Raw order book dict.
        side: "bids" or "asks".

    Returns:
        List of price level dicts.
    """
    levels = book.get(side, [])
    if not isinstance(levels, list):
        return []
    return levels


def _compute_max_absorbable(
    poly_levels: list[dict[str, Any]],
    kalshi_levels: list[dict[str, Any]],
    price_poly: Decimal,
    price_kalshi: Decimal,
    max_slip: Decimal,
) -> Decimal:
    """Find the max USD size both books can absorb within slippage.

    Binary search for the largest size where both legs stay within
    the slippage tolerance.

    Args:
        poly_levels: Polymarket ask levels.
        kalshi_levels: Kalshi ask levels.
        price_poly: Top-of-book Polymarket price.
        price_kalshi: Top-of-book Kalshi price.
        max_slip: Maximum acceptable slippage fraction.

    Returns:
        Maximum absorbable USD.
    """
    if not poly_levels or not kalshi_levels:
        return _ZERO
    if price_poly <= _ZERO or price_kalshi <= _ZERO:
        return _ZERO

    lo, hi = Decimal("1"), Decimal("10000")
    best = _ZERO
    for _ in range(20):
        mid = (lo + hi) / 2
        pc = contracts_from_usd(mid, price_poly)
        kc = contracts_from_usd(mid, price_kalshi)
        pv, pd = estimate_vwap(poly_levels, pc)
        kv, kd = estimate_vwap(kalshi_levels, kc)
        p_slip = (pv - price_poly) / price_poly if pv > _ZERO else _ZERO
        k_slip = (kv - price_kalshi) / price_kalshi if kv > _ZERO else _ZERO
        if p_slip <= max_slip and k_slip <= max_slip and pd >= pc and kd >= kc:
            best = mid
            lo = mid
        else:
            hi = mid
    return best.quantize(Decimal("0.01"))


def _compute_single_venue_absorbable(
    levels: list[dict[str, Any]],
    price: Decimal,
    max_slip: Decimal,
) -> Decimal:
    """Find max USD size one book can absorb within slippage tolerance."""
    if not levels or price <= _ZERO:
        return _ZERO

    lo, hi = Decimal("1"), Decimal("10000")
    best = _ZERO
    for _ in range(20):
        mid = (lo + hi) / 2
        contracts = contracts_from_usd(mid, price)
        vwap, depth = estimate_vwap(levels, contracts)
        slip = (vwap - price) / price if vwap > _ZERO else _ZERO
        if slip <= max_slip and depth >= contracts:
            best = mid
            lo = mid
        else:
            hi = mid
    return best.quantize(Decimal("0.01"))
