"""Unit tests for auto-execution API routes."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
    """Build a minimal Settings for testing."""
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


def _make_client(
    *,
    pipeline: Any = None,
    breakers: CircuitBreakerManager | None = None,
    auto_repo: Any = None,
) -> TestClient:
    """Build a test client with mocked dependencies."""
    config = _test_config()

    with patch("arb_scanner.storage.db.Database.connect", new_callable=AsyncMock):
        with patch("arb_scanner.storage.db.Database.disconnect", new_callable=AsyncMock):
            app = create_app(config, no_db=True)

    app.state.config = config
    if pipeline is not None:
        app.state.arb_pipeline = pipeline
    if breakers is not None:
        app.state.arb_breakers = breakers
        app.state.flip_breakers = breakers
        app.state.circuit_breakers = breakers
    if auto_repo is not None:
        app.state.auto_exec_repo = auto_repo

    mock_repo = auto_repo or AsyncMock()
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_auto_exec_repo] = lambda: mock_repo

    return TestClient(app)


class TestGetStatus:
    """Tests for GET /api/auto-execution/status."""

    def test_returns_config_no_pipeline(self) -> None:
        """Returns config with mode=off when no pipeline."""
        client = _make_client()
        resp = client.get("/api/auto-execution/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["mode"] == "off"
        assert data["initialised"] is False
        assert "config" in data
        assert "critic" in data

    def test_returns_initialised_with_pipeline(self) -> None:
        """Returns initialised=True with active pipeline."""
        pipeline = MagicMock()
        pipeline.mode = "auto"
        breakers = CircuitBreakerManager(AutoExecutionConfig())
        client = _make_client(pipeline=pipeline, breakers=breakers)
        resp = client.get("/api/auto-execution/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["initialised"] is True
        assert data["mode"] == "auto"
        assert len(data["arb_breakers"]) == 3


class TestEnableAutoExec:
    """Tests for POST /api/auto-execution/enable."""

    def test_changes_mode(self) -> None:
        """Enable endpoint sets pipeline mode."""
        pipeline = MagicMock()
        pipeline.mode = "auto"
        client = _make_client(pipeline=pipeline)
        resp = client.post(
            "/api/auto-execution/enable",
            json={"mode": "auto"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        pipeline.set_mode.assert_called_once_with("auto")

    def test_no_pipeline_returns_503(self) -> None:
        """Returns 503 when pipeline not initialised."""
        client = _make_client()
        resp = client.post(
            "/api/auto-execution/enable",
            json={"mode": "auto"},
        )
        assert resp.status_code == 503


class TestDisableAutoExec:
    """Tests for POST /api/auto-execution/disable."""

    def test_kills_pipeline(self) -> None:
        """Disable endpoint calls kill() on pipeline."""
        pipeline = MagicMock()
        client = _make_client(pipeline=pipeline)
        resp = client.post("/api/auto-execution/disable")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "off"
        assert data["status"] == "killed"
        pipeline.kill.assert_called_once()

    def test_no_pipeline_returns_503(self) -> None:
        """Returns 503 when pipeline not initialised."""
        client = _make_client()
        resp = client.post("/api/auto-execution/disable")
        assert resp.status_code == 503


class TestGetLog:
    """Tests for GET /api/auto-execution/log."""

    def test_returns_entries(self) -> None:
        """Returns log entries from repository."""
        repo = AsyncMock()
        repo.list_log = AsyncMock(
            return_value=[
                {"id": "log1", "arb_id": "t1", "status": "executed"},
            ]
        )
        client = _make_client(auto_repo=repo)
        resp = client.get("/api/auto-execution/log")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "log1"

    def test_empty_log(self) -> None:
        """Returns empty list when no log entries."""
        repo = AsyncMock()
        repo.list_log = AsyncMock(return_value=[])
        client = _make_client(auto_repo=repo)
        resp = client.get("/api/auto-execution/log")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetPositions:
    """Tests for GET /api/auto-execution/positions."""

    def test_returns_positions(self) -> None:
        """Returns open positions from repository."""
        repo = AsyncMock()
        repo.get_open_positions = AsyncMock(
            return_value=[
                {"id": "p1", "arb_id": "t1", "status": "open"},
            ]
        )
        client = _make_client(auto_repo=repo)
        resp = client.get("/api/auto-execution/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "open"


class TestGetStats:
    """Tests for GET /api/auto-execution/stats."""

    def test_returns_aggregates(self) -> None:
        """Returns stats dict from repository."""
        repo = AsyncMock()
        repo.get_daily_stats = AsyncMock(
            return_value={
                "total_trades": 10,
                "wins": 7,
                "losses": 3,
                "total_pnl": Decimal("25.50"),
                "avg_spread": Decimal("0.04"),
                "avg_slippage": Decimal("0.005"),
                "critic_rejections": 2,
                "breaker_trips": 1,
            }
        )
        client = _make_client(auto_repo=repo)
        resp = client.get("/api/auto-execution/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_trades"] == 10
        assert data["wins"] == 7


class TestResetCircuitBreaker:
    """Tests for POST /api/auto-execution/circuit-breaker/reset."""

    def test_reset_anomaly(self) -> None:
        """Resets the anomaly breaker."""
        breakers = CircuitBreakerManager(AutoExecutionConfig())
        breakers.check_anomaly(0.99)
        assert breakers.is_any_tripped() is True

        client = _make_client(pipeline=MagicMock(), breakers=breakers)
        resp = client.post(
            "/api/auto-execution/circuit-breaker/reset",
            json={"breaker_type": "anomaly"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "reset"
        assert breakers.is_any_tripped() is False

    def test_reset_all(self) -> None:
        """Resets all breakers."""
        config = AutoExecutionConfig(daily_loss_limit_usd=10.0)
        breakers = CircuitBreakerManager(config)
        breakers.check_loss(Decimal("-50"))
        assert breakers.is_any_tripped() is True

        client = _make_client(pipeline=MagicMock(), breakers=breakers)
        resp = client.post(
            "/api/auto-execution/circuit-breaker/reset",
            json={"breaker_type": "all"},
        )
        assert resp.status_code == 200
        assert breakers.is_any_tripped() is False

    def test_unknown_breaker_type_returns_400(self) -> None:
        """Returns 400 for unknown breaker type."""
        breakers = CircuitBreakerManager(AutoExecutionConfig())
        client = _make_client(pipeline=MagicMock(), breakers=breakers)
        resp = client.post(
            "/api/auto-execution/circuit-breaker/reset",
            json={"breaker_type": "unknown"},
        )
        assert resp.status_code == 400

    def test_no_breakers_returns_503(self) -> None:
        """Returns 503 when no breakers on app state."""
        client = _make_client()
        resp = client.post(
            "/api/auto-execution/circuit-breaker/reset",
            json={"breaker_type": "anomaly"},
        )
        assert resp.status_code == 503
