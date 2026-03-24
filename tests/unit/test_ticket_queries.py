"""Sanity checks for ticket query constants."""

from __future__ import annotations

from arb_scanner.storage import _ticket_queries as TQ


class TestTicketQueryConstants:
    """Verify query strings exist and contain expected SQL keywords."""

    def test_get_tickets_filtered(self) -> None:
        """GET_TICKETS_FILTERED contains SELECT and WHERE."""
        assert "SELECT" in TQ.GET_TICKETS_FILTERED
        assert "WHERE" in TQ.GET_TICKETS_FILTERED
        assert "LIMIT" in TQ.GET_TICKETS_FILTERED

    def test_get_ticket_by_id(self) -> None:
        """GET_TICKET_BY_ID selects with LEFT JOIN."""
        assert "LEFT JOIN" in TQ.GET_TICKET_BY_ID

    def test_update_ticket_status(self) -> None:
        """UPDATE_TICKET_STATUS contains UPDATE."""
        assert "UPDATE" in TQ.UPDATE_TICKET_STATUS

    def test_insert_ticket_action(self) -> None:
        """INSERT_TICKET_ACTION inserts into actions table."""
        assert "INSERT INTO flippening_ticket_actions" in TQ.INSERT_TICKET_ACTION

    def test_get_ticket_actions(self) -> None:
        """GET_TICKET_ACTIONS selects from actions table."""
        assert "flippening_ticket_actions" in TQ.GET_TICKET_ACTIONS
        assert "ORDER BY" in TQ.GET_TICKET_ACTIONS

    def test_get_ticket_summary(self) -> None:
        """GET_TICKET_SUMMARY contains CTE."""
        assert "WITH" in TQ.GET_TICKET_SUMMARY
        assert "execution_rate" in TQ.GET_TICKET_SUMMARY

    def test_auto_expire_tickets(self) -> None:
        """AUTO_EXPIRE_TICKETS updates pending tickets."""
        assert "UPDATE" in TQ.AUTO_EXPIRE_TICKETS
        assert "RETURNING" in TQ.AUTO_EXPIRE_TICKETS
