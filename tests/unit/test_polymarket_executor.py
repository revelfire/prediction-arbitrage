"""Unit tests for PolymarketExecutor method-2 and order paths."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from py_clob_client.clob_types import OrderArgs

from arb_scanner.execution.polymarket_executor import (
    PolymarketExecutor,
    _extract_fill_price,
    _map_poly_order_status,
)
from arb_scanner.models.config import PolyExecConfig
from arb_scanner.models.execution import OrderRequest


def _make_order_request(*, token_id: str) -> OrderRequest:
    """Build a standard Polymarket order request."""
    return OrderRequest(
        venue="polymarket",
        side="buy_yes",
        price=Decimal("0.51"),
        size_usd=Decimal("10"),
        size_contracts=20,
        token_id=token_id,
    )


def test_is_configured_requires_funder_for_method2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Method-2 configuration must include funder address."""
    monkeypatch.setenv("POLY_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("POLY_SIGNATURE_TYPE", "2")
    monkeypatch.delenv("POLY_FUNDER", raising=False)
    executor = PolymarketExecutor(PolyExecConfig())
    assert executor.is_configured() is False


def test_signature_type_defaults_to_method2_when_funder_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When funder is configured without signature type, default to method-2."""
    monkeypatch.setenv("POLY_PRIVATE_KEY", "0xabc")
    monkeypatch.delenv("POLY_SIGNATURE_TYPE", raising=False)
    monkeypatch.setenv("POLY_FUNDER", "0xFunderAddr")
    executor = PolymarketExecutor(PolyExecConfig())
    assert executor._signature_type == 2


@pytest.mark.asyncio()
async def test_ensure_client_forwards_method2_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """ClobClient is initialized with signature_type and funder for method-2 accounts."""
    monkeypatch.setenv("POLY_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("POLY_SIGNATURE_TYPE", "2")
    monkeypatch.setenv("POLY_FUNDER", "0xFunderAddr")
    with patch("py_clob_client.client.ClobClient") as mock_client_cls:
        executor = PolymarketExecutor(PolyExecConfig())
        await executor._ensure_client()
        kwargs = mock_client_cls.call_args.kwargs
        assert kwargs["signature_type"] == 2
        assert kwargs["funder"] == "0xFunderAddr"


@pytest.mark.asyncio()
async def test_ensure_level2_client_derives_creds_when_missing() -> None:
    """Missing env API creds are derived and set on the client."""
    executor = PolymarketExecutor(PolyExecConfig())
    creds = SimpleNamespace(api_key="k", api_secret="s", api_passphrase="p")
    client = Mock()
    client.create_or_derive_api_creds = Mock(return_value=creds)
    client.set_api_creds = Mock()
    executor._ensure_client = AsyncMock(return_value=client)  # type: ignore[method-assign]

    resolved = await executor._ensure_level2_client()

    assert resolved is client
    client.set_api_creds.assert_called_once_with(creds)
    assert executor._api_key == "k"
    assert executor._api_secret == "s"
    assert executor._api_passphrase == "p"


@pytest.mark.asyncio()
async def test_place_order_uses_order_args() -> None:
    """Order placement sends an OrderArgs object to the SDK client."""
    executor = PolymarketExecutor(PolyExecConfig())
    client = Mock()
    client.create_and_post_order = Mock(return_value={"orderID": "poly-order-1"})
    executor._ensure_level2_client = AsyncMock(return_value=client)  # type: ignore[method-assign]

    resp = await executor.place_order(_make_order_request(token_id="tok-123"))

    assert resp.status == "submitted"
    payload = client.create_and_post_order.call_args.args[0]
    assert isinstance(payload, OrderArgs)
    assert payload.token_id == "tok-123"
    assert payload.side == "BUY"


@pytest.mark.asyncio()
async def test_place_order_fails_when_token_id_missing() -> None:
    """Order placement fails early when ticket lacks token_id."""
    executor = PolymarketExecutor(PolyExecConfig())
    executor._ensure_level2_client = AsyncMock()  # type: ignore[method-assign]

    resp = await executor.place_order(_make_order_request(token_id=""))

    assert resp.status == "failed"
    assert resp.error_message is not None
    assert "token_id" in resp.error_message
    executor._ensure_level2_client.assert_not_awaited()


@pytest.mark.asyncio()
async def test_get_balance_converts_micro_usdc_to_usd() -> None:
    """Balance allowance amount is converted from 6-decimal units to USD."""
    executor = PolymarketExecutor(PolyExecConfig())
    client = Mock()
    client.get_balance_allowance = Mock(return_value={"balance": "1000000000"})
    executor._ensure_level2_client = AsyncMock(return_value=client)  # type: ignore[method-assign]

    balance = await executor.get_balance()

    assert balance == Decimal("1000.00")


@pytest.mark.asyncio()
async def test_get_book_depth_normalizes_sdk_levels() -> None:
    """Orderbook objects from the SDK are normalized to dict levels."""
    executor = PolymarketExecutor(PolyExecConfig())
    book_obj = SimpleNamespace(
        bids=[SimpleNamespace(price="0.49", size="100")],
        asks=[SimpleNamespace(price="0.51", size="120")],
    )
    client = Mock()
    client.get_order_book = Mock(return_value=book_obj)
    executor._ensure_level2_client = AsyncMock(return_value=client)  # type: ignore[method-assign]

    book = await executor.get_book_depth("tok-1")

    assert book["asks"][0]["price"] == "0.51"
    assert book["asks"][0]["size"] == "120"


@pytest.mark.asyncio()
async def test_get_order_status_normalizes_filled_payload() -> None:
    """Order status endpoint is normalized into OrderResponse."""
    executor = PolymarketExecutor(PolyExecConfig())
    client = Mock()
    client.get_order = Mock(
        return_value={
            "id": "venue-1",
            "status": "completed",
            "averagePrice": "0.57",
        }
    )
    executor._ensure_level2_client = AsyncMock(return_value=client)  # type: ignore[method-assign]

    status = await executor.get_order_status("venue-1")

    assert status.venue_order_id == "venue-1"
    assert status.status == "filled"
    assert status.fill_price == Decimal("0.57")


@pytest.mark.asyncio()
async def test_get_order_status_invalid_payload_defaults_to_submitted() -> None:
    """Non-dict SDK responses degrade safely to submitted."""
    executor = PolymarketExecutor(PolyExecConfig())
    client = Mock()
    client.get_order = Mock(return_value="bad-response")
    executor._ensure_level2_client = AsyncMock(return_value=client)  # type: ignore[method-assign]

    status = await executor.get_order_status("venue-2")

    assert status.venue_order_id == "venue-2"
    assert status.status == "submitted"
    assert status.error_message == "invalid_order_response"


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("filled", "filled"),
        ("partially-filled", "partially_filled"),
        ("expired", "cancelled"),
        ("rejected", "failed"),
        ("unknown", "submitted"),
    ],
)
def test_map_poly_order_status(raw_status: str, expected: str) -> None:
    """Status mapper handles known and unknown venue status values."""
    assert _map_poly_order_status(raw_status) == expected


@pytest.mark.asyncio()
async def test_get_token_balance_returns_share_count() -> None:
    """Token balance query returns the conditional token count."""
    executor = PolymarketExecutor(PolyExecConfig())
    client = Mock()
    client.get_balance_allowance = Mock(return_value={"balance": "50"})
    executor._ensure_level2_client = AsyncMock(return_value=client)  # type: ignore[method-assign]

    balance = await executor.get_token_balance("tok-abc")

    assert balance == 50


@pytest.mark.asyncio()
async def test_get_token_balance_returns_zero_when_no_shares() -> None:
    """Token balance is zero when no shares are held."""
    executor = PolymarketExecutor(PolyExecConfig())
    client = Mock()
    client.get_balance_allowance = Mock(return_value={"balance": "0"})
    executor._ensure_level2_client = AsyncMock(return_value=client)  # type: ignore[method-assign]

    balance = await executor.get_token_balance("tok-abc")

    assert balance == 0


@pytest.mark.asyncio()
async def test_get_token_balance_returns_negative_on_error() -> None:
    """Token balance returns -1 when the API call fails."""
    executor = PolymarketExecutor(PolyExecConfig())
    executor._ensure_level2_client = AsyncMock(side_effect=RuntimeError("no key"))  # type: ignore[method-assign]

    balance = await executor.get_token_balance("tok-abc")

    assert balance == -1


def test_extract_fill_price_ignores_invalid_values() -> None:
    """Fill-price extractor skips invalid candidates and uses first valid key."""
    raw = {"avgPrice": "bad", "averagePrice": "0.611", "price": "0.55"}
    assert _extract_fill_price(raw) == Decimal("0.611")
