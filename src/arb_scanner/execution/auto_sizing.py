"""Position sizing for auto-execution trades."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from arb_scanner.models._auto_exec_config import AutoExecutionConfig

_ZERO = Decimal("0")
_CENT = Decimal("0.01")


def compute_auto_size(
    spread_pct: float,
    min_spread_pct: float,
    config: AutoExecutionConfig,
    market_exposure: Decimal,
    available_balance: Decimal,
) -> Decimal | None:
    """Compute trade size for an auto-execution opportunity.

    Formula: size = min(base * (spread / min_spread), max_size)
    Subject to per-market and balance caps.

    Args:
        spread_pct: Current spread percentage.
        min_spread_pct: Minimum spread threshold.
        config: Auto-execution configuration.
        market_exposure: Current exposure in this market.
        available_balance: Available balance across venues.

    Returns:
        Trade size in USD, or None if below minimum.
    """
    if min_spread_pct <= 0 or spread_pct <= 0:
        return None

    base = Decimal(str(config.base_size_usd))
    ratio = Decimal(str(spread_pct)) / Decimal(str(min_spread_pct))
    raw_size = base * ratio

    max_size = Decimal(str(config.max_size_usd))
    size = min(raw_size, max_size)

    per_market_cap = Decimal(str(config.max_per_market_usd))
    remaining_market = per_market_cap - market_exposure
    if remaining_market <= _ZERO:
        return None
    size = min(size, remaining_market)

    balance_cap = available_balance * Decimal("0.5")
    size = min(size, balance_cap)

    size = size.quantize(_CENT, rounding=ROUND_DOWN)

    min_size = Decimal(str(config.min_size_usd))
    if size < min_size:
        return None

    return size
