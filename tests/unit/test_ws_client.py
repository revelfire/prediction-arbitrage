"""Tests for WebSocket and polling price stream clients."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from arb_scanner.flippening.ws_client import (
    PollingPriceStream,
    WebSocketPriceStream,
    _parse_orderbook,
    _parse_ws_message,
    _safe_dec,
    create_price_stream,
)
from arb_scanner.models.config import FlippeningConfig


class TestParseWsMessage:
    """Tests for _parse_ws_message helper."""

    def test_valid_message(self) -> None:
        """Parses a valid WS message into PriceUpdate with synthetic_spread."""
        msg = json.dumps(
            {
                "market": "mkt-1",
                "asset_id": "tok-1",
                "price": "0.65",
            }
        )
        update = _parse_ws_message(msg)
        assert update is not None
        assert update.market_id == "mkt-1"
        assert update.token_id == "tok-1"
        assert update.yes_bid == Decimal("0.64")
        assert update.yes_ask == Decimal("0.66")
        assert update.synthetic_spread is True

    def test_missing_market_returns_none(self) -> None:
        """Message without market/condition_id returns None."""
        msg = json.dumps({"asset_id": "tok-1", "price": "0.5"})
        assert _parse_ws_message(msg) is None

    def test_missing_price_returns_none(self) -> None:
        """Message without price returns None."""
        msg = json.dumps({"market": "m1", "asset_id": "t1"})
        assert _parse_ws_message(msg) is None

    def test_invalid_json_returns_none(self) -> None:
        """Non-JSON message returns None."""
        assert _parse_ws_message("not json") is None

    def test_non_dict_returns_none(self) -> None:
        """JSON array returns None."""
        assert _parse_ws_message("[1, 2, 3]") is None

    def test_uses_condition_id_fallback(self) -> None:
        """Falls back to condition_id when market not present."""
        msg = json.dumps(
            {
                "condition_id": "cond-1",
                "asset_id": "tok-2",
                "price": "0.40",
            }
        )
        update = _parse_ws_message(msg)
        assert update is not None
        assert update.market_id == "cond-1"

    def test_heartbeat_ignored_with_telemetry(self) -> None:
        """Heartbeat messages are classified as ignored."""
        from arb_scanner.flippening.ws_telemetry import WsTelemetry

        t = WsTelemetry()
        msg = json.dumps({"type": "heartbeat"})
        assert _parse_ws_message(msg, telemetry=t) is None
        assert t.ignored == 1

    def test_subscription_ack_ignored(self) -> None:
        """Subscription ack messages are ignored."""
        from arb_scanner.flippening.ws_telemetry import WsTelemetry

        t = WsTelemetry()
        msg = json.dumps({"type": "subscribed"})
        assert _parse_ws_message(msg, telemetry=t) is None
        assert t.ignored == 1

    def test_error_msg_ignored(self) -> None:
        """Error messages are ignored (logged at warning)."""
        from arb_scanner.flippening.ws_telemetry import WsTelemetry

        t = WsTelemetry()
        msg = json.dumps({"type": "error", "message": "bad"})
        assert _parse_ws_message(msg, telemetry=t) is None
        assert t.ignored == 1

    def test_telemetry_tracks_parsed(self) -> None:
        """Successful parse increments parsed_ok."""
        from arb_scanner.flippening.ws_telemetry import WsTelemetry

        t = WsTelemetry()
        msg = json.dumps({"market": "m", "asset_id": "t", "price": "0.5"})
        _parse_ws_message(msg, telemetry=t)
        assert t.parsed_ok == 1

    def test_telemetry_failure_missing_market_id(self) -> None:
        """Missing market_id tracked as failure."""
        from arb_scanner.flippening.ws_telemetry import WsTelemetry

        t = WsTelemetry()
        msg = json.dumps({"asset_id": "t", "price": "0.5"})
        _parse_ws_message(msg, telemetry=t)
        assert t._failure_reasons.get("missing_market_id") == 1

    def test_telemetry_failure_missing_token_id(self) -> None:
        """Missing token_id tracked as failure."""
        from arb_scanner.flippening.ws_telemetry import WsTelemetry

        t = WsTelemetry()
        msg = json.dumps({"market": "m", "price": "0.5"})
        _parse_ws_message(msg, telemetry=t)
        assert t._failure_reasons.get("missing_token_id") == 1

    def test_telemetry_failure_missing_price(self) -> None:
        """Missing price tracked as failure."""
        from arb_scanner.flippening.ws_telemetry import WsTelemetry

        t = WsTelemetry()
        msg = json.dumps({"market": "m", "asset_id": "t"})
        _parse_ws_message(msg, telemetry=t)
        assert t._failure_reasons.get("missing_price") == 1

    def test_telemetry_failure_price_out_of_range(self) -> None:
        """Price > 1.0 tracked as out_of_range failure."""
        from arb_scanner.flippening.ws_telemetry import WsTelemetry

        t = WsTelemetry()
        msg = json.dumps({"market": "m", "asset_id": "t", "price": "1.5"})
        _parse_ws_message(msg, telemetry=t)
        assert t._failure_reasons.get("price_out_of_range") == 1

    def test_bytes_message_parsed(self) -> None:
        """Bytes messages are decoded and parsed."""
        msg = json.dumps({"market": "m", "asset_id": "t", "price": "0.5"}).encode()
        update = _parse_ws_message(msg)
        assert update is not None
        assert update.market_id == "m"

    def test_non_json_bytes_ignored(self) -> None:
        """Non-JSON bytes messages are ignored, not failed."""
        from arb_scanner.flippening.ws_telemetry import WsTelemetry

        t = WsTelemetry()
        _parse_ws_message(b"ping", telemetry=t)
        assert t.ignored == 1
        assert t.parse_failed == 0

    def test_schema_recorded_for_json(self) -> None:
        """Schema is recorded for every valid JSON dict."""
        from arb_scanner.flippening.ws_telemetry import WsTelemetry

        t = WsTelemetry()
        msg = json.dumps({"market": "m", "asset_id": "t", "price": "0.5"})
        _parse_ws_message(msg, telemetry=t)
        assert len(t.known_schemas) == 1


class TestParseOrderbook:
    """Tests for _parse_orderbook helper."""

    def test_valid_orderbook(self) -> None:
        """Parses bids and asks into PriceUpdate."""
        data: dict[str, Any] = {
            "bids": [
                {"price": "0.60", "size": "100"},
                {"price": "0.62", "size": "50"},
            ],
            "asks": [
                {"price": "0.65", "size": "80"},
                {"price": "0.68", "size": "40"},
            ],
        }
        update = _parse_orderbook("tok-1", data)
        assert update is not None
        assert update.token_id == "tok-1"
        assert update.yes_bid == Decimal("0.62")
        assert update.yes_ask == Decimal("0.65")

    def test_empty_bids_asks(self) -> None:
        """Empty order book uses defaults."""
        data: dict[str, Any] = {"bids": [], "asks": []}
        update = _parse_orderbook("tok-1", data)
        assert update is not None
        assert update.yes_bid == Decimal("0")
        assert update.yes_ask == Decimal("1")

    def test_invalid_bids_type_returns_none(self) -> None:
        """Non-list bids returns None."""
        data: dict[str, Any] = {"bids": "invalid", "asks": []}
        assert _parse_orderbook("tok-1", data) is None


class TestSafeDec:
    """Tests for _safe_dec helper."""

    def test_string_value(self) -> None:
        """Converts string to Decimal."""
        assert _safe_dec("0.65") == Decimal("0.65")

    def test_none_returns_none(self) -> None:
        """None input returns None."""
        assert _safe_dec(None) is None

    def test_invalid_returns_none(self) -> None:
        """Invalid string returns None."""
        assert _safe_dec("not-a-number") is None


class TestWebSocketPriceStream:
    """Tests for WebSocketPriceStream."""

    @pytest.mark.asyncio
    async def test_subscribe_starts_reader(self) -> None:
        """Subscribe creates a background reader task."""
        stream = WebSocketPriceStream(ws_url="wss://example.com/ws")
        with patch(
            "arb_scanner.flippening.ws_client.WebSocketPriceStream._reader_loop",
            new_callable=AsyncMock,
        ):
            await stream.subscribe(["tok-1", "tok-2"])
            assert stream._reader_task is not None
            assert stream._subscribed_tokens == ["tok-1", "tok-2"]
            await stream.close()

    @pytest.mark.asyncio
    async def test_close_cancels_reader(self) -> None:
        """Close cancels the background reader task."""
        stream = WebSocketPriceStream()

        async def _hang_forever() -> None:
            await asyncio.sleep(3600)

        task = asyncio.create_task(_hang_forever())
        stream._reader_task = task
        await stream.close()
        assert stream._closed is True
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_anext_returns_from_queue(self) -> None:
        """__anext__ yields items from the internal queue."""
        stream = WebSocketPriceStream()
        from arb_scanner.models.flippening import PriceUpdate

        update = PriceUpdate(
            market_id="m1",
            token_id="t1",
            yes_bid=Decimal("0.60"),
            yes_ask=Decimal("0.62"),
            no_bid=Decimal("0.37"),
            no_ask=Decimal("0.39"),
            timestamp=datetime.now(tz=UTC),
        )
        await stream._queue.put(update)
        result = await stream.__anext__()
        assert result.market_id == "m1"

    @pytest.mark.asyncio
    async def test_anext_raises_stop_when_closed(self) -> None:
        """__anext__ raises StopAsyncIteration when closed and empty."""
        stream = WebSocketPriceStream()
        stream._closed = True
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()


class TestPollingPriceStream:
    """Tests for PollingPriceStream."""

    @pytest.mark.asyncio
    async def test_subscribe_starts_polling(self) -> None:
        """Subscribe creates HTTP client and polling task."""
        stream = PollingPriceStream(
            clob_base_url="https://clob.polymarket.com",
            interval_seconds=1.0,
        )
        with patch(
            "arb_scanner.flippening.ws_client.PollingPriceStream._poll_loop",
            new_callable=AsyncMock,
        ):
            await stream.subscribe(["tok-1"])
            assert stream._client is not None
            assert stream._polling_task is not None
            assert stream._subscribed_tokens == ["tok-1"]
            await stream.close()

    @pytest.mark.asyncio
    async def test_close_cleans_up(self) -> None:
        """Close cancels polling and closes HTTP client."""
        stream = PollingPriceStream(
            clob_base_url="https://clob.polymarket.com",
        )

        async def _hang_forever() -> None:
            await asyncio.sleep(3600)

        task = asyncio.create_task(_hang_forever())
        stream._polling_task = task
        mock_client = AsyncMock()
        stream._client = mock_client
        await stream.close()
        assert stream._closed is True
        assert task.cancelled()
        mock_client.aclose.assert_awaited_once()


class TestCreatePriceStream:
    """Tests for create_price_stream factory."""

    @pytest.mark.asyncio
    async def test_returns_websocket_when_available(self) -> None:
        """Returns WebSocketPriceStream when websockets is importable."""
        config = FlippeningConfig(enabled=True)
        stream = await create_price_stream(config)
        assert isinstance(stream, WebSocketPriceStream)

    @pytest.mark.asyncio
    async def test_falls_back_to_polling(self) -> None:
        """Falls back to PollingPriceStream when websockets unavailable."""
        config = FlippeningConfig(enabled=True)
        import builtins

        real_import = builtins.__import__

        def _block_websockets(
            name: str,
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            if name == "websockets":
                raise ImportError("No module named 'websockets'")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_block_websockets):
            stream = await create_price_stream(config)
            assert isinstance(stream, PollingPriceStream)
