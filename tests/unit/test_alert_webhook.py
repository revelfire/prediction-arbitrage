"""T014 - Unit tests for alert webhook payload builders and dispatch."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from arb_scanner.models.analytics import AlertType, TrendAlert
from arb_scanner.notifications.alert_webhook import (
    _DISCORD_COLOR,
    build_trend_discord_payload,
    build_trend_slack_payload,
    dispatch_trend_alert,
)

_NOW = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_alert(
    alert_type: AlertType,
    *,
    poly_id: str | None = "POLY1",
    kalshi_id: str | None = "KALSHI1",
    spread_before: Decimal | None = Decimal("0.05"),
    spread_after: Decimal | None = Decimal("0.03"),
    message: str = "Test alert",
) -> TrendAlert:
    """Build a TrendAlert with sensible defaults for testing.

    Args:
        alert_type: The type of alert to build.
        poly_id: Polymarket event identifier (None for health alerts).
        kalshi_id: Kalshi event identifier (None for health alerts).
        spread_before: Spread before the event.
        spread_after: Spread after the event.
        message: Human-readable alert message.

    Returns:
        A fully-constructed TrendAlert.
    """
    return TrendAlert(
        alert_type=alert_type,
        poly_event_id=poly_id,
        kalshi_event_id=kalshi_id,
        spread_before=spread_before,
        spread_after=spread_after,
        message=message,
        dispatched_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Slack payload tests
# ---------------------------------------------------------------------------


class TestSlackPayload:
    """Tests for Slack webhook payload construction per alert type."""

    def test_slack_payload_convergence(self) -> None:
        """Convergence alert has correct emoji, header, and section fields."""
        alert = _make_alert(AlertType.convergence, message="Spread narrowing")
        payload = build_trend_slack_payload(alert)

        assert "text" in payload
        assert ":chart_with_downwards_trend:" in payload["text"]

        blocks = payload["blocks"]
        assert isinstance(blocks, list)
        assert len(blocks) == 2

        header_block = blocks[0]
        assert header_block["type"] == "header"
        assert "Spread Converging" in header_block["text"]["text"]

        section_block = blocks[1]
        assert section_block["type"] == "section"
        field_texts = [f["text"] for f in section_block["fields"]]
        assert any("Pair" in t for t in field_texts)
        assert any("Spread Before" in t for t in field_texts)
        assert any("Spread After" in t for t in field_texts)
        assert any("Message" in t for t in field_texts)

    def test_slack_payload_divergence(self) -> None:
        """Divergence alert uses upward trend emoji and correct header."""
        alert = _make_alert(AlertType.divergence, message="Spread widening")
        payload = build_trend_slack_payload(alert)

        assert ":chart_with_upwards_trend:" in payload["text"]
        header_text = payload["blocks"][0]["text"]["text"]
        assert "Spread Diverging" in header_text

    def test_slack_payload_health_none_ids(self) -> None:
        """Health alert with None poly/kalshi IDs renders N/A gracefully."""
        alert = _make_alert(
            AlertType.health_consecutive_failures,
            poly_id=None,
            kalshi_id=None,
            spread_before=None,
            spread_after=None,
            message="3 consecutive scan failures",
        )
        payload = build_trend_slack_payload(alert)

        section = payload["blocks"][1]
        field_texts = [f["text"] for f in section["fields"]]

        pair_field = next(t for t in field_texts if "Pair" in t)
        assert "N/A" in pair_field

        spread_before_field = next(t for t in field_texts if "Spread Before" in t)
        assert "N/A" in spread_before_field

        spread_after_field = next(t for t in field_texts if "Spread After" in t)
        assert "N/A" in spread_after_field


# ---------------------------------------------------------------------------
# Discord payload tests
# ---------------------------------------------------------------------------


class TestDiscordPayload:
    """Tests for Discord webhook payload construction per alert type."""

    def test_discord_payload_convergence(self) -> None:
        """Convergence alert has embeds list with correct color and fields."""
        alert = _make_alert(AlertType.convergence)
        payload = build_trend_discord_payload(alert)

        assert "embeds" in payload
        embeds = payload["embeds"]
        assert len(embeds) == 1

        embed = embeds[0]
        assert embed["color"] == 16776960

        field_names = {f["name"] for f in embed["fields"]}
        assert "Pair" in field_names
        assert "Spread Before" in field_names
        assert "Spread After" in field_names

    def test_discord_payload_colors(self) -> None:
        """Each AlertType maps to the expected Discord embed color."""
        expected: dict[AlertType, int] = {
            AlertType.convergence: 16776960,
            AlertType.divergence: 3066993,
            AlertType.new_high: 15844367,
            AlertType.disappeared: 9807270,
            AlertType.health_consecutive_failures: 15158332,
            AlertType.health_zero_opps: 15158332,
        }
        for alert_type, color in expected.items():
            assert _DISCORD_COLOR[alert_type] == color, f"Color mismatch for {alert_type.value}"

            alert = _make_alert(alert_type)
            payload = build_trend_discord_payload(alert)
            assert payload["embeds"][0]["color"] == color, (
                f"Payload color mismatch for {alert_type.value}"
            )


# ---------------------------------------------------------------------------
# Dispatch tests
# ---------------------------------------------------------------------------


class TestDispatch:
    """Tests for the async dispatch_trend_alert function."""

    @pytest.mark.asyncio()
    async def test_dispatch_calls_post(self) -> None:
        """dispatch_trend_alert POSTs to both Slack and Discord URLs."""
        alert = _make_alert(AlertType.convergence)

        with patch(
            "arb_scanner.notifications.alert_webhook._post_webhook",
            new_callable=AsyncMock,
        ) as mock_post:
            await dispatch_trend_alert(
                alert,
                slack_url="https://hooks.slack.com/test",
                discord_url="https://discord.com/api/webhooks/test",
            )

            assert mock_post.call_count == 2

            urls_called = [call.args[0] for call in mock_post.call_args_list]
            assert "https://hooks.slack.com/test" in urls_called
            assert "https://discord.com/api/webhooks/test" in urls_called
