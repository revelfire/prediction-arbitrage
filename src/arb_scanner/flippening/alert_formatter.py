"""Alert formatting and dispatch for flippening events."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from arb_scanner.models.flippening import (
    EntrySignal,
    ExitReason,
    ExitSignal,
    FlippeningEvent,
)
from arb_scanner.utils.retry import async_retry

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.alert_formatter",
)

_ENTRY_EMOJI = ":rotating_light:"
_EXIT_EMOJI_MAP: dict[ExitReason, str] = {
    ExitReason.REVERSION: ":moneybag:",
    ExitReason.STOP_LOSS: ":x:",
    ExitReason.TIMEOUT: ":hourglass:",
    ExitReason.RESOLUTION: ":checkered_flag:",
    ExitReason.DISCONNECT: ":electric_plug:",
}
_EXIT_COLOR_MAP: dict[ExitReason, int] = {
    ExitReason.REVERSION: 3066993,  # green
    ExitReason.STOP_LOSS: 15158332,  # red
    ExitReason.TIMEOUT: 9807270,  # gray
    ExitReason.RESOLUTION: 3447003,  # blue
    ExitReason.DISCONNECT: 16776960,  # yellow
}


def build_entry_slack_payload(
    event: FlippeningEvent,
    entry: EntrySignal,
) -> dict[str, Any]:
    """Build Slack Block Kit payload for a flippening entry alert.

    Args:
        event: Detected flippening event.
        entry: Generated entry signal.

    Returns:
        Slack webhook JSON payload.
    """
    title = f"{_ENTRY_EMOJI} Flippening Detected"
    return {
        "text": f"Flippening: {event.sport.upper()} spike on {event.market_id}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": title},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Sport:* {event.sport.upper()}"},
                    {"type": "mrkdwn", "text": f"*Baseline:* {float(event.baseline_yes):.2f}"},
                    {"type": "mrkdwn", "text": f"*Current:* {float(event.spike_price):.2f}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Spike:* {float(event.spike_magnitude_pct):.1%} ({event.spike_direction.value})",
                    },
                    {"type": "mrkdwn", "text": f"*Confidence:* {float(event.confidence):.0%}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Side:* {entry.side.upper()} @ {float(entry.entry_price):.2f}",
                    },
                    {"type": "mrkdwn", "text": f"*Target:* {float(entry.target_exit_price):.2f}"},
                    {"type": "mrkdwn", "text": f"*Size:* ${float(entry.suggested_size_usd):.0f}"},
                ],
            },
        ],
    }


def build_entry_discord_payload(
    event: FlippeningEvent,
    entry: EntrySignal,
) -> dict[str, Any]:
    """Build Discord embed payload for a flippening entry alert.

    Args:
        event: Detected flippening event.
        entry: Generated entry signal.

    Returns:
        Discord webhook JSON payload.
    """
    return {
        "content": f"Flippening: {event.sport.upper()} spike detected",
        "embeds": [
            {
                "title": "Flippening Detected",
                "color": 15105570,  # orange
                "fields": [
                    {"name": "Sport", "value": event.sport.upper(), "inline": True},
                    {
                        "name": "Baseline",
                        "value": f"{float(event.baseline_yes):.2f}",
                        "inline": True,
                    },
                    {"name": "Current", "value": f"{float(event.spike_price):.2f}", "inline": True},
                    {
                        "name": "Spike",
                        "value": f"{float(event.spike_magnitude_pct):.1%}",
                        "inline": True,
                    },
                    {
                        "name": "Confidence",
                        "value": f"{float(event.confidence):.0%}",
                        "inline": True,
                    },
                    {
                        "name": "Side",
                        "value": f"{entry.side.upper()} @ {float(entry.entry_price):.2f}",
                        "inline": True,
                    },
                    {
                        "name": "Target",
                        "value": f"{float(entry.target_exit_price):.2f}",
                        "inline": True,
                    },
                    {
                        "name": "Size",
                        "value": f"${float(entry.suggested_size_usd):.0f}",
                        "inline": True,
                    },
                ],
            },
        ],
    }


def build_exit_slack_payload(
    event: FlippeningEvent,
    entry: EntrySignal,
    exit_sig: ExitSignal,
) -> dict[str, Any]:
    """Build Slack Block Kit payload for a flippening exit alert.

    Args:
        event: Original flippening event.
        entry: Entry signal that was active.
        exit_sig: Exit signal with P&L.

    Returns:
        Slack webhook JSON payload.
    """
    emoji = _EXIT_EMOJI_MAP.get(exit_sig.exit_reason, ":question:")
    reason = exit_sig.exit_reason.value.replace("_", " ").title()
    header = f"{emoji} Flippening Exit — {reason}"
    pnl_str = f"${float(exit_sig.realized_pnl * entry.suggested_size_usd):.2f}"
    return {
        "text": f"Flippening exit: {reason} on {event.market_id}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Sport:* {event.sport.upper()}"},
                    {"type": "mrkdwn", "text": f"*Reason:* {reason}"},
                    {"type": "mrkdwn", "text": f"*Entry:* {float(entry.entry_price):.2f}"},
                    {"type": "mrkdwn", "text": f"*Exit:* {float(exit_sig.exit_price):.2f}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*P&L:* {pnl_str} ({float(exit_sig.realized_pnl_pct):.1%})",
                    },
                    {"type": "mrkdwn", "text": f"*Hold:* {float(exit_sig.hold_minutes):.0f} min"},
                ],
            },
        ],
    }


def build_exit_discord_payload(
    event: FlippeningEvent,
    entry: EntrySignal,
    exit_sig: ExitSignal,
) -> dict[str, Any]:
    """Build Discord embed payload for a flippening exit alert.

    Args:
        event: Original flippening event.
        entry: Entry signal that was active.
        exit_sig: Exit signal with P&L.

    Returns:
        Discord webhook JSON payload.
    """
    reason = exit_sig.exit_reason.value.replace("_", " ").title()
    color = _EXIT_COLOR_MAP.get(exit_sig.exit_reason, 9807270)
    pnl_str = f"${float(exit_sig.realized_pnl * entry.suggested_size_usd):.2f}"
    return {
        "content": f"Flippening exit: {reason}",
        "embeds": [
            {
                "title": f"Flippening Exit — {reason}",
                "color": color,
                "fields": [
                    {"name": "Sport", "value": event.sport.upper(), "inline": True},
                    {"name": "Entry", "value": f"{float(entry.entry_price):.2f}", "inline": True},
                    {"name": "Exit", "value": f"{float(exit_sig.exit_price):.2f}", "inline": True},
                    {
                        "name": "P&L",
                        "value": f"{pnl_str} ({float(exit_sig.realized_pnl_pct):.1%})",
                        "inline": True,
                    },
                    {
                        "name": "Hold",
                        "value": f"{float(exit_sig.hold_minutes):.0f} min",
                        "inline": True,
                    },
                ],
            },
        ],
    }


@async_retry(max_retries=3, base_delay=1.0)
async def _post_webhook(
    url: str,
    payload: dict[str, Any],
    client: httpx.AsyncClient,
) -> None:
    """POST a JSON payload to a webhook URL with retry.

    Args:
        url: The webhook endpoint URL.
        payload: The JSON body to send.
        client: Shared httpx async client.
    """
    resp = await client.post(url, json=payload, timeout=10.0)
    resp.raise_for_status()


async def dispatch_flip_alert(
    payload_slack: dict[str, Any] | None,
    payload_discord: dict[str, Any] | None,
    *,
    slack_url: str = "",
    discord_url: str = "",
    client: httpx.AsyncClient | None = None,
) -> None:
    """Fire-and-forget dispatch for flippening alerts.

    Args:
        payload_slack: Slack payload (None to skip).
        payload_discord: Discord payload (None to skip).
        slack_url: Slack incoming webhook URL.
        discord_url: Discord incoming webhook URL.
        client: Optional shared httpx client.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient()
    try:
        if slack_url and payload_slack:
            await _send_safe(slack_url, payload_slack, http, "slack")
        if discord_url and payload_discord:
            await _send_safe(discord_url, payload_discord, http, "discord")
    finally:
        if owns_client:
            await http.aclose()


async def _send_safe(
    url: str,
    payload: dict[str, Any],
    client: httpx.AsyncClient,
    target: str,
) -> None:
    """Send webhook, log and swallow exceptions.

    Args:
        url: Webhook endpoint URL.
        payload: JSON body.
        client: httpx async client.
        target: Label for logging.
    """
    try:
        await _post_webhook(url, payload, client)
        logger.info("flip_webhook_sent", target=target)
    except Exception:
        logger.exception("flip_webhook_failed", target=target)
