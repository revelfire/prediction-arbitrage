"""URL builders for Slack action links in notifications."""

from __future__ import annotations

from urllib.parse import urlencode


def dashboard_position_url(
    base_url: str,
    arb_id: str,
    token: str = "",
) -> str:
    """Build a dashboard link to view a position.

    Args:
        base_url: Dashboard base URL (e.g. https://arb.spillwave.com).
        arb_id: Execution ticket / arb identifier.
        token: Optional auth token appended as query param.

    Returns:
        Full URL string, or empty string if base_url is not set.
    """
    if not base_url:
        return ""
    url = f"{base_url.rstrip('/')}/#positions/{arb_id}"
    if token:
        url += f"?{urlencode({'token': token})}"
    return url


def exit_action_url(
    base_url: str,
    arb_id: str,
    token: str = "",
) -> str:
    """Build a direct API link to trigger an exit sell order.

    Args:
        base_url: Dashboard base URL.
        arb_id: Execution ticket / arb identifier.
        token: Auth token (required for the API call to succeed).

    Returns:
        Full URL string, or empty string if base_url is not set.
    """
    if not base_url:
        return ""
    url = f"{base_url.rstrip('/')}/api/execution/flip-exit/{arb_id}"
    if token:
        url += f"?{urlencode({'token': token})}"
    return url


def polymarket_market_url(slug: str) -> str:
    """Build a Polymarket market page URL from a slug.

    Args:
        slug: Polymarket market slug.

    Returns:
        Full URL string, or empty string if slug is empty.
    """
    if not slug:
        return ""
    return f"https://polymarket.com/event/{slug}"
