"""Portfolio-level metrics from reconstructed trade positions."""

from __future__ import annotations

from decimal import Decimal

from arb_scanner.models.backtesting import (
    PortfolioSummary,
    TradePosition,
)

_ZERO = Decimal("0")


def calculate_portfolio(
    positions: list[TradePosition],
    fee_rate: Decimal = Decimal("0.02"),
) -> PortfolioSummary:
    """Calculate aggregate portfolio metrics with fee adjustment.

    Fees are charged only on winning positions (realized_pnl > 0)
    at ``fee_rate`` of the realized P&L, matching the Polymarket
    fee-on-winnings model.

    Args:
        positions: Reconstructed trade positions.
        fee_rate: Fee percentage applied to winning P&L (default 2%).

    Returns:
        Aggregated portfolio summary.
    """
    if not positions:
        return _empty_summary()

    total_realized = _ZERO
    total_unrealized = _ZERO
    total_fees = _ZERO
    total_capital = _ZERO
    win_count = 0
    loss_count = 0

    adjusted: list[TradePosition] = []
    for pos in positions:
        adj_pnl, fee = _apply_fees(pos.realized_pnl, fee_rate)
        total_realized += adj_pnl
        total_unrealized += pos.unrealized_pnl
        total_fees += fee
        total_capital += pos.cost_basis

        if pos.status != "open":
            if adj_pnl > 0:
                win_count += 1
            else:
                loss_count += 1

        adjusted.append(pos.model_copy(update={"fee_paid": fee}))

    closed = win_count + loss_count
    net_pnl = total_realized + total_unrealized - total_fees
    win_rate = win_count / closed if closed else 0.0
    roi = float(net_pnl / total_capital) if total_capital else 0.0
    trade_count = len(positions)
    avg_size = total_capital / trade_count if trade_count else _ZERO

    return PortfolioSummary(
        total_realized_pnl=total_realized,
        total_unrealized_pnl=total_unrealized,
        total_fees=total_fees,
        net_pnl=net_pnl,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=round(win_rate, 4),
        total_capital_deployed=total_capital,
        roi=round(roi, 4),
        trade_count=trade_count,
        avg_trade_size=avg_size,
        positions=adjusted,
    )


def _apply_fees(realized_pnl: Decimal, fee_rate: Decimal) -> tuple[Decimal, Decimal]:
    """Compute fee and adjusted P&L for a single position.

    Args:
        realized_pnl: Raw realized P&L before fees.
        fee_rate: Fee percentage on winnings.

    Returns:
        Tuple of (realized_pnl unchanged, fee_amount).
        Fee is zero for losing positions.
    """
    if realized_pnl > 0:
        fee = realized_pnl * fee_rate
        return realized_pnl, fee
    return realized_pnl, _ZERO


def _empty_summary() -> PortfolioSummary:
    """Return a zero-valued portfolio summary.

    Returns:
        PortfolioSummary with all metrics at zero.
    """
    return PortfolioSummary(
        total_realized_pnl=_ZERO,
        total_unrealized_pnl=_ZERO,
        total_fees=_ZERO,
        net_pnl=_ZERO,
        win_count=0,
        loss_count=0,
        win_rate=0.0,
        total_capital_deployed=_ZERO,
        roi=0.0,
        trade_count=0,
        avg_trade_size=_ZERO,
        positions=[],
    )
