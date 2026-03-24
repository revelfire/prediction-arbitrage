"""Webhook notifications for actual trade executions (buy/sell)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import structlog

from arb_scanner.notifications.action_links import (
    dashboard_position_url,
    exit_action_url,
)
from arb_scanner.notifications.webhook import _post_webhook

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="notifications.trade_webhook",
)


def build_trade_slack_payload(
    *,
    action: str,
    market_title: str,
    side: str,
    size_contracts: int,
    price: Decimal,
    arb_id: str,
    pnl: Decimal | None = None,
    dashboard_url: str = "",
    auth_token: str = "",
) -> dict[str, Any]:
    """Build Slack Block Kit payload for a trade execution.

    Args:
        action: 'buy', 'sell', or 'closed'.
        market_title: Human-readable market name.
        side: 'yes' or 'no'.
        size_contracts: Number of contracts.
        price: Execution price.
        arb_id: Execution ticket ID.
        pnl: Realized P&L (for closed trades).
        dashboard_url: Dashboard base URL for action links.
        auth_token: Auth token for API links.

    Returns:
        Slack webhook payload dict.
    """
    if action == "buy":
        emoji = ":arrow_up:"
        header = f":white_check_mark: {emoji} BUY EXECUTED"
    elif action == "sell":
        emoji = ":arrow_down:"
        header = f":white_check_mark: {emoji} SELL SUBMITTED"
    else:
        pnl_val = float(pnl) if pnl is not None else 0
        emoji = ":moneybag:" if pnl_val >= 0 else ":x:"
        header = f"{emoji} POSITION CLOSED"

    price_str = f"${float(price):.2f}"
    title = market_title[:60] if market_title else arb_id[:12]

    fields: list[dict[str, str]] = [
        {"type": "mrkdwn", "text": f"*Market:* {title}"},
        {"type": "mrkdwn", "text": f"*Side:* {side.upper()}"},
        {"type": "mrkdwn", "text": f"*Size:* {size_contracts} ct"},
        {"type": "mrkdwn", "text": f"*Price:* {price_str}"},
    ]
    if pnl is not None:
        pnl_str = f"${float(pnl):+.2f}"
        fields.append({"type": "mrkdwn", "text": f"*P&L:* {pnl_str}"})

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header},
        },
        {"type": "section", "fields": fields},
    ]

    links = _build_link_elements(action, arb_id, dashboard_url, auth_token)
    if links:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": " | ".join(links)}],
            },
        )

    return {"text": f"{header} — {title}", "blocks": blocks}


def _build_link_elements(
    action: str,
    arb_id: str,
    dashboard_url: str,
    auth_token: str,
) -> list[str]:
    """Build markdown link strings for Slack context block."""
    links: list[str] = []
    pos_url = dashboard_position_url(dashboard_url, arb_id, auth_token)
    if pos_url:
        links.append(f"<{pos_url}|View Position>")
    if action == "buy":
        ex_url = exit_action_url(dashboard_url, arb_id, auth_token)
        if ex_url:
            links.append(f"<{ex_url}|Exit Now>")
    return links


async def dispatch_trade_alert(
    *,
    action: str,
    market_title: str,
    side: str,
    size_contracts: int,
    price: Decimal,
    arb_id: str,
    pnl: Decimal | None = None,
    slack_url: str = "",
    dashboard_url: str = "",
    auth_token: str = "",
    client: httpx.AsyncClient | None = None,
) -> None:
    """Dispatch a trade execution notification to Slack.

    Args:
        action: 'buy', 'sell', or 'closed'.
        market_title: Human-readable market name.
        side: 'yes' or 'no'.
        size_contracts: Number of contracts.
        price: Execution price.
        arb_id: Execution ticket ID.
        pnl: Realized P&L (for closed trades).
        slack_url: Slack webhook URL.
        dashboard_url: Dashboard base URL.
        auth_token: Dashboard auth token.
        client: Optional shared HTTP client.
    """
    if not slack_url:
        return
    payload = build_trade_slack_payload(
        action=action,
        market_title=market_title,
        side=side,
        size_contracts=size_contracts,
        price=price,
        arb_id=arb_id,
        pnl=pnl,
        dashboard_url=dashboard_url,
        auth_token=auth_token,
    )
    owns_client = client is None
    http = client or httpx.AsyncClient()
    try:
        await _post_webhook(slack_url, payload, http)
        logger.info("trade_webhook_sent", action=action, market=market_title[:30])
    except Exception:
        logger.exception("trade_webhook_failed", action=action)
    finally:
        if owns_client:
            await http.aclose()
