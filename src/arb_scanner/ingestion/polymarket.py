"""Polymarket venue client – Gamma API for discovery, CLOB API for depth."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
import structlog

from arb_scanner.ingestion.base import BaseVenueClient
from arb_scanner.models.config import PolymarketVenueConfig
from arb_scanner.models.market import Market, Venue
from arb_scanner.utils.retry import async_retry

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="ingestion.polymarket")

_PAGE_LIMIT = 100


class PolymarketClient(BaseVenueClient):
    """Async client for the Polymarket Gamma and CLOB APIs.

    Uses Gamma for market discovery and CLOB for order-book depth.
    """

    def __init__(self, config: PolymarketVenueConfig | None = None) -> None:
        """Initialise from a :class:`PolymarketVenueConfig`.

        Args:
            config: Venue configuration.  Defaults are used when *None*.
        """
        self._cfg = config or PolymarketVenueConfig()
        super().__init__(
            base_url=self._cfg.gamma_base_url,
            rate_limit_per_sec=self._cfg.rate_limit_per_sec,
        )
        self._clob_client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Context manager – manage both HTTP clients
    # ------------------------------------------------------------------

    async def __aenter__(self) -> PolymarketClient:
        """Open Gamma and CLOB HTTP clients."""
        await super().__aenter__()
        self._clob_client = httpx.AsyncClient(
            base_url=self._cfg.clob_base_url,
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close both HTTP clients."""
        if self._clob_client is not None:
            await self._clob_client.aclose()
            self._clob_client = None
        await super().__aexit__(None, None, None)

    @property
    def clob_client(self) -> httpx.AsyncClient:
        """Return the active CLOB ``httpx.AsyncClient``."""
        if self._clob_client is None:
            raise RuntimeError("CLOB client not opened; use 'async with'")
        return self._clob_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_markets(self) -> list[Market]:
        """Fetch active Polymarket markets via Gamma API pagination.

        Applies server-side volume filtering and sorts by volume
        descending so the most liquid markets are fetched first.
        Stops early when ``max_markets`` is reached (0 = unlimited).

        Returns:
            Normalised :class:`Market` list.
        """
        max_markets = self._cfg.max_markets
        markets: list[Market] = []
        offset = 0
        while True:
            page = await self._fetch_gamma_page(offset)
            if not page:
                break
            for raw in page:
                market = _parse_gamma_market(raw)
                if market is not None:
                    markets.append(market)
            if max_markets and len(markets) >= max_markets:
                markets = markets[:max_markets]
                break
            if len(page) < _PAGE_LIMIT:
                break
            offset += _PAGE_LIMIT
        logger.info("polymarket_fetch_complete", total=len(markets))
        return markets

    @async_retry(max_retries=3)
    async def fetch_orderbook(self, token_id: str) -> dict[str, object]:
        """Fetch order-book depth from the CLOB API.

        Args:
            token_id: CLOB token identifier.

        Returns:
            Raw order-book dict with ``bids`` and ``asks`` arrays.
        """
        async with self.rate_limiter.acquire():
            resp = await self.clob_client.get("/book", params={"token_id": token_id})
            resp.raise_for_status()
            data: dict[str, object] = resp.json()
            return data

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @async_retry(max_retries=3)
    async def _fetch_gamma_page(self, offset: int) -> list[dict[str, object]]:
        """Fetch one page of markets from the Gamma API.

        Args:
            offset: Pagination offset.

        Returns:
            List of raw market dicts.
        """
        params: dict[str, str | int] = {
            "active": "true",
            "closed": "false",
            "limit": _PAGE_LIMIT,
            "offset": offset,
            "order": "volume",
            "ascending": "false",
        }
        if self._cfg.min_volume_24h > 0:
            params["volume_num_min"] = str(self._cfg.min_volume_24h)
        async with self.rate_limiter.acquire():
            resp = await self.client.get("/markets", params=params)
            resp.raise_for_status()
            result: list[dict[str, object]] = resp.json()
            return result


# ------------------------------------------------------------------
# Module-level parsing helpers (keep client class slim)
# ------------------------------------------------------------------


def _safe_json_loads(value: object) -> list[str]:
    """Decode a JSON-string field like ``outcomePrices`` into a list.

    Args:
        value: Raw field value (expected JSON string).

    Returns:
        Parsed list of strings, or empty list on failure.
    """
    if isinstance(value, str):
        try:
            parsed: list[str] = json.loads(value)
            return parsed
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _safe_decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    """Convert *value* to :class:`Decimal`, returning *default* on failure.

    Args:
        value: Raw value to convert.
        default: Fallback value.

    Returns:
        Parsed Decimal.
    """
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _parse_gamma_market(raw: dict[str, object]) -> Market | None:
    """Parse a single Gamma API response dict into a :class:`Market`.

    Args:
        raw: Single market dict from the Gamma ``/markets`` response.

    Returns:
        A validated :class:`Market`, or *None* when required data is missing.
    """
    try:
        condition_id = str(raw.get("condition_id", "") or raw.get("id", ""))
        question = str(raw.get("question", ""))
        description = str(raw.get("description", "") or "")
        if not condition_id or not question:
            return None

        prices = _safe_json_loads(raw.get("outcomePrices", "[]"))
        yes_price = _safe_decimal(prices[0]) if len(prices) > 0 else Decimal("0")
        no_price = _safe_decimal(prices[1]) if len(prices) > 1 else Decimal("0")

        spread = Decimal("0.01")
        yes_bid = max(yes_price - spread, Decimal("0"))
        yes_ask = min(yes_price + spread, Decimal("1"))
        no_bid = max(no_price - spread, Decimal("0"))
        no_ask = min(no_price + spread, Decimal("1"))

        end_date = _parse_datetime(raw.get("end_date_iso") or raw.get("endDate"))
        volume = _safe_decimal(raw.get("volume", "0") or raw.get("volumeNum", "0"))

        return Market(
            venue=Venue.POLYMARKET,
            event_id=condition_id,
            title=question,
            description=description,
            resolution_criteria=str(raw.get("resolution_source", "") or ""),
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            volume_24h=volume,
            expiry=end_date,
            fees_pct=Decimal("0.02"),
            fee_model="on_winnings",
            last_updated=datetime.now(tz=UTC),
            raw_data=raw,
        )
    except Exception:
        logger.warning("polymarket_parse_error", raw_keys=list(raw.keys()))
        return None


def _parse_datetime(value: object) -> datetime | None:
    """Parse an ISO-format datetime string.

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
