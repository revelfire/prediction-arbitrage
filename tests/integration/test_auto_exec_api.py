"""Integration tests for auto-execution API routes end-to-end.

Tests the full HTTP request -> route -> dependency -> serialized response
cycle for all auto-execution endpoints, with mocked repositories.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from arb_scanner.api.app import create_app
from arb_scanner.api.deps import get_auto_exec_repo, get_config
from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
from arb_scanner.models._auto_exec_config import AutoExecutionConfig
from arb_scanner.models.config import (
    ExecutionConfig,
    FeeSchedule,
    FeesConfig,
    Settings,
    StorageConfig,
)


def _test_config() -> Settings:
    """Build a minimal Settings for integration testing."""
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
        execution=ExecutionConfig(enabled=False),
        auto_execution=AutoExecutionConfig(enabled=True, mode="auto"),
    )


def _realistic_log_entries() -> list[dict[str, Any]]:
    """Return realistic log entry dicts for testing."""
    return [
        {
            "id": "log-001",
            "arb_id": "arb-001",
            "trigger_spread_pct": "0.05",
            "trigger_confidence": "0.85",
            "size_usd": "25.00",
            "status": "executed",
            "source": "arb_watch",
            "duration_ms": 120,
            "created_at": "2026-02-27T12:00:00Z",
        },
        {
            "id": "log-002",
            "arb_id": "arb-002",
            "trigger_spread_pct": "0.04",
            "trigger_confidence": "0.78",
            "size_usd": "0",
            "status": "rejected",
            "source": "flippening",
            "duration_ms": 5,
            "created_at": "2026-02-27T12:01:00Z",
        },
    ]


def _realistic_positions() -> list[dict[str, Any]]:
    """Return realistic open position dicts for testing."""
    return [
        {
            "id": "pos-001",
            "arb_id": "arb-001",
            "poly_market_id": "m1",
            "kalshi_ticker": "KXTICKER",
            "entry_spread": "0.05",
            "entry_cost_usd": "25.00",
            "current_value_usd": "26.50",
            "status": "open",
            "opened_at": "2026-02-27T12:00:00Z",
        },
    ]


def _realistic_stats() -> dict[str, Any]:
    """Return realistic stats dict for testing."""
    return {
        "total_trades": 15,
        "wins": 10,
        "losses": 5,
        "total_pnl": Decimal("42.50"),
        "avg_spread": Decimal("0.045"),
        "avg_slippage": Decimal("0.006"),
        "critic_rejections": 3,
        "breaker_trips": 1,
    }


@pytest.fixture()
def auto_repo() -> AsyncMock:
    """Create a mock AutoExecRepository."""
    repo = AsyncMock()
    repo.list_log = AsyncMock(return_value=_realistic_log_entries())
    repo.get_open_positions = AsyncMock(return_value=_realistic_positions())
    repo.get_daily_stats = AsyncMock(return_value=_realistic_stats())
    return repo


@pytest.fixture()
def client(auto_repo: AsyncMock) -> TestClient:
    """Build a TestClient with mocked dependencies."""
    config = _test_config()

    with (
        patch("arb_scanner.storage.db.Database.connect", new_callable=AsyncMock),
        patch("arb_scanner.storage.db.Database.disconnect", new_callable=AsyncMock),
    ):
        app = create_app(config, no_db=True)

    app.state.config = config

    # Set up pipeline mocks (split pipelines)
    pipeline = MagicMock()
    pipeline.mode = "auto"
    pipeline.set_mode = MagicMock()
    pipeline.kill = MagicMock()
    app.state.arb_pipeline = pipeline

    flip_pipeline = MagicMock()
    flip_pipeline.mode = "auto"
    flip_pipeline.set_mode = MagicMock()
    flip_pipeline.kill = MagicMock()
    app.state.flip_pipeline = flip_pipeline

    # Set up per-pipeline breakers
    arb_breakers = CircuitBreakerManager(config.auto_execution)
    flip_breakers = CircuitBreakerManager(config.auto_execution)
    app.state.arb_breakers = arb_breakers
    app.state.flip_breakers = flip_breakers

    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_auto_exec_repo] = lambda: auto_repo

    return TestClient(app)


class TestAutoExecAPIIntegration:
    """Integration tests for auto-execution API endpoints."""

    def test_status_returns_full_config(self, client: TestClient) -> None:
        """GET /api/auto-execution/status returns complete config snapshot."""
        resp = client.get("/api/auto-execution/status")
        assert resp.status_code == 200

        data = resp.json()
        assert data["enabled"] is True
        assert data["mode"] == "auto"
        assert data["initialised"] is True

        config = data["config"]
        assert "min_spread_pct" in config
        assert "max_spread_pct" in config
        assert "max_size_usd" in config
        assert "daily_loss_limit_usd" in config

        assert "critic" in data
        assert data["critic"]["enabled"] is True

        assert "arb_breakers" in data
        assert len(data["arb_breakers"]) == 3
        for cb in data["arb_breakers"]:
            assert "breaker_type" in cb
            assert "tripped" in cb
        assert "flip_breakers" in data
        assert len(data["flip_breakers"]) == 3

    def test_enable_then_disable_flow(self, client: TestClient) -> None:
        """Full enable/disable lifecycle works end-to-end."""
        # Enable in auto mode
        resp1 = client.post(
            "/api/auto-execution/enable",
            json={"mode": "auto"},
        )
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "ok"

        # Kill switch
        resp2 = client.post("/api/auto-execution/disable")
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "killed"
        assert resp2.json()["mode"] == "off"

    def test_log_response_shape(self, client: TestClient) -> None:
        """GET /api/auto-execution/log returns entries with expected keys."""
        resp = client.get("/api/auto-execution/log")
        assert resp.status_code == 200

        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == "log-001"
        assert data[0]["status"] == "executed"
        assert data[1]["status"] == "rejected"

    def test_log_with_limit(self, client: TestClient) -> None:
        """GET /api/auto-execution/log?limit=1 passes limit param."""
        resp = client.get("/api/auto-execution/log?limit=1")
        assert resp.status_code == 200

    def test_positions_response_shape(self, client: TestClient) -> None:
        """GET /api/auto-execution/positions returns position data."""
        resp = client.get("/api/auto-execution/positions")
        assert resp.status_code == 200

        data = resp.json()
        assert len(data) == 1
        pos = data[0]
        assert pos["id"] == "pos-001"
        assert pos["status"] == "open"
        assert "entry_spread" in pos
        assert "entry_cost_usd" in pos

    def test_stats_response_shape(self, client: TestClient) -> None:
        """GET /api/auto-execution/stats returns aggregate stats."""
        resp = client.get("/api/auto-execution/stats")
        assert resp.status_code == 200

        data = resp.json()
        assert data["total_trades"] == 15
        assert data["wins"] == 10
        assert data["losses"] == 5
        # Decimal values serialized as strings
        assert data["total_pnl"] == "42.50"

    def test_breaker_reset_and_verify(self, client: TestClient) -> None:
        """Reset anomaly breaker and verify state via status endpoint."""
        # Trip the anomaly breaker on arb breakers
        breakers = client.app.state.arb_breakers  # type: ignore[union-attr]
        breakers.check_anomaly(0.99)
        assert breakers.is_any_tripped() is True

        # The reset endpoint uses app.state.circuit_breakers — set it
        client.app.state.circuit_breakers = breakers  # type: ignore[union-attr]

        # Reset it
        resp = client.post(
            "/api/auto-execution/circuit-breaker/reset",
            json={"breaker_type": "anomaly"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "reset"

        # Verify via status endpoint
        status_resp = client.get("/api/auto-execution/status")
        data = status_resp.json()
        arb_states = data["arb_breakers"]
        anomaly = next(b for b in arb_states if b["breaker_type"] == "anomaly")
        assert anomaly["tripped"] is False

    def test_stats_with_days_param(self, client: TestClient) -> None:
        """GET /api/auto-execution/stats?days=30 passes days param."""
        resp = client.get("/api/auto-execution/stats?days=30")
        assert resp.status_code == 200

    def test_invalid_mode_returns_422(self, client: TestClient) -> None:
        """POST /api/auto-execution/enable with bad mode returns 422."""
        resp = client.post(
            "/api/auto-execution/enable",
            json={"mode": "turbo"},
        )
        assert resp.status_code == 422
