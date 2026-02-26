"""WebSocket message parsing for Polymarket CLOB market channel.

Handles event types: book (orderbook snapshot), price_change (nested
price_changes array), last_trade_price, best_bid_ask. Also handles
PONG heartbeats and JSON arrays.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import structlog

from arb_scanner.flippening.ws_telemetry import WsTelemetry, classify_ws_message
from arb_scanner.models.flippening import PriceUpdate

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.ws_parser",
)


def parse_ws_message(
    raw: str | bytes,
    telemetry: WsTelemetry | None = None,
) -> PriceUpdate | None:
    """Parse a WebSocket message into a PriceUpdate.

    Args:
        raw: Raw WebSocket message data.
        telemetry: Optional telemetry tracker.

    Returns:
        PriceUpdate or None if unparseable.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    text = raw.strip()
    if text.upper() in ("PONG", "PING", ""):
        if telemetry:
            telemetry.record_ignored()
        return None

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        if telemetry:
            telemetry.record_ignored()
        return None

    if isinstance(data, list):
        return _parse_first_event(data, telemetry)
    if not isinstance(data, dict):
        if telemetry:
            telemetry.record_ignored()
        return None
    return _parse_event_dict(data, telemetry)


def _parse_first_event(
    events: list[object],
    telemetry: WsTelemetry | None,
) -> PriceUpdate | None:
    """Parse the first usable event from a JSON array."""
    for item in events:
        if isinstance(item, dict):
            result = _parse_event_dict(item, telemetry)
            if result is not None:
                return result
    if telemetry:
        telemetry.record_ignored()
    return None


def _parse_event_dict(
    data: dict[str, object],
    telemetry: WsTelemetry | None,
) -> PriceUpdate | None:
    """Parse a single event dict into a PriceUpdate."""
    if telemetry:
        telemetry.record_schema(frozenset(data.keys()))

    msg_type = classify_ws_message(data)
    if msg_type != "price_update":
        if msg_type == "error":
            logger.warning("ws_error_message", data=str(data)[:500])
        if telemetry:
            telemetry.record_ignored()
        return None

    event_type = str(data.get("event_type", ""))
    market_id = str(data.get("market", ""))

    if event_type == "book":
        return _parse_book_event(data, market_id, telemetry)
    if event_type == "price_change":
        return _parse_price_change(data, market_id, telemetry)
    if event_type == "best_bid_ask":
        return _parse_best_bid_ask(data, market_id, telemetry)
    if event_type == "last_trade_price":
        return _parse_last_trade(data, market_id, telemetry)

    if telemetry:
        telemetry.record_failed("unhandled_event_type")
    return None


def _parse_book_event(
    data: dict[str, object],
    market_id: str,
    telemetry: WsTelemetry | None,
) -> PriceUpdate | None:
    """Extract best bid/ask from a book snapshot event.

    Book format: {event_type, asset_id, market, bids, asks, timestamp, hash}
    """
    token_id = str(data.get("asset_id", ""))
    if not token_id:
        if telemetry:
            telemetry.record_failed("missing_asset_id")
        return None

    bids = data.get("bids", [])
    asks = data.get("asks", [])
    if not isinstance(bids, list) or not isinstance(asks, list):
        if telemetry:
            telemetry.record_failed("invalid_book_format")
        return None

    yes_bid = Decimal("0")
    yes_ask = Decimal("1")
    if bids:
        best = bids[-1] if isinstance(bids[-1], dict) else {}
        yes_bid = _safe_dec(best.get("price")) or Decimal("0")
    if asks:
        best = asks[0] if isinstance(asks[0], dict) else {}
        yes_ask = _safe_dec(best.get("price")) or Decimal("1")

    result = PriceUpdate(
        market_id=market_id,
        token_id=token_id,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=max(Decimal("1") - yes_ask, Decimal("0")),
        no_ask=min(Decimal("1") - yes_bid, Decimal("1")),
        timestamp=datetime.now(tz=UTC),
    )
    if telemetry:
        telemetry.record_parsed()
    return result


