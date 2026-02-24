"""Arbitrage opportunity and execution ticket models."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult

_VALID_STATUSES = frozenset({"pending", "approved", "expired"})


class ArbOpportunity(BaseModel):
    """A detected arbitrage opportunity between two venues.

    Includes the matched markets, spread calculations, and risk flags.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    match: MatchResult
    poly_market: Market
    kalshi_market: Market
    buy_venue: Venue
    sell_venue: Venue
    cost_per_contract: Decimal
    gross_profit: Decimal
    net_profit: Decimal
    net_spread_pct: Decimal
    max_size: Decimal
    annualized_return: Decimal | None = None
    depth_risk: bool
    detected_at: datetime

    @model_validator(mode="after")
    def venues_differ(self) -> "ArbOpportunity":
        """Validate that buy and sell venues are different."""
        if self.buy_venue == self.sell_venue:
            raise ValueError("buy_venue and sell_venue must be different")
        return self

    @field_validator("cost_per_contract")
    @classmethod
    def cost_below_one(cls, v: Decimal) -> Decimal:
        """Validate that cost_per_contract is less than 1.0 for a valid arb."""
        if v >= Decimal("1"):
            raise ValueError(f"cost_per_contract must be < 1.0 for a valid arb, got {v}")
        return v


class ExecutionTicket(BaseModel):
    """An execution ticket for a human operator to review and approve.

    Represents the two legs of an arbitrage trade with expected cost and profit.
    """

    arb_id: str
    leg_1: dict[str, object]
    leg_2: dict[str, object]
    expected_cost: Decimal
    expected_profit: Decimal
    status: str = "pending"

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str) -> str:
        """Validate that status is one of 'pending', 'approved', or 'expired'."""
        if v not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {_VALID_STATUSES}, got '{v}'")
        return v
