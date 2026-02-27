"""Unit tests for ticket action models and state transitions."""

from __future__ import annotations

from decimal import Decimal

import pytest

from arb_scanner.models.ticket_action import (
    TicketAction,
    TicketPatchBody,
    _VALID_ACTIONS,
    _VALID_TRANSITIONS,
    valid_transition,
)


class TestValidTransition:
    """Tests for the valid_transition helper."""

    def test_pending_to_approved(self) -> None:
        """Pending -> approved is allowed."""
        assert valid_transition("pending", "approved") is True

    def test_pending_to_expired(self) -> None:
        """Pending -> expired is allowed."""
        assert valid_transition("pending", "expired") is True

    def test_pending_to_cancelled(self) -> None:
        """Pending -> cancelled is allowed."""
        assert valid_transition("pending", "cancelled") is True

    def test_pending_to_executed(self) -> None:
        """Pending -> executed is NOT allowed (must approve first)."""
        assert valid_transition("pending", "executed") is False

    def test_approved_to_executed(self) -> None:
        """Approved -> executed is allowed."""
        assert valid_transition("approved", "executed") is True

    def test_approved_to_cancelled(self) -> None:
        """Approved -> cancelled is allowed."""
        assert valid_transition("approved", "cancelled") is True

    def test_approved_to_expired(self) -> None:
        """Approved -> expired is NOT allowed."""
        assert valid_transition("approved", "expired") is False

    def test_executed_is_terminal(self) -> None:
        """Executed is a terminal state — no transitions out."""
        assert valid_transition("executed", "pending") is False
        assert valid_transition("executed", "cancelled") is False

    def test_expired_is_terminal(self) -> None:
        """Expired is a terminal state."""
        assert valid_transition("expired", "pending") is False

    def test_cancelled_is_terminal(self) -> None:
        """Cancelled is a terminal state."""
        assert valid_transition("cancelled", "approved") is False

    def test_unknown_current_status(self) -> None:
        """Unknown current status returns False."""
        assert valid_transition("bogus", "approved") is False

    def test_all_valid_transitions_covered(self) -> None:
        """Transition map covers all known statuses."""
        expected = {"pending", "approved", "executed", "expired", "cancelled"}
        assert set(_VALID_TRANSITIONS.keys()) == expected


class TestTicketAction:
    """Tests for the TicketAction model."""

    def test_valid_actions(self) -> None:
        """All defined actions are accepted."""
        for action in _VALID_ACTIONS:
            ta = TicketAction(ticket_id="t1", action=action)
            assert ta.action == action

    def test_invalid_action_raises(self) -> None:
        """Unknown action string raises ValueError."""
        with pytest.raises(ValueError, match="action must be one of"):
            TicketAction(ticket_id="t1", action="nope")

    def test_defaults(self) -> None:
        """Default fields are populated."""
        ta = TicketAction(ticket_id="t1", action="approve")
        assert ta.id  # non-empty UUID
        assert ta.notes == ""
        assert ta.actual_entry_price is None
        assert ta.slippage is None

    def test_full_fields(self) -> None:
        """All optional fields can be populated."""
        ta = TicketAction(
            ticket_id="t1",
            action="execute",
            actual_entry_price=Decimal("0.45"),
            actual_size_usd=Decimal("100"),
            actual_exit_price=Decimal("0.55"),
            actual_pnl=Decimal("10"),
            slippage=Decimal("0.01"),
            notes="Manual execution",
        )
        assert ta.actual_entry_price == Decimal("0.45")
        assert ta.slippage == Decimal("0.01")


class TestTicketPatchBody:
    """Tests for the TicketPatchBody request model."""

    def test_empty_body(self) -> None:
        """Empty body is valid (annotation-only PATCH)."""
        body = TicketPatchBody()
        assert body.status is None
        assert body.notes is None

    def test_status_only(self) -> None:
        """Status-only PATCH body."""
        body = TicketPatchBody(status="approved")
        assert body.status == "approved"

    def test_execution_data(self) -> None:
        """Execution data fields populate correctly."""
        body = TicketPatchBody(
            status="executed",
            actual_entry_price=Decimal("0.48"),
            actual_size_usd=Decimal("200"),
            notes="Filled at market",
        )
        assert body.actual_entry_price == Decimal("0.48")
        assert body.notes == "Filled at market"
