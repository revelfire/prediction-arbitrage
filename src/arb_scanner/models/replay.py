"""Data models for backtesting replay results and evaluation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, field_validator

from arb_scanner.models.flippening import ExitReason

_VALID_SIDES = frozenset({"yes", "no"})


class ReplaySignal(BaseModel):
    """A hypothetical signal produced during historical replay."""

    market_id: str
    entry_price: Decimal
    exit_price: Decimal
    exit_reason: ExitReason
    realized_pnl: Decimal
    hold_minutes: Decimal
    confidence: Decimal
    side: str
    entry_at: datetime
    exit_at: datetime

    @field_validator("side")
    @classmethod
    def side_valid(cls, v: str) -> str:
        """Validate that side is 'yes' or 'no'."""
        if v not in _VALID_SIDES:
            raise ValueError(f"side must be one of {_VALID_SIDES}, got '{v}'")
        return v


class ReplayEvaluation(BaseModel):
    """Aggregate metrics from a set of replay signals."""

    total_signals: int
    win_count: int
    win_rate: float
    avg_pnl: float
    avg_hold_minutes: float
    max_drawdown: float
    profit_factor: float
    config_overrides: dict[str, Any] = {}


class SweepResult(BaseModel):
    """Result of a parameter sweep — one evaluation per value."""

    param_name: str
    results: list[tuple[float, ReplayEvaluation]]
