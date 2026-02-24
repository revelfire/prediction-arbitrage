"""Integration tests for webhook dispatcher (Slack & Discord)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult
from arb_scanner.notifications.webhook import (
    build_discord_payload,
    build_slack_payload,
    dispatch_webhook,
)

_NOW = datetime.now(tz=timezone.utc)


def _make_opp() -> ArbOpportunity:
    """Build a test ArbOpportunity for webhook tests."""
    match = MatchResult(
        poly_event_id="poly-001",
        kalshi_event_id="kalshi-001",
        match_confidence=0.95,
        resolution_equivalent=True,
        resolution_risks=[],
        safe_to_arb=True,
        reasoning="Test match",
        matched_at=_NOW,
        ttl_expires=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    poly = Market(
        venue=Venue.POLYMARKET,
        event_id="poly-001",
        title="Will BTC exceed $100k?",
        description="BTC price prediction",
        resolution_criteria="Price above 100k",
        yes_bid=Decimal("0.60"),
        yes_ask=Decimal("0.62"),
        no_bid=Decimal("0.33"),
        no_ask=Decimal("0.35"),
        volume_24h=Decimal("50000"),
        fees_pct=Decimal("0.00"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )
    kalshi = Market(
        venue=Venue.KALSHI,
        event_id="kalshi-001",
        title="Will BTC exceed $100k?",
        description="BTC price prediction",
        resolution_criteria="Price above 100k",
        yes_bid=Decimal("0.60"),
        yes_ask=Decimal("0.65"),
        no_bid=Decimal("0.30"),
        no_ask=Decimal("0.35"),
        volume_24h=Decimal("30000"),
        fees_pct=Decimal("0.07"),
        fee_model="per_contract",
        last_updated=_NOW,
    )
    return ArbOpportunity(
        match=match,
        poly_market=poly,
        kalshi_market=kalshi,
        buy_venue=Venue.POLYMARKET,
        sell_venue=Venue.KALSHI,
        cost_per_contract=Decimal("0.97"),
        gross_profit=Decimal("0.03"),
        net_profit=Decimal("0.0186"),
        net_spread_pct=Decimal("0.0186"),
        max_size=Decimal("150"),
        annualized_return=Decimal("0.34"),
        depth_risk=False,
        detected_at=_NOW,
    )


class TestSlackPayload:
    """Tests for Slack webhook payload construction."""

    def test_slack_payload_has_text(self) -> None:
        """Slack payload must have a fallback text field."""
        payload = build_slack_payload(_make_opp())
        assert "text" in payload
        assert "Arb Alert" in payload["text"]

    def test_slack_payload_has_blocks(self) -> None:
        """Slack payload must have blocks with header and section."""
        payload = build_slack_payload(_make_opp())
        assert "blocks" in payload
        blocks = payload["blocks"]
        assert len(blocks) == 2
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "section"

    def test_slack_section_has_six_fields(self) -> None:
        """Slack section block must have exactly 6 fields per contract."""
        payload = build_slack_payload(_make_opp())
        fields = payload["blocks"][1]["fields"]
        assert len(fields) == 6
        names = [f["text"] for f in fields]
        assert any("Buy" in n for n in names)
        assert any("Sell" in n for n in names)
        assert any("Net Spread" in n for n in names)
        assert any("Max Size" in n for n in names)
        assert any("Match Confidence" in n for n in names)
        assert any("Annualized" in n for n in names)

    def test_slack_fields_are_mrkdwn(self) -> None:
        """All Slack section fields should be mrkdwn type."""
        payload = build_slack_payload(_make_opp())
        for field in payload["blocks"][1]["fields"]:
            assert field["type"] == "mrkdwn"


class TestDiscordPayload:
    """Tests for Discord webhook payload construction."""

    def test_discord_payload_has_content(self) -> None:
        """Discord payload must have a content field."""
        payload = build_discord_payload(_make_opp())
        assert "content" in payload
        assert "Arb Alert" in payload["content"]

    def test_discord_payload_has_embeds(self) -> None:
        """Discord payload must have embeds with correct structure."""
        payload = build_discord_payload(_make_opp())
        assert "embeds" in payload
        assert len(payload["embeds"]) == 1
        embed = payload["embeds"][0]
        assert embed["title"] == "Arbitrage Opportunity"
        assert embed["color"] == 3066993

    def test_discord_embed_has_six_fields(self) -> None:
        """Discord embed must have 6 inline fields."""
        payload = build_discord_payload(_make_opp())
        fields = payload["embeds"][0]["fields"]
        assert len(fields) == 6
        for f in fields:
            assert f["inline"] is True
        names = {f["name"] for f in fields}
        assert names == {
            "Buy",
            "Sell",
            "Net Spread",
            "Max Size",
            "Match Confidence",
            "Annualized Return",
        }


class TestWebhookDispatch:
    """Tests for the async webhook dispatch logic."""

    @pytest.mark.asyncio()
    async def test_slack_dispatch_posts_correctly(self) -> None:
        """Dispatch should POST the Slack payload to the configured URL."""
        requests_made: list[httpx.Request] = []

        async def _handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(request)
            return httpx.Response(200)

        transport = httpx.MockTransport(_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await dispatch_webhook(
                _make_opp(),
                slack_url="https://hooks.slack.com/test",
                client=client,
            )

        assert len(requests_made) == 1
        assert requests_made[0].url == "https://hooks.slack.com/test"

    @pytest.mark.asyncio()
    async def test_discord_dispatch_posts_correctly(self) -> None:
        """Dispatch should POST the Discord payload to the configured URL."""
        requests_made: list[httpx.Request] = []

        async def _handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(request)
            return httpx.Response(200)

        transport = httpx.MockTransport(_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await dispatch_webhook(
                _make_opp(),
                discord_url="https://discord.com/api/webhooks/test",
                client=client,
            )

        assert len(requests_made) == 1

    @pytest.mark.asyncio()
    async def test_retry_on_failure_then_success(self) -> None:
        """Should retry on HTTP 500 and succeed on subsequent 200."""
        call_count = 0

        async def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return httpx.Response(500)
            return httpx.Response(200)

        transport = httpx.MockTransport(_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await dispatch_webhook(
                _make_opp(),
                slack_url="https://hooks.slack.com/test",
                client=client,
            )

        assert call_count == 3

    @pytest.mark.asyncio()
    async def test_fire_and_forget_no_exception(self) -> None:
        """Dispatch should not raise even when all retries are exhausted."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        transport = httpx.MockTransport(_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            # Should NOT raise -- fire-and-forget behavior
            await dispatch_webhook(
                _make_opp(),
                slack_url="https://hooks.slack.com/test",
                client=client,
            )

    @pytest.mark.asyncio()
    async def test_both_webhooks_dispatched(self) -> None:
        """When both URLs are configured, both should receive POSTs."""
        urls_hit: list[str] = []

        async def _handler(request: httpx.Request) -> httpx.Response:
            urls_hit.append(str(request.url))
            return httpx.Response(200)

        transport = httpx.MockTransport(_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await dispatch_webhook(
                _make_opp(),
                slack_url="https://hooks.slack.com/test",
                discord_url="https://discord.com/api/webhooks/test",
                client=client,
            )

        assert len(urls_hit) == 2

    @pytest.mark.asyncio()
    async def test_no_dispatch_when_urls_empty(self) -> None:
        """When no webhook URLs are configured, no requests should be made."""
        call_count = 0

        async def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200)

        transport = httpx.MockTransport(_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await dispatch_webhook(_make_opp(), client=client)

        assert call_count == 0
