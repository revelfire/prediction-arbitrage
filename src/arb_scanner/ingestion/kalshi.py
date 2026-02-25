"""Kalshi venue client – public market data and order-book depth."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from operator import attrgetter

import structlog

from arb_scanner.ingestion.base import BaseVenueClient
from arb_scanner.models.config import KalshiVenueConfig
from arb_scanner.models.market import Market, Venue
from arb_scanner.utils.retry import async_retry

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="ingestion.kalshi")

_PAGE_LIMIT = 200


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

    async def fetch_markets(self) -> list[Market]:
        """Fetch open Kalshi markets via cursor-based pagination.

        Applies client-side volume filtering (``min_volume_24h``) and
        caps results at ``max_markets`` (0 = unlimited).  Results are
        sorted by 24h volume descending so the most liquid markets
        appear first when capped.

        Returns:
            Normalised :class:`Market` list.
        """
        min_vol = self._cfg.min_volume_24h
        max_markets = self._cfg.max_markets
        markets: list[Market] = []
        cursor: str | None = None
        while True:
            page, cursor = await self._fetch_markets_page(cursor)
            for raw in page:
                market = _parse_kalshi_market(raw)
                if market is not None:
                    if min_vol and market.volume_24h < min_vol:
                        continue
                    markets.append(market)
            if not cursor:
                break
        if max_markets:
            markets.sort(key=attrgetter("volume_24h"), reverse=True)
            markets = markets[:max_markets]
        logger.info("kalshi_fetch_complete", total=len(markets))
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
        return _process_orderbook(data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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


# ------------------------------------------------------------------
# Module-level parsing helpers
# ------------------------------------------------------------------


def _safe_decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
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


def _clamp(value: Decimal) -> Decimal:
    """Clamp *value* into [0, 1].

    Args:
        value: Decimal to clamp.

    Returns:
        Clamped Decimal.
    """
    return max(Decimal("0"), min(Decimal("1"), value))


def _parse_kalshi_market(raw: dict[str, object]) -> Market | None:
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

        yes_bid = _clamp(_safe_decimal(raw.get("yes_bid_dollars")))
        yes_ask = _clamp(_safe_decimal(raw.get("yes_ask_dollars")))
        no_bid = _clamp(_safe_decimal(raw.get("no_bid_dollars")))
        no_ask = _clamp(_safe_decimal(raw.get("no_ask_dollars")))

        if yes_bid > yes_ask:
            yes_bid, yes_ask = yes_ask, yes_bid
        if no_bid > no_ask:
            no_bid, no_ask = no_ask, no_bid

        rules = _build_rules(raw)
        expiry = _parse_expiry(raw.get("expiration_time"))
        volume_raw = raw.get("volume_dollars_24h_fp") or raw.get("volume_fp", "0")
        volume = _safe_decimal(volume_raw)

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


def _parse_expiry(value: object) -> datetime | None:
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


def _process_orderbook(data: dict[str, object]) -> dict[str, object]:
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

    yes_best = _safe_decimal(yes_bids[-1][0]) if yes_bids else Decimal("0")
    no_best = _safe_decimal(no_bids[-1][0]) if no_bids else Decimal("0")

    yes_ask = _clamp(Decimal("1") - no_best)
    no_ask = _clamp(Decimal("1") - yes_best)

    return {
        "yes_bids": yes_bids,
        "no_bids": no_bids,
        "yes_best_bid": str(yes_best),
        "no_best_bid": str(no_best),
        "yes_ask": str(yes_ask),
        "no_ask": str(no_ask),
    }
