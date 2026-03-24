"""Tests for WebSocket telemetry and message classification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from arb_scanner.flippening.ws_telemetry import WsTelemetry, classify_ws_message


class TestClassifyWsMessage:
    """Tests for classify_ws_message()."""

    def test_heartbeat_type(self) -> None:
        assert classify_ws_message({"type": "heartbeat"}) == "heartbeat"

    def test_ping_type(self) -> None:
        assert classify_ws_message({"type": "ping"}) == "heartbeat"

    def test_subscribe_ack(self) -> None:
        assert classify_ws_message({"type": "subscribe"}) == "subscription_ack"

    def test_subscribed_ack(self) -> None:
        assert classify_ws_message({"type": "subscribed"}) == "subscription_ack"

    def test_error_type(self) -> None:
        assert classify_ws_message({"type": "error", "message": "bad"}) == "error"

    def test_price_update_book(self) -> None:
        msg = {"event_type": "book", "market": "0x1", "asset_id": "tok1", "bids": [], "asks": []}
        assert classify_ws_message(msg) == "price_update"

    def test_price_update_price_change(self) -> None:
        msg = {"event_type": "price_change", "market": "0x1", "asset_id": "tok1", "price": "0.65"}
        assert classify_ws_message(msg) == "price_update"

    def test_price_update_last_trade(self) -> None:
        msg = {"event_type": "last_trade_price", "asset_id": "tok1", "price": "0.5"}
        assert classify_ws_message(msg) == "price_update"

    def test_price_update_best_bid_ask(self) -> None:
        msg = {"event_type": "best_bid_ask", "market": "0x1", "asset_id": "tok1"}
        assert classify_ws_message(msg) == "price_update"

    def test_unknown(self) -> None:
        assert classify_ws_message({"foo": "bar"}) == "unknown"

    def test_unknown_event_type(self) -> None:
        assert classify_ws_message({"event_type": "something_new"}) == "unknown"

    def test_case_insensitive_type(self) -> None:
        assert classify_ws_message({"type": "HEARTBEAT"}) == "heartbeat"


class TestWsTelemetryCounters:
    """Tests for WsTelemetry counter increments."""

    def test_record_parsed(self) -> None:
        t = WsTelemetry()
        t.record_parsed()
        assert t.received == 1
        assert t.parsed_ok == 1
        assert t.cum_received == 1
        assert t.cum_parsed_ok == 1

    def test_record_failed(self) -> None:
        t = WsTelemetry()
        t.record_failed("missing_price")
        assert t.parse_failed == 1
        assert t.cum_parse_failed == 1
        assert t._failure_reasons == {"missing_price": 1}

    def test_record_failed_multiple_reasons(self) -> None:
        t = WsTelemetry()
        t.record_failed("missing_price")
        t.record_failed("missing_market_id")
        t.record_failed("missing_price")
        assert t._failure_reasons == {"missing_price": 2, "missing_market_id": 1}

    def test_record_ignored(self) -> None:
        t = WsTelemetry()
        t.record_ignored()
        assert t.ignored == 1
        assert t.cum_ignored == 1


class TestWsTelemetrySnapshot:
    """Tests for snapshot()."""

    def test_snapshot_empty(self) -> None:
        t = WsTelemetry()
        s = t.snapshot()
        assert s["received"] == 0
        assert s["parse_success_rate"] == 0.0
        assert s["schema_match_rate"] == 1.0

    def test_snapshot_with_data(self) -> None:
        t = WsTelemetry()
        t.record_parsed()
        t.record_parsed()
        t.record_failed("bad")
        s = t.snapshot()
        assert s["cum_received"] == 3
        assert s["cum_parsed_ok"] == 2
        assert s["parse_success_rate"] == round(2 / 3 * 100, 2)
        assert s["failure_reasons"] == {"bad": 1}


class TestWsTelemetryRollingReset:
    """Tests for reset_rolling()."""

    def test_reset_zeros_rolling_keeps_cumulative(self) -> None:
        t = WsTelemetry()
        t.record_parsed()
        t.record_failed("x")
        t.record_ignored()
        t.reset_rolling()
        assert t.received == 0
        assert t.parsed_ok == 0
        assert t.parse_failed == 0
        assert t.ignored == 0
        assert t._failure_reasons == {}
        # cumulative preserved
        assert t.cum_received == 3
        assert t.cum_parsed_ok == 1


class TestWsTelemetryShouldLog:
    """Tests for should_log() timing."""

    def test_should_log_false_when_recent(self) -> None:
        t = WsTelemetry()
        assert t.should_log(60) is False

    def test_should_log_true_after_interval(self) -> None:
        t = WsTelemetry()
        t._last_log_time = datetime.now(tz=UTC) - timedelta(seconds=120)
        t.record_parsed()
        assert t.should_log(60) is True
        # rolling should be reset after log
        assert t.received == 0

    def test_should_log_resets_rolling(self) -> None:
        t = WsTelemetry()
        t._last_log_time = datetime.now(tz=UTC) - timedelta(seconds=70)
        t.record_failed("test")
        t.should_log(60)
        assert t.parse_failed == 0
        assert t.cum_parse_failed == 1


class TestWsTelemetrySchemas:
    """Tests for schema drift detection."""

    def test_record_new_schema_logged(self) -> None:
        t = WsTelemetry()
        keys = frozenset({"market", "asset_id", "price"})
        t.record_schema(keys)
        assert keys in t.known_schemas
        assert t._schema_total_count == 1
        assert t._schema_match_count == 1

    def test_schema_match_price_changes_field(self) -> None:
        t = WsTelemetry()
        # price_change schema has price_changes + market (no top-level asset_id)
        keys = frozenset({"event_type", "market", "price_changes", "timestamp"})
        t.record_schema(keys)
        assert t._schema_match_count == 1

    def test_schema_match_rate_with_mixed(self) -> None:
        t = WsTelemetry()
        # matching schema
        t.record_schema(frozenset({"market", "asset_id", "price"}))
        # non-matching schema (no market/condition_id)
        t.record_schema(frozenset({"foo", "bar"}))
        assert t.schema_match_rate == 0.5

    def test_check_drift_below_threshold(self) -> None:
        t = WsTelemetry()
        t.record_schema(frozenset({"foo"}))
        t.record_schema(frozenset({"bar"}))
        assert t.check_drift(0.5) is True

    def test_check_drift_above_threshold(self) -> None:
        t = WsTelemetry()
        t.record_schema(frozenset({"market", "asset_id", "price"}))
        assert t.check_drift(0.5) is False

    def test_reset_clears_schema_counters(self) -> None:
        t = WsTelemetry()
        t.record_schema(frozenset({"market", "asset_id", "price"}))
        t.reset_rolling()
        assert t._schema_match_count == 0
        assert t._schema_total_count == 0
        # known_schemas persists
        assert len(t.known_schemas) == 1

    def test_duplicate_schema_not_readded(self) -> None:
        t = WsTelemetry()
        keys = frozenset({"market", "asset_id", "price"})
        t.record_schema(keys)
        t.record_schema(keys)
        assert len(t.known_schemas) == 1
        assert t._schema_total_count == 2
