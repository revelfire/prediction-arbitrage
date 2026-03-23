"""Shared math helpers for flip position accounting."""

from __future__ import annotations

from decimal import Decimal


def compute_realized_pnl(
    entry_price: Decimal,
    exit_price: Decimal,
    size_contracts: int,
) -> Decimal:
    """Compute realized P&L for a closed position.

    Args:
        entry_price: Price paid per contract at entry.
        exit_price: Price received per contract at exit.
        size_contracts: Number of contracts.

    Returns:
        Total realized P&L (positive = profit).
    """
    return (exit_price - entry_price) * Decimal(size_contracts)
