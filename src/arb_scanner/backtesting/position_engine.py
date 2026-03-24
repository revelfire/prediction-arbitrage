"""FIFO cost-basis position reconstruction from imported trades."""

from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal

from arb_scanner.models.backtesting import (
    ImportedTrade,
    PositionStatus,
    TradeAction,
    TradePosition,
)

_ZERO = Decimal("0")


def reconstruct_positions(
    trades: list[ImportedTrade],
) -> list[TradePosition]:
    """Reconstruct positions from imported trades using FIFO cost basis.

    Filters out Deposit/Withdraw rows, groups by (market_name, token_name),
    and applies FIFO lot matching for cost basis and realized P&L.

    Args:
        trades: Validated imported trades (may include deposits/withdrawals).

    Returns:
        One TradePosition per (market_name, token_name) group.
    """
    market_trades = trades_only(trades)
    groups: dict[tuple[str, str], list[ImportedTrade]] = defaultdict(list)
    for t in market_trades:
        groups[(t.market_name, t.token_name)].append(t)

    positions: list[TradePosition] = []
    for _key, group in sorted(groups.items()):
        group.sort(key=lambda t: t.timestamp)
        positions.append(_process_group(group))
    return positions


def trades_only(trades: list[ImportedTrade]) -> list[ImportedTrade]:
    """Filter to Buy/Sell trades, excluding Deposit/Withdraw.

    Args:
        trades: All imported trades.

    Returns:
        Trades with action Buy or Sell only.
    """
    return [t for t in trades if t.action in (TradeAction.Buy, TradeAction.Sell)]


def _process_group(group: list[ImportedTrade]) -> TradePosition:
    """Apply FIFO lot matching to a single (market, token) group.

    Args:
        group: Trades for one market/token pair, sorted by timestamp.

    Returns:
        Reconstructed TradePosition with cost basis and realized P&L.
    """
    lots: deque[tuple[Decimal, Decimal]] = deque()
    cost_basis = _ZERO
    realized_pnl = _ZERO

    for trade in group:
        if trade.action == TradeAction.Buy:
            price = trade.usdc_amount / trade.token_amount
            lots.append((price, trade.token_amount))
            cost_basis += trade.usdc_amount
        elif trade.action == TradeAction.Sell:
            sell_price = trade.usdc_amount / trade.token_amount
            remaining = trade.token_amount
            realized_pnl += _consume_lots(lots, sell_price, remaining)

    tokens_held: Decimal = sum((qty for _, qty in lots), _ZERO)
    remaining_cost = sum(p * q for p, q in lots)
    avg_entry = remaining_cost / tokens_held if tokens_held else _ZERO
    status = PositionStatus.open if tokens_held > 0 else PositionStatus.closed

    return TradePosition(
        market_name=group[0].market_name,
        token_name=group[0].token_name,
        cost_basis=cost_basis,
        tokens_held=tokens_held,
        avg_entry_price=avg_entry,
        realized_pnl=realized_pnl,
        unrealized_pnl=_ZERO,
        status=status,
        fee_paid=_ZERO,
        first_trade_at=group[0].timestamp,
        last_trade_at=group[-1].timestamp,
    )


def _consume_lots(
    lots: deque[tuple[Decimal, Decimal]],
    sell_price: Decimal,
    remaining: Decimal,
) -> Decimal:
    """Consume FIFO lots for a sell, returning realized P&L.

    Args:
        lots: FIFO queue of (buy_price, quantity) tuples.
        sell_price: Price per token on the sell.
        remaining: Number of tokens to sell.

    Returns:
        Realized P&L from consumed lots.
    """
    pnl = _ZERO
    while remaining > 0:
        if not lots:
            pnl += sell_price * remaining
            break
        buy_price, lot_qty = lots[0]
        consumed = min(lot_qty, remaining)
        pnl += (sell_price - buy_price) * consumed
        remaining -= consumed
        if consumed >= lot_qty:
            lots.popleft()
        else:
            lots[0] = (buy_price, lot_qty - consumed)
    return pnl
