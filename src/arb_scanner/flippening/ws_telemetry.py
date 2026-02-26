"""WebSocket telemetry, message classification, and schema drift detection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.ws_telemetry",
)

_PRICE_UPDATE_KEYS = frozenset({"market", "condition_id", "asset_id", "price"})
_EXPECTED_FIELDS = frozenset({"asset_id", "price"})
_MARKET_FIELDS = frozenset({"market", "condition_id"})


def classify_ws_message(data: dict[str, object]) -> str:
    """Classify a parsed WebSocket message by type.

    Args:
        data: Parsed JSON dict from WebSocket.

    Returns:
        One of: heartbeat, subscription_ack, error, price_update, unknown.
    """
    msg_type = str(data.get("type", "")).lower()
    if msg_type in ("heartbeat", "ping"):
        return "heartbeat"
    if msg_type in ("subscribe", "subscribed"):
        return "subscription_ack"
    if msg_type == "error":
        return "error"
    if "price" in data or "asset_id" in data:
        return "price_update"
    return "unknown"


class WsTelemetry:
    """Rolling and cumulative WebSocket message telemetry."""

    def __init__(self) -> None:
        """Initialise telemetry counters."""
        self.received: int = 0
        self.parsed_ok: int = 0
        self.parse_failed: int = 0
        self.ignored: int = 0

        self.cum_received: int = 0
        self.cum_parsed_ok: int = 0
        self.cum_parse_failed: int = 0
        self.cum_ignored: int = 0

        self._failure_reasons: dict[str, int] = {}
        self._last_log_time: datetime = datetime.now(tz=UTC)

        self.known_schemas: set[frozenset[str]] = set()
        self._schema_match_count: int = 0
        self._schema_total_count: int = 0

    def record_parsed(self) -> None:
        """Record a successfully parsed message."""
        self.received += 1
        self.parsed_ok += 1
        self.cum_received += 1
        self.cum_parsed_ok += 1

    def record_failed(self, reason: str) -> None:
        """Record a parse failure with reason.

        Args:
            reason: Failure reason (e.g. missing_market_id).
        """
        self.received += 1
        self.parse_failed += 1
        self.cum_received += 1
        self.cum_parse_failed += 1
        self._failure_reasons[reason] = self._failure_reasons.get(reason, 0) + 1

    def record_ignored(self) -> None:
        """Record an ignored message (heartbeat, ack, etc.)."""
        self.received += 1
        self.ignored += 1
        self.cum_received += 1
        self.cum_ignored += 1

    def record_schema(self, keys: frozenset[str]) -> None:
        """Record a message schema for drift detection.

        Args:
            keys: Top-level keys from the parsed JSON message.
        """
        is_new = keys not in self.known_schemas
        if is_new:
            self.known_schemas.add(keys)
            logger.info("ws_new_schema_variant", keys=sorted(keys))

        self._schema_total_count += 1
        has_market = bool(keys & _MARKET_FIELDS)
        has_expected = bool(keys & _EXPECTED_FIELDS)
        if has_market and has_expected:
            self._schema_match_count += 1

    @property
    def schema_match_rate(self) -> float:
        """Ratio of schema-matching messages to total."""
        if self._schema_total_count == 0:
            return 1.0
        return self._schema_match_count / self._schema_total_count

    def check_drift(self, threshold: float) -> bool:
        """Check if schema match rate has dropped below threshold.

        Args:
            threshold: Minimum acceptable schema match rate.

        Returns:
            True if drifted below threshold.
        """
        return self.schema_match_rate < threshold

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of all telemetry counters.

        Returns:
            Dict with rolling, cumulative, and rate data.
        """
        total = self.cum_received
        parse_rate = (self.cum_parsed_ok / total * 100) if total > 0 else 0.0
        return {
            "received": self.received,
            "parsed_ok": self.parsed_ok,
            "parse_failed": self.parse_failed,
            "ignored": self.ignored,
            "cum_received": self.cum_received,
            "cum_parsed_ok": self.cum_parsed_ok,
            "cum_parse_failed": self.cum_parse_failed,
            "cum_ignored": self.cum_ignored,
            "parse_success_rate": round(parse_rate, 2),
            "schema_match_rate": round(self.schema_match_rate, 4),
            "failure_reasons": dict(self._failure_reasons),
        }

    def should_log(self, interval_seconds: int) -> bool:
        """Check if enough time has elapsed to log telemetry.

        Args:
            interval_seconds: Minimum seconds between logs.

        Returns:
            True if we should log now.
        """
        now = datetime.now(tz=UTC)
        elapsed = (now - self._last_log_time).total_seconds()
        if elapsed >= interval_seconds:
            logger.info(
                "ws_telemetry",
                received=self.received,
                parsed_ok=self.parsed_ok,
                parse_failed=self.parse_failed,
                ignored=self.ignored,
                failure_reasons=dict(self._failure_reasons),
                schema_match_rate=round(self.schema_match_rate, 4),
            )
            self.reset_rolling()
            self._last_log_time = now
            return True
        return False

    def reset_rolling(self) -> None:
        """Zero rolling counters and failure reasons."""
        self.received = 0
        self.parsed_ok = 0
        self.parse_failed = 0
        self.ignored = 0
        self._failure_reasons.clear()
        self._schema_match_count = 0
        self._schema_total_count = 0
