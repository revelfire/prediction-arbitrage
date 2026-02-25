"""Data models for the flippening engine (mean reversion on live sports)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from arb_scanner.models.market import Market


class GamePhase(str, Enum):
    """Lifecycle phase of a sports game being monitored."""

    UPCOMING = "upcoming"
    LIVE = "live"
    COMPLETED = "completed"


class SpikeDirection(str, Enum):
    """Direction of a detected price spike relative to the pre-game favorite."""

    FAVORITE_DROP = "favorite_drop"
    UNDERDOG_RISE = "underdog_rise"


class ExitReason(str, Enum):
    """Reason for exiting a flippening position."""

    REVERSION = "reversion"
    STOP_LOSS = "stop_loss"
    TIMEOUT = "timeout"
    RESOLUTION = "resolution"
    DISCONNECT = "disconnect"


class PriceUpdate(BaseModel):
    """A real-time price update from the Polymarket CLOB."""

    market_id: str
    token_id: str
    yes_bid: Decimal
    yes_ask: Decimal
    no_bid: Decimal
    no_ask: Decimal
    timestamp: datetime

    @field_validator("yes_bid", "yes_ask", "no_bid", "no_ask")
    @classmethod
    def price_in_range(cls, v: Decimal) -> Decimal:
        """Validate that prices are in [0.0, 1.0]."""
        if v < Decimal("0") or v > Decimal("1"):
            raise ValueError(f"Price must be in [0.0, 1.0], got {v}")
        return v


class Baseline(BaseModel):
    """Pre-game baseline odds captured at game start."""

    market_id: str
    token_id: str
    yes_price: Decimal
    no_price: Decimal
    sport: str
    game_start_time: datetime | None = None
    captured_at: datetime
    late_join: bool = False

    @field_validator("yes_price", "no_price")
    @classmethod
    def price_in_range(cls, v: Decimal) -> Decimal:
        """Validate that baseline prices are in [0.0, 1.0]."""
        if v < Decimal("0") or v > Decimal("1"):
            raise ValueError(f"Price must be in [0.0, 1.0], got {v}")
        return v


class SportsMarket(BaseModel):
    """A Polymarket market identified as a sports event."""

    market: Market
    sport: str
    game_start_time: datetime | None = None
    token_id: str


class FlippeningEvent(BaseModel):
    """A detected flippening: an emotional overreaction spike in a live game."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    market_id: str
    market_title: str
    baseline_yes: Decimal
    spike_price: Decimal
    spike_magnitude_pct: Decimal
    spike_direction: SpikeDirection
    confidence: Decimal
    sport: str
    detected_at: datetime

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: Decimal) -> Decimal:
        """Validate that confidence is in [0.0, 1.0]."""
        if v < Decimal("0") or v > Decimal("1"):
            raise ValueError(f"Confidence must be in [0.0, 1.0], got {v}")
        return v


_VALID_SIDES = frozenset({"yes", "no"})


class EntrySignal(BaseModel):
    """An entry signal recommending a position in a flippening."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str
    side: str
    entry_price: Decimal
    target_exit_price: Decimal
    stop_loss_price: Decimal
    suggested_size_usd: Decimal
    expected_profit_pct: Decimal
    max_hold_minutes: int
    created_at: datetime

    @field_validator("side")
    @classmethod
    def side_valid(cls, v: str) -> str:
        """Validate that side is 'yes' or 'no'."""
        if v not in _VALID_SIDES:
            raise ValueError(f"side must be one of {_VALID_SIDES}, got '{v}'")
        return v

    @field_validator("entry_price")
    @classmethod
    def entry_in_range(cls, v: Decimal) -> Decimal:
        """Validate that entry_price is in [0.0, 1.0]."""
        if v < Decimal("0") or v > Decimal("1"):
            raise ValueError(f"entry_price must be in [0.0, 1.0], got {v}")
        return v


class ExitSignal(BaseModel):
    """An exit signal indicating a position should be closed."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str
    side: str
    exit_price: Decimal
    exit_reason: ExitReason
    realized_pnl: Decimal
    realized_pnl_pct: Decimal
    hold_minutes: Decimal
    created_at: datetime

    @field_validator("side")
    @classmethod
    def side_valid(cls, v: str) -> str:
        """Validate that side is 'yes' or 'no'."""
        if v not in _VALID_SIDES:
            raise ValueError(f"side must be one of {_VALID_SIDES}, got '{v}'")
        return v
