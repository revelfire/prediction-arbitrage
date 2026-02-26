"""Kalshi venue client – public market data and order-book depth."""

from __future__ import annotations

from operator import attrgetter

import structlog

from arb_scanner.ingestion._kalshi_parse import parse_market, process_orderbook
from arb_scanner.ingestion.base import BaseVenueClient
from arb_scanner.models.config import KalshiVenueConfig
from arb_scanner.models.market import Market
from arb_scanner.utils.retry import async_retry

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="ingestion.kalshi")

_PAGE_LIMIT = 200
_MAX_PAGES = 100  # Safety cap: never paginate beyond 20K markets
_MAX_EVENT_PAGES = 30  # Events are much fewer than markets
_MAX_EVENT_MARKET_PAGES = 5  # Per-event market pagination cap


class KalshiClient(BaseVenueClient):
    """Async client for the Kalshi public market-data API.

    Uses cursor-based pagination for market discovery and computes
    order-book asks from the bids-only response.
    """

    def __init__(self, config: KalshiVenueConfig | None = None) -> None:
        """Initialise from a :class:`KalshiVenueConfig`.

        Args:
            config: Venue configuration.  Defaults are used when *None*.
        """
        self._cfg = config or KalshiVenueConfig()
        super().__init__(
            base_url=self._cfg.base_url,
            rate_limit_per_sec=self._cfg.rate_limit_per_sec,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_events(self) -> list[dict[str, str]]:
        """Fetch open events for demand-driven market discovery.

        Returns:
            List of dicts with ``event_ticker``, ``title``, ``category``.
        """
        events: list[dict[str, str]] = []
        cursor: str | None = None
        pages = 0
        while True:
            page, cursor = await self._fetch_events_page(cursor)
            pages += 1
            for raw in page:
                ticker = str(raw.get("event_ticker", ""))
                title = str(raw.get("title", ""))
                category = str(raw.get("category", ""))
                if ticker and title:
                    events.append({"event_ticker": ticker, "title": title, "category": category})
            if not cursor or pages >= _MAX_EVENT_PAGES:
                break
        logger.info("kalshi_events_fetched", total=len(events), pages=pages)
        return events

    async def fetch_markets_for_events(
        self,
        event_tickers: list[str],
    ) -> list[Market]:
        """Fetch markets for specific events, applying volume filter.

        Args:
            event_tickers: Event tickers to fetch markets for.

        Returns:
            Normalised :class:`Market` list sorted by volume descending.
        """
        min_vol = self._cfg.min_volume_24h
        markets: list[Market] = []
        for ticker in event_tickers:
            raw_list = await self._fetch_event_markets(ticker)
            for raw in raw_list:
                market = parse_market(raw)
                if market is None:
                    continue
                if min_vol and market.volume_24h < min_vol:
                    continue
                markets.append(market)
        markets.sort(key=attrgetter("volume_24h"), reverse=True)
        logger.info(
            "kalshi_event_markets_fetched",
            events=len(event_tickers),
            markets=len(markets),
        )
        return markets

    async def fetch_markets(self) -> list[Market]:
        """Fetch open Kalshi markets via cursor-based pagination.

        Applies client-side volume filtering and ticker prefix exclusion.
        Used as a fallback when event-driven fetch is not available.

        Returns:
            Normalised :class:`Market` list.
        """
        min_vol = self._cfg.min_volume_24h
        max_markets = self._cfg.max_markets
        collect_target = max_markets * 5 if max_markets else 0
        exclude = tuple(self._cfg.exclude_ticker_prefixes)
        markets: list[Market] = []
        excluded = 0
        cursor: str | None = None
        pages = 0
        while True:
            page, cursor = await self._fetch_markets_page(cursor)
            pages += 1
            for raw in page:
                if exclude:
                    ticker = str(raw.get("ticker", ""))
                    if ticker.startswith(exclude):
                        excluded += 1
                        continue
                market = parse_market(raw)
                if market is not None:
                    if min_vol and market.volume_24h < min_vol:
                        continue
                    markets.append(market)
            if collect_target and len(markets) >= collect_target:
                break
            if not cursor or pages >= _MAX_PAGES:
                break
        markets.sort(key=attrgetter("volume_24h"), reverse=True)
        logger.info(
            "kalshi_fetch_complete",
            total=len(markets),
            excluded=excluded,
            pages=pages,
        )
        return markets

    @async_retry(max_retries=3)
    async def fetch_orderbook(self, ticker: str) -> dict[str, object]:
        """Fetch and process the order book for a market.

        Kalshi only returns bids.  Asks are computed as::

            YES_ask = 1.00 - highest_NO_bid

        Best bid is the **last** element (ascending sort).

        Args:
            ticker: Market ticker string.

        Returns:
            Dict with ``yes_bids``, ``no_bids``, ``yes_best_bid``,
            ``no_best_bid``, ``yes_ask``, and ``no_ask``.
        """
        async with self.rate_limiter.acquire():
            resp = await self.client.get(f"/markets/{ticker}/orderbook")
            resp.raise_for_status()
            data: dict[str, object] = resp.json()
        return process_orderbook(data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @async_retry(max_retries=3)
    async def _fetch_events_page(
        self,
        cursor: str | None,
    ) -> tuple[list[dict[str, object]], str | None]:
        """Fetch one page of open events.

        Args:
            cursor: Pagination cursor, *None* for the first page.

        Returns:
            Tuple of (event dicts, next cursor or *None* if done).
        """
        params: dict[str, str | int] = {"status": "open", "limit": _PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        async with self.rate_limiter.acquire():
            resp = await self.client.get("/events", params=params)
            resp.raise_for_status()
            body: dict[str, object] = resp.json()
        raw_events = body.get("events")
        page: list[dict[str, object]] = raw_events if isinstance(raw_events, list) else []
        next_cursor_raw = body.get("cursor")
        next_cursor = str(next_cursor_raw) if next_cursor_raw else None
        return page, next_cursor

    @async_retry(max_retries=3)
    async def _fetch_markets_page(
        self,
        cursor: str | None,
    ) -> tuple[list[dict[str, object]], str | None]:
        """Fetch one page of open markets.

        Args:
            cursor: Pagination cursor, *None* for the first page.

        Returns:
            Tuple of (market dicts, next cursor or *None* if done).
        """
        params: dict[str, str | int] = {"status": "open", "limit": _PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        async with self.rate_limiter.acquire():
            resp = await self.client.get("/markets", params=params)
            resp.raise_for_status()
            body: dict[str, object] = resp.json()
        raw_markets = body.get("markets")
        page: list[dict[str, object]] = raw_markets if isinstance(raw_markets, list) else []
        next_cursor_raw = body.get("cursor")
        next_cursor = str(next_cursor_raw) if next_cursor_raw else None
        return page, next_cursor

    @async_retry(max_retries=3)
    async def _fetch_event_markets(
        self,
        event_ticker: str,
    ) -> list[dict[str, object]]:
        """Fetch all markets for a single event.

        Args:
            event_ticker: The event ticker to query markets for.

        Returns:
            List of raw market dicts for the event.
        """
        all_markets: list[dict[str, object]] = []
        cursor: str | None = None
        for _ in range(_MAX_EVENT_MARKET_PAGES):
            params: dict[str, str | int] = {
                "status": "open",
                "event_ticker": event_ticker,
                "limit": _PAGE_LIMIT,
            }
            if cursor:
                params["cursor"] = cursor
            async with self.rate_limiter.acquire():
                resp = await self.client.get("/markets", params=params)
                resp.raise_for_status()
                body: dict[str, object] = resp.json()
            raw = body.get("markets")
            page: list[dict[str, object]] = raw if isinstance(raw, list) else []
            all_markets.extend(page)
            next_cursor_raw = body.get("cursor")
            cursor = str(next_cursor_raw) if next_cursor_raw else None
            if not cursor:
                break
        return all_markets
