"""Async webhook dispatcher for Slack and Discord notifications."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.market import Venue
from arb_scanner.utils.retry import async_retry

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="notifications.webhook")


def build_slack_payload(opp: ArbOpportunity) -> dict[str, Any]:
    """Build a Slack Block Kit payload for an arbitrage opportunity.

    Args:
        opp: The detected arbitrage opportunity.

    Returns:
        Dict matching the Slack webhook JSON contract.
    """
    buy_label, sell_label = _format_leg_labels(opp)
    spread = f"{float(opp.net_spread_pct):.2%}"
    ann = f"{float(opp.annualized_return):.0%}" if opp.annualized_return else "N/A"
    title = opp.poly_market.title
    return {
        "text": f'Arb Alert: {spread} spread on "{title}"',
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Arbitrage Opportunity Detected"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Buy:* {buy_label}"},
                    {"type": "mrkdwn", "text": f"*Sell:* {sell_label}"},
                    {"type": "mrkdwn", "text": f"*Net Spread:* {spread}"},
                    {"type": "mrkdwn", "text": f"*Max Size:* ${float(opp.max_size):.0f}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Match Confidence:* {opp.match.match_confidence:.0%}",
                    },
                    {"type": "mrkdwn", "text": f"*Annualized:* {ann}"},
                ],
            },
        ],
    }


def build_discord_payload(opp: ArbOpportunity) -> dict[str, Any]:
    """Build a Discord embed payload for an arbitrage opportunity.

    Args:
        opp: The detected arbitrage opportunity.

    Returns:
        Dict matching the Discord webhook JSON contract.
    """
    buy_label, sell_label = _format_leg_labels(opp)
    spread = f"{float(opp.net_spread_pct):.2%}"
    ann = f"{float(opp.annualized_return):.0%}" if opp.annualized_return else "N/A"
    return {
        "content": f"Arb Alert: {spread} spread detected",
        "embeds": [
            {
                "title": "Arbitrage Opportunity",
                "color": 3066993,
                "fields": [
                    {"name": "Buy", "value": buy_label, "inline": True},
                    {"name": "Sell", "value": sell_label, "inline": True},
                    {"name": "Net Spread", "value": spread, "inline": True},
                    {"name": "Max Size", "value": f"${float(opp.max_size):.0f}", "inline": True},
                    {
                        "name": "Match Confidence",
                        "value": f"{opp.match.match_confidence:.0%}",
                        "inline": True,
                    },
                    {"name": "Annualized Return", "value": ann, "inline": True},
                ],
            },
        ],
    }


def _format_leg_labels(opp: ArbOpportunity) -> tuple[str, str]:
    """Format buy/sell leg labels with venue, side, and price.

    Args:
        opp: The arbitrage opportunity.

    Returns:
        Tuple of (buy_label, sell_label).
    """
    if opp.buy_venue == Venue.POLYMARKET:
        buy_price = float(opp.poly_market.yes_ask)
        sell_price = float(opp.kalshi_market.no_ask)
    else:
        buy_price = float(opp.kalshi_market.yes_ask)
        sell_price = float(opp.poly_market.no_ask)
    buy_label = f"YES on {opp.buy_venue.value.capitalize()} @ ${buy_price:.2f}"
    sell_label = f"NO on {opp.sell_venue.value.capitalize()} @ ${sell_price:.2f}"
    return buy_label, sell_label


@async_retry(max_retries=3, base_delay=1.0)
async def _post_webhook(url: str, payload: dict[str, Any], client: httpx.AsyncClient) -> None:
    """POST a JSON payload to a webhook URL with retry.

    Args:
        url: The webhook endpoint URL.
        payload: The JSON body to send.
        client: Shared httpx async client.

    Raises:
        httpx.HTTPStatusError: If the request fails after retries.
    """
    resp = await client.post(url, json=payload, timeout=10.0)
    resp.raise_for_status()


async def dispatch_webhook(
    opp: ArbOpportunity,
    *,
    slack_url: str = "",
    discord_url: str = "",
    client: httpx.AsyncClient | None = None,
) -> None:
    """Fire-and-forget webhook dispatch to Slack and/or Discord.

    Logs failures but never raises -- safe to call in a scan loop.

    Args:
        opp: The arbitrage opportunity to notify about.
        slack_url: Slack incoming webhook URL (empty to skip).
        discord_url: Discord incoming webhook URL (empty to skip).
        client: Optional shared httpx client (created if not provided).
    """
    owns_client = client is None
    http = client or httpx.AsyncClient()
    try:
        if slack_url:
            await _send_safe(slack_url, build_slack_payload(opp), http, "slack")
        if discord_url:
            await _send_safe(discord_url, build_discord_payload(opp), http, "discord")
    finally:
        if owns_client:
            await http.aclose()


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
        logger.info("webhook_sent", target=target)
    except Exception:
        logger.exception("webhook_failed", target=target)
