"""Tests for backtesting dashboard data routes."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from arb_scanner.api.app import create_app
from arb_scanner.api.deps import get_backtest_repo, get_config, get_flip_repo
from arb_scanner.models.config import FeeSchedule, FeesConfig, Settings, StorageConfig

_NOW = datetime.now(tz=timezone.utc)


def _test_config() -> Settings:
    return Settings(
        storage=StorageConfig(database_url="postgresql://test:test@localhost/test"),
        fees=FeesConfig(
            polymarket=FeeSchedule(
                taker_fee_pct=Decimal("0.02"),
                fee_model="percent_winnings",
            ),
            kalshi=FeeSchedule(
                taker_fee_pct=Decimal("0.07"),
                fee_model="per_contract",
            ),
        ),
    )


def test_signal_comparison_uses_actual_matches() -> None:
    """Signal comparison counts actual trade-to-signal matches, not heuristics."""
    config = _test_config()
    backtest_repo = AsyncMock()
    backtest_repo.get_trades.return_value = [
        {
            "market_name": "Will BTC be above $80k?",
            "action": "Buy",
            "usdc_amount": Decimal("10"),
            "token_amount": Decimal("20"),
            "token_name": "Yes",
            "timestamp": _NOW,
            "tx_hash": "0xabc",
            "condition_id": None,
            "imported_at": _NOW,
        }
    ]
    flip_repo = AsyncMock()
    flip_repo.get_history.return_value = [
        {
            "market_title": "Will BTC be above $80k?",
            "side": "yes",
            "entry_at": _NOW,
            "realized_pnl": Decimal("1.25"),
            "confidence": Decimal("0.78"),
        }
    ]

    with (
        patch("arb_scanner.storage.db.Database.connect", new_callable=AsyncMock),
        patch("arb_scanner.storage.db.Database.disconnect", new_callable=AsyncMock),
    ):
        app = create_app(config)
        app.dependency_overrides[get_backtest_repo] = lambda: backtest_repo
        app.dependency_overrides[get_flip_repo] = lambda: flip_repo
        app.dependency_overrides[get_config] = lambda: config
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/backtesting/signal-comparison")

    assert resp.status_code == 200
    data = resp.json()
    assert data["aligned"]["count"] == 1
    assert data["aligned"]["total_pnl"] == 1.25
    assert data["contrary"]["count"] == 0
    assert data["no_signal"]["count"] == 0
