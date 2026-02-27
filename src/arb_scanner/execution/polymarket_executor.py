"""Polymarket CLOB order execution via py-clob-client SDK."""

from __future__ import annotations

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
        self._client: Any = None

    def is_configured(self) -> bool:
        """Return True if a private key is available.

        Returns:
            Whether credentials are set.
        """
        return bool(self._private_key)

    async def _ensure_client(self) -> Any:
        """Lazily initialize the CLOB client.

        Returns:
            Initialized ClobClient instance.

        Raises:
            RuntimeError: If credentials are not configured.
        """
        if self._client is not None:
            return self._client
        if not self._private_key:
            raise RuntimeError("POLY_PRIVATE_KEY not set")
        try:
            from py_clob_client.client import ClobClient  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("py-clob-client not installed. Run: uv add py-clob-client") from exc
        self._client = ClobClient(
            self._config.clob_api_url,
            key=self._private_key,
            chain_id=self._config.chain_id,
        )
        logger.info("polymarket_executor_initialized")
        return self._client

    async def place_order(self, req: OrderRequest) -> OrderResponse:
        """Place a GTC limit order on Polymarket CLOB.

        Args:
            req: Order parameters including token_id and side.

        Returns:
            OrderResponse with venue order ID or error.
        """
        try:
            client = await self._ensure_client()
            side_str = "BUY" if req.side.startswith("buy") else "SELL"
            resp = client.create_and_post_order(
                token_id=req.token_id,
                price=float(req.price),
                size=req.size_contracts,
                side=side_str,
            )
            order_id = str(resp.get("orderID", resp.get("id", "")))
            logger.info(
                "poly_order_placed",
                order_id=order_id,
                side=side_str,
                price=str(req.price),
                size=req.size_contracts,
            )
            return OrderResponse(
                venue_order_id=order_id,
                status="submitted",
            )
        except Exception as exc:
            logger.error("poly_order_failed", error=str(exc))
            return OrderResponse(
                status="failed",
                error_message=str(exc)[:500],
            )

    async def cancel_order(self, venue_order_id: str) -> bool:
        """Cancel a pending order on Polymarket.

        Args:
            venue_order_id: The CLOB order ID.

        Returns:
            True if cancellation succeeded.
        """
        try:
            client = await self._ensure_client()
            client.cancel(venue_order_id)
            logger.info("poly_order_cancelled", order_id=venue_order_id)
            return True
        except Exception as exc:
            logger.error("poly_cancel_failed", order_id=venue_order_id, error=str(exc))
            return False

    async def get_balance(self) -> Decimal:
        """Fetch USDC balance on Polygon.

        Returns:
            Available USDC balance.
        """
        try:
            client = await self._ensure_client()
            bal = client.get_balance()
            result = Decimal(str(bal))
            logger.debug("poly_balance", balance=str(result))
            return result
        except Exception as exc:
            logger.error("poly_balance_failed", error=str(exc))
            return _ZERO

    async def get_book_depth(self, token_or_ticker: str) -> dict[str, Any]:
        """Fetch the full CLOB order book.

        Args:
            token_or_ticker: CLOB token ID.

        Returns:
            Order book with bids and asks arrays.
        """
        try:
            client = await self._ensure_client()
            book: dict[str, Any] = client.get_order_book(token_or_ticker)
            return book
        except Exception as exc:
            logger.error("poly_book_failed", token=token_or_ticker, error=str(exc))
            return {"bids": [], "asks": []}
