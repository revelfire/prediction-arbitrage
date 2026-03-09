"""Polymarket CLOB order execution via py-clob-client SDK."""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.models.config import PolyExecConfig
from arb_scanner.models.execution import OrderRequest, OrderResponse

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.polymarket",
)

_ZERO = Decimal("0")
_USDC_SCALE = Decimal("1000000")


class PolymarketExecutor:
    """Execute trades on Polymarket's CLOB API.

    Uses the py-clob-client SDK for EIP-712 signing and order placement.
    """

    def __init__(self, config: PolyExecConfig) -> None:
        """Initialize with execution config.

        Args:
            config: Polymarket execution venue configuration.
        """
        self._config = config
        self._private_key: str = os.environ.get("POLY_PRIVATE_KEY", "")
        self._api_key: str = os.environ.get("POLY_API_KEY", "")
        self._api_secret: str = os.environ.get("POLY_API_SECRET", "")
        self._api_passphrase: str = os.environ.get("POLY_API_PASSPHRASE", "")
        self._funder: str = os.environ.get("POLY_FUNDER", config.funder).strip()
        self._signature_type: int = _resolve_signature_type(
            os.environ.get("POLY_SIGNATURE_TYPE"),
            config.signature_type,
            has_funder=bool(self._funder),
        )
        self._client: Any = None

    def is_configured(self) -> bool:
        """Return True if required signing config is available.

        Returns:
            Whether private key (and method-2 funder when needed) are set.
        """
        if not self._private_key:
            return False
        if self._signature_type != 0 and not self._funder:
            return False
        return True

    def _has_api_creds(self) -> bool:
        """Return True if all three CLOB API credentials are set.

        Returns:
            Whether Level 2 API credentials are available.
        """
        return bool(self._api_key and self._api_secret and self._api_passphrase)

    async def _ensure_client(self) -> Any:
        """Lazily initialize the CLOB client.

        Returns:
            Initialized ClobClient instance.

        Raises:
            RuntimeError: If credentials are not configured.
        """
        if self._client is not None:
            return self._client
        if not self.is_configured():
            if not self._private_key:
                raise RuntimeError("POLY_PRIVATE_KEY not set")
            raise RuntimeError(
                "POLY_FUNDER must be set when POLY_SIGNATURE_TYPE != 0 "
                "(method-2 / proxy-wallet accounts)"
            )
        try:
            from py_clob_client.client import ClobClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "py-clob-client not installed. Run: uv add py-clob-client",
            ) from exc
        creds: Any = None
        if self._has_api_creds():
            from py_clob_client.clob_types import ApiCreds  # type: ignore[import-untyped]

            creds = ApiCreds(
                api_key=self._api_key,
                api_secret=self._api_secret,
                api_passphrase=self._api_passphrase,
            )
            logger.info("polymarket_using_api_creds")
        self._client = ClobClient(
            self._config.clob_api_url,
            key=self._private_key,
            chain_id=self._config.chain_id,
            creds=creds,
            signature_type=self._signature_type,
            funder=self._funder or None,
        )
        logger.info(
            "polymarket_executor_initialized",
            signature_type=self._signature_type,
            has_funder=bool(self._funder),
        )
        return self._client

    async def _ensure_level2_client(self) -> Any:
        """Ensure client has Level-2 API credentials.

        If credentials are not provided via env, tries to create/derive
        them using the signer key and configured signature mode.
        """
        client = await self._ensure_client()
        if self._has_api_creds():
            return client

        loop = asyncio.get_running_loop()
        creds = await loop.run_in_executor(None, client.create_or_derive_api_creds)
        if creds is None:
            raise RuntimeError(
                "Failed to derive Polymarket API credentials. "
                "Set POLY_API_KEY / POLY_API_SECRET / POLY_API_PASSPHRASE."
            )
        client.set_api_creds(creds)
        self._api_key = creds.api_key
        self._api_secret = creds.api_secret
        self._api_passphrase = creds.api_passphrase
        logger.info("polymarket_api_creds_derived")
        return client

    async def place_order(self, req: OrderRequest) -> OrderResponse:
        """Place a GTC limit order on Polymarket CLOB.

        Args:
            req: Order parameters including token_id and side.

        Returns:
            OrderResponse with venue order ID or error.
        """
        try:
            if not req.token_id:
                raise ValueError("Missing Polymarket token_id in execution ticket leg")
            client = await self._ensure_level2_client()
            from py_clob_client.clob_types import OrderArgs  # type: ignore[import-untyped,unused-ignore]

            side_str = "BUY" if req.side.startswith("buy") else "SELL"
            order_args = OrderArgs(
                token_id=req.token_id,
                price=float(req.price),
                size=float(req.size_contracts),
                side=side_str,
            )
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None,
                client.create_and_post_order,
                order_args,
            )
            order_id = str(resp.get("orderID", resp.get("id", "")))
            raw_fill = resp.get("averagePrice") or resp.get("price")
            fill_price = Decimal(str(raw_fill)) if raw_fill else None
            logger.info(
                "poly_order_placed",
                order_id=order_id,
                side=side_str,
                price=str(req.price),
                fill_price=str(fill_price) if fill_price else None,
                size=req.size_contracts,
            )
            return OrderResponse(
                venue_order_id=order_id,
                status="submitted",
                fill_price=fill_price,
            )
        except Exception as exc:
            error_str = str(exc)
            cause = str(exc.__cause__) if exc.__cause__ else None
            if _is_geoblock(error_str):
                logger.error(
                    "poly_order_geoblock",
                    error=error_str,
                    error_type=type(exc).__name__,
                    cause=cause,
                    token_id=req.token_id,
                    side=req.side,
                    price=str(req.price),
                    size=req.size_contracts,
                    exc_info=True,
                )
                return OrderResponse(status="failed", error_message=f"GEOBLOCK: {error_str[:490]}")
            logger.error(
                "poly_order_failed",
                error=error_str,
                error_type=type(exc).__name__,
                cause=cause,
                token_id=req.token_id,
                side=req.side,
                price=str(req.price),
                size=req.size_contracts,
                clob_url=self._config.clob_api_url,
                exc_info=True,
            )
            return OrderResponse(status="failed", error_message=error_str[:500])

    async def cancel_order(self, venue_order_id: str) -> bool:
        """Cancel a pending order on Polymarket.

        Args:
            venue_order_id: The CLOB order ID.

        Returns:
            True if cancellation succeeded.
        """
        try:
            client = await self._ensure_level2_client()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, client.cancel, venue_order_id)
            logger.info("poly_order_cancelled", order_id=venue_order_id)
            return True
        except Exception as exc:
            logger.error(
                "poly_cancel_failed",
                order_id=venue_order_id,
                error=str(exc),
                error_type=type(exc).__name__,
                cause=str(exc.__cause__) if exc.__cause__ else None,
                exc_info=True,
            )
            return False

    async def get_balance(self) -> Decimal:
        """Fetch USDC collateral balance on Polygon.

        Runs the synchronous SDK call in a thread to avoid blocking
        the event loop.

        Returns:
            Available USDC balance.
        """
        try:
            logger.info(
                "poly_balance_fetch_start",
                has_key=bool(self._private_key),
                has_api_creds=self._has_api_creds(),
                signature_type=self._signature_type,
                has_funder=bool(self._funder),
            )
            from py_clob_client.clob_types import (
                AssetType,
                BalanceAllowanceParams,
            )

            client = await self._ensure_level2_client()
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=self._signature_type,
            )
            loop = asyncio.get_running_loop()
            resp: Any = await loop.run_in_executor(
                None,
                client.get_balance_allowance,
                params,
            )
            bal = resp.get("balance", "0") if isinstance(resp, dict) else "0"
            raw = Decimal(str(bal))
            result = (raw / _USDC_SCALE).quantize(Decimal("0.01"))
            logger.info("poly_balance_ok", balance=str(result), raw_balance=str(raw))
            return result
        except Exception as exc:
            logger.error(
                "poly_balance_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                cause=str(exc.__cause__) if exc.__cause__ else None,
                has_key=bool(self._private_key),
                has_api_creds=self._has_api_creds(),
                exc_info=True,
            )
            return _ZERO

    async def get_book_depth(self, token_or_ticker: str) -> dict[str, Any]:
        """Fetch the full CLOB order book.

        Args:
            token_or_ticker: CLOB token ID.

        Returns:
            Order book with bids and asks arrays.
        """
        try:
            client = await self._ensure_level2_client()
            loop = asyncio.get_running_loop()
            raw: Any = await loop.run_in_executor(
                None,
                client.get_order_book,
                token_or_ticker,
            )
            return _normalize_poly_orderbook(raw)
        except Exception as exc:
            logger.error(
                "poly_book_failed",
                token=token_or_ticker,
                error=str(exc),
                error_type=type(exc).__name__,
                cause=str(exc.__cause__) if exc.__cause__ else None,
                exc_info=True,
            )
            return {"bids": [], "asks": []}


