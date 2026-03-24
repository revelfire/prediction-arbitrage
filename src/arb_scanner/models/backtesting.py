"""Data models for trade history import and backtesting analysis."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, field_validator


class TradeAction(str, Enum):
    """Action type for an imported trade."""

    Buy = "Buy"
    Sell = "Sell"
    Deposit = "Deposit"
    Withdraw = "Withdraw"
    Redeem = "Redeem"


class PositionStatus(str, Enum):
    """Lifecycle status of a trade position."""

    open = "open"
    closed = "closed"
    resolved = "resolved"


class SignalAlignment(str, Enum):
    """Whether a trade aligned with a flippening signal."""

    aligned = "aligned"
    contrary = "contrary"
    no_signal = "no_signal"


class ImportedTrade(BaseModel):
    """A single trade row parsed from a Polymarket CSV export."""

    market_name: str
    action: TradeAction
    usdc_amount: Decimal
    token_amount: Decimal
    token_name: str
    timestamp: datetime
    tx_hash: str
    condition_id: str | None = None
    imported_at: datetime | None = None

    @field_validator("tx_hash")
    @classmethod
    def tx_hash_not_empty(cls, v: str) -> str:
        """Validate that tx_hash is non-empty."""
        if not v.strip():
            raise ValueError("tx_hash must not be empty")
        return v

    @field_validator("usdc_amount", "token_amount")
    @classmethod
    def amounts_non_negative(cls, v: Decimal) -> Decimal:
        """Validate that financial amounts are non-negative."""
        if v < 0:
            raise ValueError("amounts must be non-negative")
        return v


class ImportResult(BaseModel):
    """Summary of a CSV import operation."""

    inserted: int
    duplicates: int
    errors: int


class TradePosition(BaseModel):
    """Aggregated position for a market/token pair."""

    market_name: str
    token_name: str
    cost_basis: Decimal
    tokens_held: Decimal
    avg_entry_price: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    status: PositionStatus
    fee_paid: Decimal
    first_trade_at: datetime
    last_trade_at: datetime


class PortfolioSummary(BaseModel):
    """Aggregate portfolio metrics across all positions."""

    total_realized_pnl: Decimal
    total_unrealized_pnl: Decimal
    total_fees: Decimal
    net_pnl: Decimal
    win_count: int
    loss_count: int
    win_rate: float
    total_capital_deployed: Decimal
    roi: float
    trade_count: int
    avg_trade_size: Decimal
    positions: list[TradePosition]


class CategoryPerformance(BaseModel):
    """Performance metrics for a market category."""

    category: str
    win_rate: float
    avg_pnl: float
    trade_count: int
    total_pnl: float
    profit_factor: float
    avg_hold_minutes: float
    signal_alignment_rate: float
    aligned_win_rate: float
    contrary_win_rate: float
    computed_at: datetime


class OptimalParamSnapshot(BaseModel):
    """Snapshot of an optimal parameter value from a sweep."""

    category: str
    param_name: str
    optimal_value: float
    win_rate_at_optimal: float
    sweep_date: datetime
