"""Tests for trade execution webhook payloads."""

from __future__ import annotations

from decimal import Decimal

from arb_scanner.notifications.trade_webhook import build_trade_slack_payload


class TestBuildTradeSlackPayload:
    def test_buy_payload(self) -> None:
        payload = build_trade_slack_payload(
            action="buy",
            market_title="Grizzlies vs. Hawks O/U 231.5",
            side="yes",
            size_contracts=31,
            price=Decimal("0.37"),
            arb_id="arb-123",
        )
        assert "BUY EXECUTED" in payload["text"]
        assert "blocks" in payload
        fields = payload["blocks"][1]["fields"]
        market_field = fields[0]["text"]
        assert "Grizzlies" in market_field

    def test_sell_payload(self) -> None:
        payload = build_trade_slack_payload(
            action="sell",
            market_title="Grizzlies O/U",
            side="yes",
            size_contracts=31,
            price=Decimal("0.01"),
            arb_id="arb-123",
        )
        assert "SELL SUBMITTED" in payload["text"]

    def test_closed_payload_with_pnl(self) -> None:
        payload = build_trade_slack_payload(
            action="closed",
            market_title="Grizzlies O/U",
            side="yes",
            size_contracts=31,
            price=Decimal("0.01"),
            arb_id="arb-123",
            pnl=Decimal("-11.47"),
        )
        assert "CLOSED" in payload["text"]
        fields = payload["blocks"][1]["fields"]
        pnl_field = [f for f in fields if "P&L" in f["text"]]
        assert len(pnl_field) == 1
        assert "-11.47" in pnl_field[0]["text"]

    def test_buy_includes_action_links(self) -> None:
        payload = build_trade_slack_payload(
            action="buy",
            market_title="Test",
            side="yes",
            size_contracts=10,
            price=Decimal("0.50"),
            arb_id="arb-456",
            dashboard_url="https://arb.spillwave.com",
            auth_token="secret",
        )
        context_block = payload["blocks"][-1]
        assert context_block["type"] == "context"
        text = context_block["elements"][0]["text"]
        assert "View Position" in text
        assert "Exit Now" in text
        assert "token=secret" in text

    def test_sell_has_no_exit_link(self) -> None:
        payload = build_trade_slack_payload(
            action="sell",
            market_title="Test",
            side="yes",
            size_contracts=10,
            price=Decimal("0.01"),
            arb_id="arb-456",
            dashboard_url="https://arb.spillwave.com",
        )
        context_block = payload["blocks"][-1]
        text = context_block["elements"][0]["text"]
        assert "View Position" in text
        assert "Exit Now" not in text

    def test_no_links_without_dashboard_url(self) -> None:
        payload = build_trade_slack_payload(
            action="buy",
            market_title="Test",
            side="yes",
            size_contracts=10,
            price=Decimal("0.50"),
            arb_id="arb-456",
        )
        assert len(payload["blocks"]) == 2  # header + section only, no context