def _is_geoblock(error_str: str) -> bool:
    """Return True if the error string indicates a geographic restriction."""
    low = error_str.lower()
    return "restricted in your region" in low or "geoblock" in low


def _coerce_signature_type(raw: str | None, default: int) -> int:
    """Parse POLY_SIGNATURE_TYPE into an integer, falling back safely."""
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("invalid_poly_signature_type", value=raw, fallback=default)
        return default


def _resolve_signature_type(raw: str | None, default: int, *, has_funder: bool) -> int:
    """Resolve effective signature type with safe method-2 fallback.

    If signature type is unset and a funder is present, default to method-2
    behavior to avoid silently querying an EOA balance context.
    """
    parsed = _coerce_signature_type(raw, default)
    if (raw is None or not raw.strip()) and parsed == 0 and has_funder:
        logger.warning(
            "poly_signature_type_defaulted_for_funder",
            fallback=2,
        )
        return 2
    return parsed


def _normalize_poly_orderbook(raw: Any) -> dict[str, Any]:
    """Normalize SDK orderbook types into a dict with bids/asks lists."""
    if isinstance(raw, dict):
        return {
            "bids": _normalize_poly_levels(raw.get("bids", [])),
            "asks": _normalize_poly_levels(raw.get("asks", [])),
        }
    bids = _normalize_poly_levels(getattr(raw, "bids", []))
    asks = _normalize_poly_levels(getattr(raw, "asks", []))
    return {"bids": bids, "asks": asks}


def _normalize_poly_levels(levels: Any) -> list[dict[str, str]]:
    """Convert SDK order levels into {'price','size'} dict levels."""
    if not isinstance(levels, list):
        return []
    out: list[dict[str, str]] = []
    for lv in levels:
        if isinstance(lv, dict):
            price = lv.get("price")
            size = lv.get("size")
        else:
            price = getattr(lv, "price", None)
            size = getattr(lv, "size", None)
        if price is None or size is None:
            continue
        out.append({"price": str(price), "size": str(size)})
    return out
