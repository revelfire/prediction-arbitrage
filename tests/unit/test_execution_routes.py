"""Unit tests for execution API routes."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from arb_scanner.api.app import create_app
from arb_scanner.api.deps import get_config, get_ticket_repo
from arb_scanner.models.config import (
    ExecutionConfig,
    FeeSchedule,
    FeesConfig,
    Settings,
    StorageConfig,
)
from arb_scanner.models.execution import ExecutionResult, PreflightCheck, PreflightResult


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
                fee_cap=Decimal("0.99"),
            ),
        ),
        execution=ExecutionConfig(enabled=True, max_size_usd=100.0),
    )


def _make_client(
    *,
    orch: Any = None,
    exec_repo: Any = None,
    ticket_repo: Any = None,
) -> TestClient:
    """Build a test client with mocked dependencies."""
    config = _test_config()

    with patch("arb_scanner.storage.db.Database.connect", new_callable=AsyncMock):
        with patch("arb_scanner.storage.db.Database.disconnect", new_callable=AsyncMock):
            app = create_app(config, no_db=True)

    app.state.config = config
    if orch is not None:
        app.state.execution_orchestrator = orch
    if exec_repo is not None:
        app.state.execution_repo = exec_repo

    mock_ticket_repo = ticket_repo or AsyncMock()
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_ticket_repo] = lambda: mock_ticket_repo

    return TestClient(app)


class TestExecutionStatus:
    """Tests for GET /api/execution/status."""

    def test_status_disabled(self) -> None:
        """Returns disabled when no orchestrator."""
        client = _make_client()
        resp = client.get("/api/execution/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["initialised"] is False

    def test_status_enabled(self) -> None:
        """Returns enabled with initialised orchestrator."""
        client = _make_client(orch=MagicMock())
        resp = client.get("/api/execution/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["initialised"] is True


class TestPreflight:
    """Tests for POST /api/execution/preflight/{arb_id}."""

    def test_no_orchestrator_returns_503(self) -> None:
        """Returns 503 when orchestrator not available."""
        client = _make_client()
        resp = client.post("/api/execution/preflight/t1")
        assert resp.status_code == 503

    def test_successful_preflight(self) -> None:
        """Returns preflight result."""
        orch = MagicMock()
        result = PreflightResult(
            checks=[PreflightCheck(name="test", passed=True, message="OK")],
            suggested_size_usd=Decimal("10"),
        )
        orch.preflight = AsyncMock(return_value=result)
        client = _make_client(orch=orch)
        resp = client.post("/api/execution/preflight/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["all_passed"] is True
        assert len(data["checks"]) == 1


class TestExecuteTrade:
    """Tests for POST /api/execution/execute/{arb_id}."""

    def test_no_orchestrator_returns_503(self) -> None:
        """Returns 503 when orchestrator not available."""
        client = _make_client()
        resp = client.post(
            "/api/execution/execute/t1",
            json={"size_usd": 10},
        )
        assert resp.status_code == 503

    def test_invalid_size(self) -> None:
        """Returns 400 for zero/negative size."""
        orch = MagicMock()
        ticket_repo = AsyncMock()
        ticket_repo.get_ticket = AsyncMock(return_value={"arb_id": "t1", "status": "approved"})
        client = _make_client(orch=orch, ticket_repo=ticket_repo)
        resp = client.post(
            "/api/execution/execute/t1",
            json={"size_usd": 0},
        )
        assert resp.status_code == 400

    def test_exceeds_max_size(self) -> None:
        """Returns 400 when size exceeds max."""
        orch = MagicMock()
        ticket_repo = AsyncMock()
        ticket_repo.get_ticket = AsyncMock(return_value={"arb_id": "t1", "status": "approved"})
        client = _make_client(orch=orch, ticket_repo=ticket_repo)
        resp = client.post(
            "/api/execution/execute/t1",
            json={"size_usd": 999},
        )
        assert resp.status_code == 400

    def test_ticket_not_found(self) -> None:
        """Returns 404 when ticket doesn't exist."""
        orch = MagicMock()
        ticket_repo = AsyncMock()
        ticket_repo.get_ticket = AsyncMock(return_value=None)
        client = _make_client(orch=orch, ticket_repo=ticket_repo)
        resp = client.post(
            "/api/execution/execute/t1",
            json={"size_usd": 10},
        )
        assert resp.status_code == 404

    def test_wrong_status(self) -> None:
        """Returns 409 when ticket is not executable."""
        orch = MagicMock()
        ticket_repo = AsyncMock()
        ticket_repo.get_ticket = AsyncMock(return_value={"arb_id": "t1", "status": "expired"})
        client = _make_client(orch=orch, ticket_repo=ticket_repo)
        resp = client.post(
            "/api/execution/execute/t1",
            json={"size_usd": 10},
        )
        assert resp.status_code == 409

    def test_successful_execution(self) -> None:
        """Returns execution result on success."""
        orch = MagicMock()
        result = ExecutionResult(
            id="r1",
            arb_id="t1",
            status="complete",
            total_cost_usd=Decimal("20"),
        )
        orch.execute = AsyncMock(return_value=result)
        ticket_repo = AsyncMock()
        ticket_repo.get_ticket = AsyncMock(return_value={"arb_id": "t1", "status": "approved"})
        client = _make_client(orch=orch, ticket_repo=ticket_repo)
        resp = client.post(
            "/api/execution/execute/t1",
            json={"size_usd": 10},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "complete"


class TestGetOrders:
    """Tests for GET /api/execution/orders/{arb_id}."""

    def test_no_exec_repo(self) -> None:
        """Returns 503 when exec repo not available."""
        client = _make_client()
        resp = client.get("/api/execution/orders/t1")
        assert resp.status_code == 503

    def test_returns_orders(self) -> None:
        """Returns order list."""
        exec_repo = MagicMock()
        exec_repo.get_orders_for_ticket = AsyncMock(
            return_value=[
                {"id": "o1", "venue": "polymarket"},
            ]
        )
        client = _make_client(exec_repo=exec_repo)
        resp = client.get("/api/execution/orders/t1")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestCancelOrder:
    """Tests for DELETE /api/execution/orders/{order_id}."""

    def test_no_orchestrator(self) -> None:
        """Returns 503 when orchestrator not available."""
        client = _make_client()
        resp = client.delete("/api/execution/orders/o1")
        assert resp.status_code == 503

    def test_order_not_found(self) -> None:
        """Returns 404 when order not cancellable."""
        orch = MagicMock()
        orch.cancel_order = AsyncMock(return_value=False)
        client = _make_client(orch=orch)
        resp = client.delete("/api/execution/orders/o1")
        assert resp.status_code == 404

    def test_successful_cancel(self) -> None:
        """Returns success on cancel."""
        orch = MagicMock()
        orch.cancel_order = AsyncMock(return_value=True)
        client = _make_client(orch=orch)
        resp = client.delete("/api/execution/orders/o1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"


class TestOpenOrders:
    """Tests for GET /api/execution/open-orders."""

    def test_no_exec_repo(self) -> None:
        """Returns 503 when exec repo not available."""
        client = _make_client()
        resp = client.get("/api/execution/open-orders")
        assert resp.status_code == 503

    def test_returns_list(self) -> None:
        """Returns empty list."""
        exec_repo = MagicMock()
        exec_repo.get_open_orders = AsyncMock(return_value=[])
        client = _make_client(exec_repo=exec_repo)
        resp = client.get("/api/execution/open-orders")
        assert resp.status_code == 200
        assert resp.json() == []
