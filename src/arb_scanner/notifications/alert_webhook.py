"""Async webhook dispatcher for TrendAlert notifications (Slack & Discord)."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from arb_scanner.models.analytics import AlertType, TrendAlert
from arb_scanner.notifications.webhook import _post_webhook

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="notifications.alert_webhook",
)

# ── Slack emoji per alert type ──────────────────────────────────────────

_SLACK_EMOJI: dict[AlertType, str] = {
    AlertType.convergence: ":chart_with_downwards_trend:",
    AlertType.divergence: ":chart_with_upwards_trend:",
    AlertType.new_high: ":trophy:",
    AlertType.disappeared: ":ghost:",
    AlertType.health_consecutive_failures: ":warning:",
    AlertType.health_zero_opps: ":warning:",
}

# ── Slack header text per alert type ────────────────────────────────────

_SLACK_HEADER: dict[AlertType, str] = {
    AlertType.convergence: "Spread Converging",
    AlertType.divergence: "Spread Diverging",
    AlertType.new_high: "New High Spread",
    AlertType.disappeared: "Opportunity Disappeared",
    AlertType.health_consecutive_failures: "Scanner Health Alert",
    AlertType.health_zero_opps: "Scanner Health Alert",
}

# ── Discord embed colour per alert type ─────────────────────────────────

_DISCORD_COLOR: dict[AlertType, int] = {
    AlertType.convergence: 16776960,  # Yellow
    AlertType.divergence: 3066993,  # Green
    AlertType.new_high: 15844367,  # Gold
    AlertType.disappeared: 9807270,  # Gray
    AlertType.health_consecutive_failures: 15158332,  # Red
    AlertType.health_zero_opps: 15158332,  # Red
}


# ── Helpers ─────────────────────────────────────────────────────────────


def _fmt_spread(value: Any) -> str:
    """Format a Decimal spread as a percentage string, or 'N/A'."""
    if value is None:
        return "N/A"
    return f"{float(value):.2%}"


def _fmt_id(value: str | None) -> str:
    """Return the identifier or 'N/A' when absent."""
    return value if value else "N/A"


# ── Slack payload ───────────────────────────────────────────────────────


def build_trend_slack_payload(alert: TrendAlert) -> dict[str, Any]:
    """Build a Slack Block Kit payload for a trend alert.

    Args:
        alert: The trend alert to format.

    Returns:
        Dict matching the Slack incoming-webhook JSON contract.
    """
    emoji = _SLACK_EMOJI[alert.alert_type]
    header = _SLACK_HEADER[alert.alert_type]
    pair = f"{_fmt_id(alert.poly_event_id)} / {_fmt_id(alert.kalshi_event_id)}"

    return {
        "text": f"{emoji} {header}: {alert.message}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{emoji} {header}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Pair:* {pair}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Spread Before:* {_fmt_spread(alert.spread_before)}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Spread After:* {_fmt_spread(alert.spread_after)}",
                    },
                    {"type": "mrkdwn", "text": f"*Message:* {alert.message}"},
                ],
            },
        ],
    }


# ── Discord payload ─────────────────────────────────────────────────────


def build_trend_discord_payload(alert: TrendAlert) -> dict[str, Any]:
    """Build a Discord embed payload for a trend alert.

    Args:
        alert: The trend alert to format.

    Returns:
        Dict matching the Discord webhook JSON contract.
    """
    emoji = _SLACK_EMOJI[alert.alert_type]
    header = _SLACK_HEADER[alert.alert_type]
    pair = f"{_fmt_id(alert.poly_event_id)} / {_fmt_id(alert.kalshi_event_id)}"

    return {
        "content": f"{emoji} {header}: {alert.message}",
        "embeds": [
            {
                "title": header,
                "color": _DISCORD_COLOR[alert.alert_type],
                "fields": [
                    {"name": "Pair", "value": pair, "inline": True},
                    {
                        "name": "Spread Before",
                        "value": _fmt_spread(alert.spread_before),
                        "inline": True,
                    },
                    {
                        "name": "Spread After",
                        "value": _fmt_spread(alert.spread_after),
                        "inline": True,
                    },
                    {
                        "name": "Message",
                        "value": alert.message,
                        "inline": False,
                    },
                ],
            },
        ],
    }


# ── Dispatch ────────────────────────────────────────────────────────────


async def _send_safe(
    url: str,
    payload: dict[str, Any],
    client: httpx.AsyncClient,
    target: str,
) -> None:
    """Send a webhook, logging and swallowing any exception.

    Args:
        url: Webhook endpoint URL.
        payload: JSON body.
        client: httpx async client.
        target: Label for logging (e.g. 'slack', 'discord').
    """
    try:
        await _post_webhook(url, payload, client)
        logger.info("trend_alert_webhook_sent", target=target)
    except Exception:
        logger.exception("trend_alert_webhook_failed", target=target)


async def dispatch_trend_alert(
    alert: TrendAlert,
    *,
    slack_url: str = "",
    discord_url: str = "",
    client: httpx.AsyncClient | None = None,
) -> None:
    """Fire-and-forget webhook dispatch for a trend alert.

    Logs failures but never raises -- safe to call in a scan loop.

    Args:
        alert: The trend alert to notify about.
        slack_url: Slack incoming webhook URL (empty to skip).
        discord_url: Discord incoming webhook URL (empty to skip).
        client: Optional shared httpx client (created if not provided).
    """
    owns_client = client is None
    http = client or httpx.AsyncClient()
    try:
        if slack_url:
            await _send_safe(
                slack_url,
                build_trend_slack_payload(alert),
                http,
                "slack",
            )
        if discord_url:
            await _send_safe(
                discord_url,
                build_trend_discord_payload(alert),
                http,
                "discord",
            )
    finally:
        if owns_client:
            await http.aclose()
