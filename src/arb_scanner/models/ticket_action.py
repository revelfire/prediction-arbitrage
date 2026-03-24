"""Models for ticket lifecycle actions and PATCH request validation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

_VALID_ACTIONS = frozenset({"approve", "execute", "expire", "cancel", "annotate"})

_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"approved", "expired", "cancelled"}),
    "approved": frozenset({"executed", "cancelled"}),
    "executed": frozenset(),
    "expired": frozenset(),
    "cancelled": frozenset(),
}


def valid_transition(current: str, target: str) -> bool:
    """Check whether a status transition is allowed.

    Args:
        current: Current ticket status.
        target: Desired new status.

    Returns:
        True if the transition is valid.
    """
    return target in _VALID_TRANSITIONS.get(current, frozenset())


class TicketAction(BaseModel):
    """A single action recorded against an execution ticket."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ticket_id: str
    action: str
    actual_entry_price: Decimal | None = None
    actual_size_usd: Decimal | None = None
    actual_exit_price: Decimal | None = None
    actual_pnl: Decimal | None = None
    slippage: Decimal | None = None
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @field_validator("action")
    @classmethod
    def action_valid(cls, v: str) -> str:
        """Validate that action is a known lifecycle action."""
        if v not in _VALID_ACTIONS:
            raise ValueError(f"action must be one of {_VALID_ACTIONS}, got '{v}'")
        return v


class TicketPatchBody(BaseModel):
    """Request body for PATCH /api/tickets/{ticket_id}."""

    status: str | None = None
    actual_entry_price: Decimal | None = None
    actual_size_usd: Decimal | None = None
    notes: str | None = None
