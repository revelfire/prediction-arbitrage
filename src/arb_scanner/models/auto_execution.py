"""Data models for automated execution pipeline."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

AutoExecMode = Literal["off", "manual", "auto"]


class CircuitBreakerType(str, Enum):
    """Types of circuit breakers."""

    loss = "loss"
    failure = "failure"
    anomaly = "anomaly"


class CriticVerdict(BaseModel):
    """Result from the AI trade critic evaluation."""

    approved: bool = True
    risk_flags: list[str] = Field(default_factory=list)
    reasoning: str = ""
    confidence: float = 1.0
    skipped: bool = False
    error: str | None = None


class CircuitBreakerState(BaseModel):
    """Current state of a circuit breaker."""

    breaker_type: CircuitBreakerType
    tripped: bool = False
    tripped_at: datetime | None = None
    reason: str = ""
    reset_at: datetime | None = None
    requires_ack: bool = False


class AutoExecLogEntry(BaseModel):
    """Audit log entry for an auto-execution attempt."""

    id: str = ""
    arb_id: str
    trigger_spread_pct: Decimal = Decimal("0")
    trigger_confidence: Decimal = Decimal("0")
    criteria_snapshot: dict[str, Any] = Field(default_factory=dict)
    pre_exec_balances: dict[str, Any] = Field(default_factory=dict)
    size_usd: Decimal = Decimal("0")
    critic_verdict: CriticVerdict | None = None
    execution_result_id: str | None = None
    actual_spread: Decimal | None = None
    actual_pnl: Decimal | None = None
    slippage: Decimal | None = None
    duration_ms: int | None = None
    circuit_breaker_state: list[CircuitBreakerState] = Field(
        default_factory=list,
    )
    status: str = "pending"
    source: str = ""
    created_at: datetime | None = None


class AutoExecPosition(BaseModel):
    """Tracks an open auto-execution position."""

    id: str = ""
    arb_id: str
    poly_market_id: str = ""
    kalshi_ticker: str = ""
    entry_spread: Decimal = Decimal("0")
    entry_cost_usd: Decimal = Decimal("0")
    current_value_usd: Decimal = Decimal("0")
    status: str = "open"
    opened_at: datetime | None = None
    closed_at: datetime | None = None


class AutoExecStats(BaseModel):
    """Daily aggregate statistics for auto-execution."""

    date: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: Decimal = Decimal("0")
    avg_spread: Decimal = Decimal("0")
    avg_slippage: Decimal = Decimal("0")
    critic_rejections: int = 0
    breaker_trips: int = 0
