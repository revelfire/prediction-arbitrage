"""Unit tests for KalshiExecutor orderbook normalization."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from arb_scanner.execution.kalshi_executor import KalshiExecutor
from arb_scanner.models.config import KalshiExecConfig


@pytest.mark.asyncio()
async def test_get_book_depth_normalizes_yes_no_levels() -> None:
    """Kalshi yes/no bid ladders are normalized into side-aware asks."""
    executor = KalshiExecutor(KalshiExecConfig())
    mock_http = Mock()
    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json = Mock(
        return_value={
            "orderbook": {
                "yes": [[0.20, 10], [0.30, 20]],
                "no": [[0.60, 15], [0.70, 25]],
            }
        }
    )
    mock_http.get = AsyncMock(return_value=mock_resp)
    executor._get_http = AsyncMock(return_value=mock_http)  # type: ignore[method-assign]

    book = await executor.get_book_depth("KXTEST")

    assert book["asks_yes"][0]["price"] == "0.3"
    assert book["asks_yes"][0]["size"] == "25"
    assert book["asks_no"][0]["price"] == "0.7"
    assert book["asks_no"][0]["size"] == "20"


@pytest.mark.asyncio()
async def test_get_book_depth_passthrough_for_asks_books() -> None:
    """Books already carrying asks are returned unchanged."""
    executor = KalshiExecutor(KalshiExecConfig())
    mock_http = Mock()
    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json = Mock(return_value={"orderbook": {"asks": [{"price": "0.5", "size": "10"}]}})
    mock_http.get = AsyncMock(return_value=mock_resp)
    executor._get_http = AsyncMock(return_value=mock_http)  # type: ignore[method-assign]

    book = await executor.get_book_depth("KXTEST")

    assert book["asks"][0]["price"] == "0.5"
