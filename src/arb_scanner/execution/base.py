"""Venue executor protocol and shared utilities."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from arb_scanner.models.execution import OrderRequest, OrderResponse

_ONE = Decimal("1")
_ZERO = Decimal("0")


@runtime_checkable
class VenueExecutor(Protocol):
    """Protocol for venue-specific order execution."""

    async def place_order(self, req: OrderRequest) -> OrderResponse:
        """Place a limit order on the venue.

        Args:
            req: Order parameters.

        Returns:
            Order response with venue order ID and status.
        """
        ...

    async def cancel_order(self, venue_order_id: str) -> bool:
        """Cancel a pending order.

        Args:
            venue_order_id: The venue's order identifier.

        Returns:
            True if cancelled successfully.
        """
        ...

    async def get_balance(self) -> Decimal:
        """Fetch the account's available trading balance.

        Returns:
            Available balance in USD (or USDC).
        """
        ...

    async def get_book_depth(self, token_or_ticker: str) -> dict[str, Any]:
        """Fetch the full order book for a market.

        Args:
            token_or_ticker: CLOB token ID (Polymarket) or ticker (Kalshi).

        Returns:
            Raw order book dict with bids/asks arrays.
        """
        ...

    def is_configured(self) -> bool:
        """Return True if venue credentials are set.

        Returns:
            Whether the executor has valid credentials.
        """
        ...


def estimate_vwap(
    levels: list[dict[str, Any]],
    size_contracts: int,
) -> tuple[Decimal, int]:
    """Walk an order book side to compute volume-weighted average price.

    Args:
        levels: List of price-level dicts with 'price' and 'size' keys.
        size_contracts: Number of contracts to fill.

    Returns:
        Tuple of (vwap, contracts_available). If book is empty or cannot
        fill any contracts, returns (Decimal("0"), 0).
    """
    if not levels or size_contracts <= 0:
        return _ZERO, 0
    filled = 0
    cost = _ZERO
    for level in levels:
        price = Decimal(str(level.get("price", "0")))
        available = int(float(level.get("size", 0)))
        if available <= 0 or price <= _ZERO:
            continue
        take = min(available, size_contracts - filled)
        cost += price * Decimal(take)
        filled += take
        if filled >= size_contracts:
            break
    if filled == 0:
        return _ZERO, 0
    return cost / Decimal(filled), filled


def contracts_from_usd(size_usd: Decimal, price: Decimal) -> int:
    """Convert a USD position size to contract count.

    Args:
        size_usd: Position size in USD.
        price: Price per contract.

    Returns:
        Number of contracts (floored to integer).
    """
    if price <= _ZERO:
        return 0
    return int(size_usd / price)
