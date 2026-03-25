"""Data models for one-click trade execution."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, computed_field

OrderSide = Literal["buy_yes", "buy_no", "sell_yes", "sell_no"]
OrderStatus = Literal[
    "submitting", "submitted", "filled", "partially_filled", "failed", "cancelled"
]
ResultStatus = Literal["pending", "complete", "partial", "failed"]


class ExecutionOrder(BaseModel):
    """A single-leg order placed on a venue."""

    id: str
    arb_id: str
    venue: str
    venue_order_id: str | None = None
    side: OrderSide
    requested_price: Decimal
    fill_price: Decimal | None = None
    size_usd: Decimal
    size_contracts: int | None = None
    status: OrderStatus = "submitting"
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExecutionResult(BaseModel):
    """Aggregate result for a two-leg execution."""

    id: str
    arb_id: str
    total_cost_usd: Decimal | None = None
    actual_spread: Decimal | None = None
    actual_pnl: Decimal | None = None
    slippage_from_ticket: Decimal | None = None
    poly_order_id: str | None = None
    kalshi_order_id: str | None = None
    status: ResultStatus = "pending"
    created_at: datetime | None = None
    error_message: str | None = None


class PreflightCheck(BaseModel):
    """Result of a single pre-execution validation check."""

    name: str
    passed: bool
    message: str
    value: Decimal | None = None


class PreflightResult(BaseModel):
    """Aggregated results of all pre-execution validation checks."""

    checks: list[PreflightCheck]
    suggested_size_usd: Decimal = Decimal("0")
    max_size_usd: Decimal = Decimal("0")
    estimated_slippage_poly: Decimal | None = None
    estimated_slippage_kalshi: Decimal | None = None
    poly_balance: Decimal | None = None
    kalshi_balance: Decimal | None = None
    poly_depth_contracts: int | None = None
    kalshi_depth_contracts: int | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_passed(self) -> bool:
        """Return True if every individual check passed."""
        return all(c.passed for c in self.checks)


class OrderRequest(BaseModel):
    """Parameters for placing a single-leg order."""

    venue: str
    side: OrderSide
    price: Decimal
    size_usd: Decimal
    size_contracts: int
    token_id: str = ""
    ticker: str = ""


class OrderResponse(BaseModel):
    """Result from a venue after placing or attempting an order."""

    venue_order_id: str = ""
    status: OrderStatus = "submitting"
    fill_price: Decimal | None = None
    error_message: str | None = None
    raw_status: str | None = None
    diagnostics: dict[str, object] | None = None


class LiquidityResult(BaseModel):
    """Result of liquidity validation across both venues."""

    poly_vwap: Decimal = Decimal("0")
    kalshi_vwap: Decimal = Decimal("0")
    poly_slippage: Decimal = Decimal("0")
    kalshi_slippage: Decimal = Decimal("0")
    poly_depth_contracts: int = 0
    kalshi_depth_contracts: int = 0
    max_absorbable_usd: Decimal = Decimal("0")
    passed: bool = False
    warnings: list[str] = []


class ConstraintStatus(BaseModel):
    """Status of a single capital constraint check."""

    name: str
    ok: bool
    detail: str


class BalancesResponse(BaseModel):
    """Venue balances, exposure, P&L, and capital constraint status."""

    poly_balance: Decimal
    kalshi_balance: Decimal
    total_balance: Decimal
    suggested_size_usd: Decimal
    current_exposure: Decimal
    remaining_capacity: Decimal
    daily_pnl: Decimal
    open_positions: int
    constraints: list[ConstraintStatus]
