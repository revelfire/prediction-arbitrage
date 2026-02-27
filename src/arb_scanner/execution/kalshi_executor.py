"""Kalshi order execution via REST API with RSA-PSS signing."""

from __future__ import annotations

import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import structlog

from arb_scanner.models.config import KalshiExecConfig
from arb_scanner.models.execution import OrderRequest, OrderResponse

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.kalshi",
)

_ZERO = Decimal("0")


class KalshiExecutor:
    """Execute trades on Kalshi's exchange via signed REST API.

    Uses RSA-PSS signing for authentication on write endpoints.
    """

    def __init__(self, config: KalshiExecConfig) -> None:
        """Initialize with execution config.

        Args:
            config: Kalshi execution venue configuration.
        """
        self._config = config
        self._api_key_id: str = os.environ.get("KALSHI_API_KEY_ID", "")
        self._rsa_key_path: str = os.environ.get("KALSHI_RSA_PRIVATE_KEY_PATH", "")
        self._private_key: Any = None
        self._http: httpx.AsyncClient | None = None

    def is_configured(self) -> bool:
        """Return True if API key ID and RSA key path are set.

        Returns:
            Whether credentials are configured.
        """
        return bool(self._api_key_id) and bool(self._rsa_key_path)

    def _load_private_key(self) -> Any:
        """Load the RSA private key from file.

        Returns:
            Loaded RSA private key object.

        Raises:
            RuntimeError: If key path is not set or file doesn't exist.
        """
        if self._private_key is not None:
            return self._private_key
        if not self._rsa_key_path:
            raise RuntimeError("KALSHI_RSA_PRIVATE_KEY_PATH not set")
        key_path = Path(self._rsa_key_path)
        if not key_path.exists():
            raise RuntimeError(f"RSA key file not found: {key_path}")
        try:
            from cryptography.hazmat.primitives.serialization import (  # type: ignore[import-not-found]
                load_pem_private_key,
            )
        except ImportError as exc:
            raise RuntimeError("cryptography not installed. Run: uv add cryptography") from exc
        self._private_key = load_pem_private_key(
            key_path.read_bytes(),
            password=None,
        )
        logger.info("kalshi_key_loaded")
        return self._private_key

    def _sign_request(
        self,
        method: str,
        path: str,
    ) -> dict[str, str]:
        """Generate RSA-PSS auth headers for a Kalshi API request.

        Args:
            method: HTTP method (GET, POST, DELETE).
            path: API path (e.g., /trade-api/v2/portfolio/orders).

        Returns:
            Dict of auth headers to include in the request.
        """
        from cryptography.hazmat.primitives import hashes  # type: ignore[import-not-found]
        from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore[import-not-found]

        key = self._load_private_key()
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method.upper()}{path}"
        signature = key.sign(
            message.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        import base64

        sig_b64 = base64.b64encode(signature).decode()
        return {
            "KALSHI-ACCESS-KEY": self._api_key_id,
            "KALSHI-ACCESS-SIGNATURE": sig_b64,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }

    async def _get_http(self) -> httpx.AsyncClient:
        """Get or create the HTTP client.

        Returns:
            Active httpx.AsyncClient.
        """
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._config.api_base_url,
                timeout=15.0,
            )
        return self._http

    async def place_order(self, req: OrderRequest) -> OrderResponse:
        """Place a limit order on Kalshi.

        Args:
            req: Order parameters including ticker and side.

        Returns:
            OrderResponse with venue order ID or error.
        """
        try:
            http = await self._get_http()
            path = "/trade-api/v2/portfolio/orders"
            headers = self._sign_request("POST", path)
            is_yes = "yes" in req.side
            price_cents = int(req.price * 100)
            body: dict[str, Any] = {
                "ticker": req.ticker,
                "action": "buy",
                "type": "limit",
                "side": "yes" if is_yes else "no",
                "yes_price" if is_yes else "no_price": price_cents,
                "count": req.size_contracts,
            }
            resp = await http.post(path, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            order = data.get("order", data)
            order_id = str(order.get("order_id", ""))
            logger.info(
                "kalshi_order_placed",
                order_id=order_id,
                ticker=req.ticker,
                price_cents=price_cents,
                count=req.size_contracts,
            )
            return OrderResponse(
                venue_order_id=order_id,
                status="submitted",
            )
        except Exception as exc:
            logger.error("kalshi_order_failed", error=str(exc))
            return OrderResponse(
                status="failed",
                error_message=str(exc)[:500],
            )

    async def cancel_order(self, venue_order_id: str) -> bool:
        """Cancel a pending order on Kalshi.

        Args:
            venue_order_id: The Kalshi order ID.

        Returns:
            True if cancellation succeeded.
        """
        try:
            http = await self._get_http()
            path = f"/trade-api/v2/portfolio/orders/{venue_order_id}"
            headers = self._sign_request("DELETE", path)
            resp = await http.delete(path, headers=headers)
            resp.raise_for_status()
            logger.info("kalshi_order_cancelled", order_id=venue_order_id)
            return True
        except Exception as exc:
            logger.error("kalshi_cancel_failed", order_id=venue_order_id, error=str(exc))
            return False

    async def get_balance(self) -> Decimal:
        """Fetch available trading balance from Kalshi.

        Returns:
            Available USD balance.
        """
        try:
            http = await self._get_http()
            path = "/trade-api/v2/portfolio/balance"
            headers = self._sign_request("GET", path)
            resp = await http.get(path, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            bal_cents = data.get("balance", 0)
            result = Decimal(str(bal_cents)) / Decimal("100")
            logger.debug("kalshi_balance", balance=str(result))
            return result
        except Exception as exc:
            logger.error("kalshi_balance_failed", error=str(exc))
            return _ZERO

    async def get_book_depth(self, token_or_ticker: str) -> dict[str, Any]:
        """Fetch the full order book from Kalshi.

        Args:
            token_or_ticker: Kalshi market ticker.

        Returns:
            Order book with bids (and computed asks).
        """
        try:
            http = await self._get_http()
            path = f"/trade-api/v2/orderbook/{token_or_ticker}"
            resp = await http.get(path)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            result: dict[str, Any] = data.get("orderbook", data)
            return result
        except Exception as exc:
            logger.error("kalshi_book_failed", ticker=token_or_ticker, error=str(exc))
            return {"bids": [], "asks": []}

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None
