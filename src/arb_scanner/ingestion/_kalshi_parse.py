"""Kalshi API response parsing helpers.

Extracts and normalises market data from raw Kalshi API responses.
Uses ONLY ``*_dollars`` fields (4-decimal strings) per API guidelines.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import structlog

from arb_scanner.models.market import Market, Venue

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="ingestion.kalshi")


def safe_decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    """Convert *value* to :class:`Decimal`, returning *default* on failure.

    Args:
        value: Raw value to convert.
        default: Fallback decimal.

    Returns:
        Parsed Decimal.
    """
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def clamp(value: Decimal) -> Decimal:
    """Clamp *value* into [0, 1].

    Args:
        value: Decimal to clamp.

    Returns:
        Clamped Decimal.
    """
    return max(Decimal("0"), min(Decimal("1"), value))


def parse_market(raw: dict[str, object]) -> Market | None:
    """Parse a single Kalshi API market dict into a :class:`Market`.

    Uses ONLY ``*_dollars`` fields (4-decimal strings).

    Args:
        raw: Single market dict from the ``/markets`` response.

    Returns:
        A validated :class:`Market`, or *None* on missing data.
    """
    try:
        ticker = str(raw.get("ticker", ""))
        title = str(raw.get("title", ""))
        if not ticker or not title:
            return None

        yes_bid = clamp(safe_decimal(raw.get("yes_bid_dollars")))
        yes_ask = clamp(safe_decimal(raw.get("yes_ask_dollars")))
        no_bid = clamp(safe_decimal(raw.get("no_bid_dollars")))
        no_ask = clamp(safe_decimal(raw.get("no_ask_dollars")))

        if yes_bid > yes_ask:
            yes_bid, yes_ask = yes_ask, yes_bid
        if no_bid > no_ask:
            no_bid, no_ask = no_ask, no_bid

        rules = _build_rules(raw)
        expiry = parse_expiry(raw.get("expiration_time"))
        volume_raw = raw.get("volume_dollars_24h_fp") or raw.get("volume_fp", "0")
        volume = safe_decimal(volume_raw)

        return Market(
            venue=Venue.KALSHI,
            event_id=ticker,
            title=title,
            description=str(raw.get("subtitle", "") or ""),
            resolution_criteria=rules,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            volume_24h=volume,
            expiry=expiry,
            fees_pct=Decimal("0.07"),
            fee_model="per_contract",
            last_updated=datetime.now(tz=UTC),
            raw_data=raw,
        )
    except Exception:
        logger.warning("kalshi_parse_error", raw_keys=list(raw.keys()))
        return None


def _build_rules(raw: dict[str, object]) -> str:
    """Combine ``rules_primary`` and ``rules_secondary`` fields.

    Args:
        raw: Market dict.

    Returns:
        Combined resolution criteria string.
    """
    primary = str(raw.get("rules_primary", "") or "")
    secondary = str(raw.get("rules_secondary", "") or "")
    parts = [p for p in (primary, secondary) if p]
    return " ".join(parts)


def parse_expiry(value: object) -> datetime | None:
    """Parse an ISO-format expiration timestamp.

    Args:
        value: Raw datetime string.

    Returns:
        Parsed datetime or *None*.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def process_orderbook(data: dict[str, object]) -> dict[str, object]:
    """Process Kalshi's bids-only order book into a richer structure.

    Kalshi returns only bids, sorted ascending (best bid = last element).
    Asks are computed: ``YES_ask = 1.00 - highest_NO_bid``.

    Args:
        data: Raw order-book response.

    Returns:
        Processed order-book dict.
    """
    raw_yes = data.get("yes", [])
    raw_no = data.get("no", [])
    yes_bids: list[list[object]] = raw_yes if isinstance(raw_yes, list) else []
    no_bids: list[list[object]] = raw_no if isinstance(raw_no, list) else []

    yes_best = safe_decimal(yes_bids[-1][0]) if yes_bids else Decimal("0")
    no_best = safe_decimal(no_bids[-1][0]) if no_bids else Decimal("0")

    yes_ask = clamp(Decimal("1") - no_best)
    no_ask = clamp(Decimal("1") - yes_best)

    return {
        "yes_bids": yes_bids,
        "no_bids": no_bids,
        "yes_best_bid": str(yes_best),
        "no_best_bid": str(no_best),
        "yes_ask": str(yes_ask),
        "no_ask": str(no_ask),
    }
