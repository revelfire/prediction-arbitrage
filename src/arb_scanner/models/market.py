"""Market data models for prediction market venues."""

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class Venue(str, Enum):
    """Supported prediction market venues."""

    POLYMARKET = "polymarket"
    KALSHI = "kalshi"


_VALID_FEE_MODELS = frozenset({"on_winnings", "per_contract"})


class Market(BaseModel):
    """Represents a single market from a prediction market venue.

    Contains price quotes, volume, fee information, and raw venue data.
    All prices are expressed as decimals in [0.0, 1.0].
    """

    venue: Venue
    event_id: str
    title: str
    description: str
    resolution_criteria: str
    yes_bid: Decimal
    yes_ask: Decimal
    no_bid: Decimal
    no_ask: Decimal
    volume_24h: Decimal
    expiry: datetime | None = None
    fees_pct: Decimal
    fee_model: str
    last_updated: datetime
    raw_data: dict[str, object] = Field(default_factory=dict)

    @field_validator("event_id")
    @classmethod
    def event_id_non_empty(cls, v: str) -> str:
        """Validate that event_id is non-empty."""
        if not v.strip():
            raise ValueError("event_id must be non-empty")
        return v

    @field_validator("title")
    @classmethod
    def title_non_empty(cls, v: str) -> str:
        """Validate that title is non-empty."""
        if not v.strip():
            raise ValueError("title must be non-empty")
        return v

    @field_validator("fee_model")
    @classmethod
    def fee_model_valid(cls, v: str) -> str:
        """Validate that fee_model is 'on_winnings' or 'per_contract'."""
        if v not in _VALID_FEE_MODELS:
            raise ValueError(f"fee_model must be one of {_VALID_FEE_MODELS}, got '{v}'")
        return v

    @field_validator("yes_bid", "yes_ask", "no_bid", "no_ask")
    @classmethod
    def price_in_range(cls, v: Decimal) -> Decimal:
        """Validate that all prices are in [0.0, 1.0]."""
        if v < Decimal("0") or v > Decimal("1"):
            raise ValueError(f"Price must be in [0.0, 1.0], got {v}")
        return v

    @model_validator(mode="after")
    def bid_lte_ask(self) -> "Market":
        """Validate that bid prices do not exceed ask prices."""
        if self.yes_bid > self.yes_ask:
            raise ValueError(f"yes_bid ({self.yes_bid}) must be <= yes_ask ({self.yes_ask})")
        if self.no_bid > self.no_ask:
            raise ValueError(f"no_bid ({self.no_bid}) must be <= no_ask ({self.no_ask})")
        return self
