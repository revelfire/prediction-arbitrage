"""Real-time price streaming via WebSocket or REST polling fallback."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import AsyncIterator, Protocol, runtime_checkable

import httpx
import structlog

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
    """Stream price updates via Polymarket CLOB WebSocket.

    Reconnects automatically on disconnect with exponential backoff.
    """

    def __init__(
        self,
        ws_url: str = _DEFAULT_WS_URL,
        reconnect_max_seconds: int = 60,
    ) -> None:
        """Initialise WebSocket stream.

        Args:
            ws_url: WebSocket endpoint URL.
            reconnect_max_seconds: Max backoff for reconnect.
        """
        self._ws_url = ws_url
        self._reconnect_max = reconnect_max_seconds
        self._subscribed_tokens: list[str] = []
        self._queue: asyncio.Queue[PriceUpdate] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False

    async def subscribe(self, token_ids: list[str]) -> None:
        """Connect and subscribe to token price updates.

        Args:
            token_ids: CLOB token identifiers to monitor.
        """
        self._subscribed_tokens = list(token_ids)
        self._reader_task = asyncio.create_task(self._reader_loop())
        logger.info(
            "ws_subscribe",
            token_count=len(token_ids),
            url=self._ws_url,
        )

    async def _reader_loop(self) -> None:
        """Background loop: connect, read, reconnect on failure."""
        delay = 1.0
        while not self._closed:
            try:
                import websockets

                async with websockets.connect(self._ws_url) as ws:
                    logger.info("ws_connected", url=self._ws_url)
                    delay = 1.0
                    for token_id in self._subscribed_tokens:
                        msg = json.dumps(
                            {
                                "type": "subscribe",
                                "channel": "market",
                                "assets_id": token_id,
                            }
                        )
                        await ws.send(msg)

                    async for raw_msg in ws:
                        update = _parse_ws_message(raw_msg)
                        if update is not None:
                            await self._queue.put(update)

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
    """Stream price updates by polling the CLOB REST API.

    Fallback when WebSocket is unavailable.
    """

    def __init__(
        self,
        clob_base_url: str,
        interval_seconds: float = 5.0,
        rate_limit_per_sec: int = 10,
    ) -> None:
        """Initialise polling stream.

        Args:
            clob_base_url: CLOB API base URL.
            interval_seconds: Seconds between poll cycles.
            rate_limit_per_sec: Max requests per second.
        """
        self._clob_url = clob_base_url
        self._interval = interval_seconds
        self._rate_limiter = RateLimiter(rate_limit_per_sec)
        self._subscribed_tokens: list[str] = []
        self._queue: asyncio.Queue[PriceUpdate] = asyncio.Queue()
        self._polling_task: asyncio.Task[None] | None = None
        self._closed = False
        self._client: httpx.AsyncClient | None = None

    async def subscribe(self, token_ids: list[str]) -> None:
        """Start polling for the given tokens.

        Args:
            token_ids: CLOB token identifiers to monitor.
        """
        self._subscribed_tokens = list(token_ids)
        self._client = httpx.AsyncClient(
            base_url=self._clob_url,
            timeout=10.0,
        )
        self._polling_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "polling_subscribe",
            token_count=len(token_ids),
            interval=self._interval,
        )

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
        """Fetch order book for one token and parse into PriceUpdate.

        Args:
            token_id: CLOB token identifier.

        Returns:
            PriceUpdate or None on parse failure.
        """
        if self._client is None:
            return None
        async with self._rate_limiter.acquire():
            resp = await self._client.get(
                "/book",
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            data = resp.json()
            return _parse_orderbook(token_id, data)

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
) -> PriceStream:
    """Create the best available price stream.

    Tries WebSocket first; falls back to REST polling.

    Args:
        config: Flippening configuration.
        clob_base_url: CLOB API base URL for polling fallback.

    Returns:
        A PriceStream implementation.
    """
    try:
        import websockets  # noqa: F401

        ws = WebSocketPriceStream(
            reconnect_max_seconds=config.ws_reconnect_max_seconds,
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


def _parse_ws_message(raw: str | bytes) -> PriceUpdate | None:
    """Parse a WebSocket message into a PriceUpdate.

    Args:
        raw: Raw WebSocket message data.

    Returns:
        PriceUpdate or None if unparseable.
    """
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None

        market_id = str(data.get("market", data.get("condition_id", "")))
        token_id = str(data.get("asset_id", ""))
        if not market_id or not token_id:
            return None

        price = _safe_dec(data.get("price"))
        if price is None:
            return None

        return PriceUpdate(
            market_id=market_id,
            token_id=token_id,
            yes_bid=max(price - Decimal("0.01"), Decimal("0")),
            yes_ask=min(price + Decimal("0.01"), Decimal("1")),
            no_bid=max(Decimal("1") - price - Decimal("0.01"), Decimal("0")),
            no_ask=min(Decimal("1") - price + Decimal("0.01"), Decimal("1")),
            timestamp=datetime.now(tz=UTC),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _parse_orderbook(
    token_id: str,
    data: dict[str, object],
) -> PriceUpdate | None:
    """Parse a CLOB order book response into a PriceUpdate.

    Args:
        token_id: The token identifier.
        data: Raw order book JSON.

    Returns:
        PriceUpdate or None if unparseable.
    """
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
    """Safely convert a value to Decimal.

    Args:
        value: Raw value to convert.

    Returns:
        Decimal or None on failure.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