def _parse_price_change(
    data: dict[str, object],
    market_id: str,
    telemetry: WsTelemetry | None,
) -> PriceUpdate | None:
    """Parse price_change event with nested price_changes array.

    Format: {event_type, market, timestamp,
             price_changes: [{asset_id, price, size, side,
                              best_bid, best_ask, hash}]}
    """
    changes = data.get("price_changes")
    if not isinstance(changes, list) or not changes:
        if telemetry:
            telemetry.record_failed("empty_price_changes")
        return None

    first = changes[0]
    if not isinstance(first, dict):
        if telemetry:
            telemetry.record_failed("invalid_price_change_entry")
        return None

    token_id = str(first.get("asset_id", ""))
    if not token_id:
        if telemetry:
            telemetry.record_failed("missing_asset_id")
        return None

    best_bid = _safe_dec(first.get("best_bid"))
    best_ask = _safe_dec(first.get("best_ask"))

    if best_bid is not None and best_ask is not None:
        result = PriceUpdate(
            market_id=market_id,
            token_id=token_id,
            yes_bid=best_bid,
            yes_ask=best_ask,
            no_bid=max(Decimal("1") - best_ask, Decimal("0")),
            no_ask=min(Decimal("1") - best_bid, Decimal("1")),
            timestamp=datetime.now(tz=UTC),
        )
        if telemetry:
            telemetry.record_parsed()
        return result

    price = _safe_dec(first.get("price"))
    if price is None or price < Decimal("0") or price > Decimal("1"):
        if telemetry:
            telemetry.record_failed("invalid_price")
        return None

    result = PriceUpdate(
        market_id=market_id,
        token_id=token_id,
        yes_bid=max(price - Decimal("0.01"), Decimal("0")),
        yes_ask=min(price + Decimal("0.01"), Decimal("1")),
        no_bid=max(Decimal("1") - price - Decimal("0.01"), Decimal("0")),
        no_ask=min(Decimal("1") - price + Decimal("0.01"), Decimal("1")),
        timestamp=datetime.now(tz=UTC),
        synthetic_spread=True,
    )
    if telemetry:
        telemetry.record_parsed()
    return result


def _parse_best_bid_ask(
    data: dict[str, object],
    market_id: str,
    telemetry: WsTelemetry | None,
) -> PriceUpdate | None:
    """Parse best_bid_ask event.

    Format: {event_type, market, asset_id, best_bid, best_ask,
             spread, timestamp}
    """
    token_id = str(data.get("asset_id", ""))
    if not token_id:
        if telemetry:
            telemetry.record_failed("missing_asset_id")
        return None

    best_bid = _safe_dec(data.get("best_bid"))
    best_ask = _safe_dec(data.get("best_ask"))
    if best_bid is None or best_ask is None:
        if telemetry:
            telemetry.record_failed("missing_best_bid_ask")
        return None

    result = PriceUpdate(
        market_id=market_id,
        token_id=token_id,
        yes_bid=best_bid,
        yes_ask=best_ask,
        no_bid=max(Decimal("1") - best_ask, Decimal("0")),
        no_ask=min(Decimal("1") - best_bid, Decimal("1")),
        timestamp=datetime.now(tz=UTC),
    )
    if telemetry:
        telemetry.record_parsed()
    return result


def _parse_last_trade(
    data: dict[str, object],
    market_id: str,
    telemetry: WsTelemetry | None,
) -> PriceUpdate | None:
    """Parse last_trade_price event into synthetic spread.

    Format: {event_type, asset_id, market, price, side, size,
             fee_rate_bps, timestamp}
    """
    token_id = str(data.get("asset_id", ""))
    if not token_id:
        if telemetry:
            telemetry.record_failed("missing_asset_id")
        return None

    price = _safe_dec(data.get("price"))
    if price is None or price < Decimal("0") or price > Decimal("1"):
        if telemetry:
            telemetry.record_failed("invalid_price")
        return None

    result = PriceUpdate(
        market_id=market_id,
        token_id=token_id,
        yes_bid=max(price - Decimal("0.01"), Decimal("0")),
        yes_ask=min(price + Decimal("0.01"), Decimal("1")),
        no_bid=max(Decimal("1") - price - Decimal("0.01"), Decimal("0")),
        no_ask=min(Decimal("1") - price + Decimal("0.01"), Decimal("1")),
        timestamp=datetime.now(tz=UTC),
        synthetic_spread=True,
    )
    if telemetry:
        telemetry.record_parsed()
    return result


def parse_orderbook(
    token_id: str,
    data: dict[str, object],
) -> PriceUpdate | None:
    """Parse a CLOB REST order book response into a PriceUpdate."""
    try:
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if not isinstance(bids, list) or not isinstance(asks, list):
            return None

        yes_bid = Decimal("0")
        yes_ask = Decimal("1")
        if bids:
            top_bid = bids[-1]
            if isinstance(top_bid, dict):
                yes_bid = _safe_dec(top_bid.get("price")) or Decimal("0")
        if asks:
            top_ask = asks[0]
            if isinstance(top_ask, dict):
                yes_ask = _safe_dec(top_ask.get("price")) or Decimal("1")

        return PriceUpdate(
            market_id="",
            token_id=token_id,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=max(Decimal("1") - yes_ask, Decimal("0")),
            no_ask=min(Decimal("1") - yes_bid, Decimal("1")),
            timestamp=datetime.now(tz=UTC),
        )
    except (KeyError, TypeError, IndexError):
        return None


def _safe_dec(value: object) -> Decimal | None:
    """Safely convert a value to Decimal."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
