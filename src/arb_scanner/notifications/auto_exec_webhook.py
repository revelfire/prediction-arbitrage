"""Webhook notifications for auto-execution events."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from arb_scanner.models.auto_execution import AutoExecLogEntry
from arb_scanner.notifications.webhook import _post_webhook

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="notifications.auto_exec_webhook",
)

_STATUS_EMOJI: dict[str, str] = {
    "executed": ":white_check_mark:",
    "failed": ":x:",
    "rejected": ":no_entry_sign:",
    "critic_rejected": ":brain:",
    "breaker_blocked": ":rotating_light:",
    "partial": ":warning:",
}

_STATUS_COLOR: dict[str, int] = {
    "executed": 3066993,
    "failed": 15158332,
    "rejected": 9807270,
    "critic_rejected": 10181046,
    "breaker_blocked": 15158332,
    "partial": 16776960,
}


def build_auto_exec_slack_payload(entry: AutoExecLogEntry) -> dict[str, Any]:
    """Build Slack Block Kit payload for an auto-execution event.

    Args:
        entry: Auto-execution log entry.

    Returns:
        Slack webhook payload dict.
    """
    emoji = _STATUS_EMOJI.get(entry.status, ":robot_face:")
    header = f"Auto-Exec: {entry.status.replace('_', ' ').title()}"
    size_str = f"${float(entry.size_usd):.2f}" if entry.size_usd else "N/A"
    spread_str = f"{float(entry.trigger_spread_pct):.4%}" if entry.trigger_spread_pct else "N/A"

    critic_text = "Skipped"
    if entry.critic_verdict and not entry.critic_verdict.skipped:
        verdict = entry.critic_verdict
        status = "Approved" if verdict.approved else "Rejected"
        flags = len(verdict.risk_flags)
        critic_text = f"{status} ({flags} flags)"

    fields = [
        {"type": "mrkdwn", "text": f"*Arb ID:* `{entry.arb_id[:12]}...`"},
        {"type": "mrkdwn", "text": f"*Spread:* {spread_str}"},
        {"type": "mrkdwn", "text": f"*Size:* {size_str}"},
        {"type": "mrkdwn", "text": f"*Critic:* {critic_text}"},
    ]

    if entry.duration_ms is not None:
        fields.append(
            {"type": "mrkdwn", "text": f"*Duration:* {entry.duration_ms}ms"},
        )
    if entry.source:
        fields.append(
            {"type": "mrkdwn", "text": f"*Source:* {entry.source}"},
        )

    return {
        "text": f"{emoji} {header}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{emoji} {header}"},
            },
            {"type": "section", "fields": fields},
        ],
    }


def build_auto_exec_discord_payload(entry: AutoExecLogEntry) -> dict[str, Any]:
    """Build Discord embed payload for an auto-execution event.

    Args:
        entry: Auto-execution log entry.

    Returns:
        Discord webhook payload dict.
    """
    emoji = _STATUS_EMOJI.get(entry.status, ":robot_face:")
    header = f"Auto-Exec: {entry.status.replace('_', ' ').title()}"
    color = _STATUS_COLOR.get(entry.status, 9807270)
    size_str = f"${float(entry.size_usd):.2f}" if entry.size_usd else "N/A"
    spread_str = f"{float(entry.trigger_spread_pct):.4%}" if entry.trigger_spread_pct else "N/A"

    fields = [
        {"name": "Arb ID", "value": f"`{entry.arb_id[:12]}...`", "inline": True},
        {"name": "Spread", "value": spread_str, "inline": True},
        {"name": "Size", "value": size_str, "inline": True},
        {"name": "Source", "value": entry.source or "unknown", "inline": True},
    ]

    if entry.duration_ms is not None:
        fields.append(
            {"name": "Duration", "value": f"{entry.duration_ms}ms", "inline": True},
        )

    return {
        "content": f"{emoji} {header}",
        "embeds": [
            {
                "title": header,
                "color": color,
                "fields": fields,
            },
        ],
    }


def build_breaker_slack_payload(
    breaker_type: str,
    reason: str,
) -> dict[str, Any]:
    """Build Slack payload for a circuit breaker trip.

    Args:
        breaker_type: Type of breaker (loss, failure, anomaly).
        reason: Trip reason.

    Returns:
        Slack webhook payload dict.
    """
    return {
        "text": f":rotating_light: Circuit Breaker Tripped: {breaker_type}",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f":rotating_light: Circuit Breaker: {breaker_type.upper()}",
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Reason:* {reason}"},
            },
        ],
    }


def build_breaker_discord_payload(
    breaker_type: str,
    reason: str,
) -> dict[str, Any]:
    """Build Discord payload for a circuit breaker trip.

    Args:
        breaker_type: Type of breaker.
        reason: Trip reason.

    Returns:
        Discord webhook payload dict.
    """
    return {
        "content": f":rotating_light: Circuit Breaker Tripped: {breaker_type}",
        "embeds": [
            {
                "title": f"Circuit Breaker: {breaker_type.upper()}",
                "color": 15158332,
                "fields": [
                    {"name": "Reason", "value": reason, "inline": False},
                ],
            },
        ],
    }


async def dispatch_auto_exec_alert(
    entry: AutoExecLogEntry,
    *,
    slack_url: str = "",
    discord_url: str = "",
    client: httpx.AsyncClient | None = None,
) -> None:
    """Dispatch webhook notification for an auto-execution event.

    Args:
        entry: Auto-execution log entry.
        slack_url: Slack webhook URL.
        discord_url: Discord webhook URL.
        client: Optional shared HTTP client.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient()
    try:
        if slack_url:
            payload = build_auto_exec_slack_payload(entry)
            await _safe_send(slack_url, payload, http, "slack")
        if discord_url:
            payload = build_auto_exec_discord_payload(entry)
            await _safe_send(discord_url, payload, http, "discord")
    finally:
        if owns_client:
            await http.aclose()


async def dispatch_breaker_alert(
    breaker_type: str,
    reason: str,
    *,
    slack_url: str = "",
    discord_url: str = "",
    client: httpx.AsyncClient | None = None,
) -> None:
    """Dispatch webhook notification for a circuit breaker trip.

    Args:
        breaker_type: Type of breaker (loss, failure, anomaly).
        reason: Trip reason.
        slack_url: Slack webhook URL.
        discord_url: Discord webhook URL.
        client: Optional shared HTTP client.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient()
    try:
        if slack_url:
            payload = build_breaker_slack_payload(breaker_type, reason)
            await _safe_send(slack_url, payload, http, "slack")
        if discord_url:
            payload = build_breaker_discord_payload(breaker_type, reason)
            await _safe_send(discord_url, payload, http, "discord")
    finally:
        if owns_client:
            await http.aclose()


async def _safe_send(
    url: str,
    payload: dict[str, Any],
    client: httpx.AsyncClient,
    target: str,
) -> None:
    """Send webhook, swallowing errors.

    Args:
        url: Webhook endpoint.
        payload: JSON body.
        client: HTTP client.
        target: Label for logging.
    """
    try:
        await _post_webhook(url, payload, client)
        logger.info("auto_exec_webhook_sent", target=target)
    except Exception:
        logger.exception("auto_exec_webhook_failed", target=target)
