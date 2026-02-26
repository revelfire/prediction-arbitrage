"""Real-time price streaming via WebSocket or REST polling fallback."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Protocol, runtime_checkable

import httpx
import structlog

from arb_scanner.flippening.ws_parser import parse_orderbook, parse_ws_message
from arb_scanner.flippening.ws_telemetry import WsTelemetry
from arb_scanner.models.config import FlippeningConfig
from arb_scanner.models.flippening import PriceUpdate
from arb_scanner.utils.rate_limiter import RateLimiter

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="flippening.ws_client",
)

_DEFAULT_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@runtime_checkable
class PriceStream(Protocol):
    """Protocol for real-time price update streams."""

    async def subscribe(self, token_ids: list[str]) -> None:
        """Subscribe to price updates for the given tokens.

        Args:
            token_ids: CLOB token identifiers to monitor.
        """
        ...

    def __aiter__(self) -> AsyncIterator[PriceUpdate]:
        """Return async iterator over price updates."""
        ...

    async def __anext__(self) -> PriceUpdate:
        """Yield the next price update."""
        ...

    async def close(self) -> None:
        """Shut down the stream and release resources."""
        ...


class WebSocketPriceStream:
    """Stream prices via Polymarket CLOB WebSocket with auto-reconnect."""

    def __init__(
        self,
        ws_url: str = _DEFAULT_WS_URL,
        reconnect_max_seconds: int = 60,
        telemetry: WsTelemetry | None = None,
        telemetry_interval_seconds: int = 60,
    ) -> None:
        """Initialise WebSocket stream."""
        self._ws_url = ws_url
        self._reconnect_max = reconnect_max_seconds
        self._subscribed_tokens: list[str] = []
        self._queue: asyncio.Queue[PriceUpdate] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False
        self._telemetry = telemetry
        self._telemetry_interval = telemetry_interval_seconds

    async def subscribe(self, token_ids: list[str]) -> None:
        """Connect and subscribe to token price updates."""
        self._subscribed_tokens = list(token_ids)
        self._reader_task = asyncio.create_task(self._reader_loop())
        logger.info("ws_subscribe", token_count=len(token_ids), url=self._ws_url)

    async def _reader_loop(self) -> None:
        """Background loop: connect, read, reconnect on failure."""
        delay = 1.0
        while not self._closed:
            try:
                import websockets

                async with websockets.connect(self._ws_url) as ws:
                    logger.info("ws_connected", url=self._ws_url)
                    delay = 1.0
                    sub_msg = json.dumps(
                        {
                            "assets_ids": self._subscribed_tokens,
                            "type": "market",
                        }
                    )
                    await ws.send(sub_msg)
                    logger.info(
                        "ws_subscribed",
                        token_count=len(self._subscribed_tokens),
                    )
                    ping_task = asyncio.create_task(
                        self._ping_loop(ws),
                    )
                    try:
                        async for raw_msg in ws:
                            update = parse_ws_message(
                                raw_msg,
                                self._telemetry,
                            )
                            if update is not None:
                                await self._queue.put(update)
                            if self._telemetry:
                                self._telemetry.should_log(
                                    self._telemetry_interval,
                                )
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

            except asyncio.CancelledError:
                break
            except Exception:
                if self._closed:
                    break
                logger.warning(
                    "ws_disconnected",
                    reconnect_delay=delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._reconnect_max)

    async def _ping_loop(self, ws: object) -> None:
        """Send PING every 10s to keep the connection alive."""
        try:
            while not self._closed:
                await asyncio.sleep(10)
                await ws.send("PING")  # type: ignore[attr-defined]
        except (asyncio.CancelledError, Exception):
            pass

    def __aiter__(self) -> AsyncIterator[PriceUpdate]:
        """Return self as async iterator."""
        return self

    async def __anext__(self) -> PriceUpdate:
        """Yield next price update from the queue."""
        if self._closed and self._queue.empty():
            raise StopAsyncIteration
        return await self._queue.get()

    async def close(self) -> None:
        """Cancel reader task and mark stream as closed."""
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        logger.info("ws_closed")


class PollingPriceStream:
    """Stream prices by polling the CLOB REST API (WS fallback)."""

    def __init__(
        self,
        clob_base_url: str,
        interval_seconds: float = 5.0,
        rate_limit_per_sec: int = 10,
    ) -> None:
        """Initialise polling stream."""
        self._clob_url = clob_base_url
        self._interval = interval_seconds
        self._rate_limiter = RateLimiter(rate_limit_per_sec)
        self._subscribed_tokens: list[str] = []
        self._queue: asyncio.Queue[PriceUpdate] = asyncio.Queue()
        self._polling_task: asyncio.Task[None] | None = None
        self._closed = False
        self._client: httpx.AsyncClient | None = None

    async def subscribe(self, token_ids: list[str]) -> None:
        """Start polling for the given tokens."""
        self._subscribed_tokens = list(token_ids)
        self._client = httpx.AsyncClient(base_url=self._clob_url, timeout=10.0)
        self._polling_task = asyncio.create_task(self._poll_loop())
        logger.info("polling_subscribe", token_count=len(token_ids), interval=self._interval)

    async def _poll_loop(self) -> None:
        """Background loop: poll order books at interval."""
        while not self._closed:
            for token_id in self._subscribed_tokens:
                if self._closed:
                    break
                try:
                    update = await self._fetch_one(token_id)
                    if update is not None:
                        await self._queue.put(update)
                except Exception:
                    logger.warning(
                        "poll_fetch_error",
                        token_id=token_id,
                    )
            await asyncio.sleep(self._interval)

    async def _fetch_one(self, token_id: str) -> PriceUpdate | None:
        """Fetch order book for one token and parse into PriceUpdate."""
        if self._client is None:
            return None
        async with self._rate_limiter.acquire():
            resp = await self._client.get(
                "/book",
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            data = resp.json()
            return parse_orderbook(token_id, data)

    def __aiter__(self) -> AsyncIterator[PriceUpdate]:
        """Return self as async iterator."""
        return self

    async def __anext__(self) -> PriceUpdate:
        """Yield next price update from the queue."""
        if self._closed and self._queue.empty():
            raise StopAsyncIteration
        return await self._queue.get()

    async def close(self) -> None:
        """Cancel polling and close HTTP client."""
        self._closed = True
        if self._polling_task is not None:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        if self._client is not None:
            await self._client.aclose()
        logger.info("polling_closed")


async def create_price_stream(
    config: FlippeningConfig,
    clob_base_url: str = "https://clob.polymarket.com",
    telemetry: WsTelemetry | None = None,
) -> PriceStream:
    """Create the best available price stream (WS preferred, polling fallback)."""
    try:
        import websockets  # noqa: F401

        ws = WebSocketPriceStream(
            reconnect_max_seconds=config.ws_reconnect_max_seconds,
            telemetry=telemetry,
            telemetry_interval_seconds=config.ws_telemetry_interval_seconds,
        )
        logger.info("price_stream_mode", mode="websocket")
        return ws
    except ImportError:
        logger.warning(
            "websockets_not_available",
            fallback="polling",
        )

    stream = PollingPriceStream(
        clob_base_url=clob_base_url,
        interval_seconds=config.polling_interval_seconds,
    )
    logger.info("price_stream_mode", mode="polling")
    return stream
