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
    capital_manager: Any = None,
    flip_position_repo: Any = None,
    flip_exit_executor: Any = None,
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
    if capital_manager is not None:
        app.state.capital_manager = capital_manager
    if flip_position_repo is not None:
        app.state.flip_position_repo = flip_position_repo
    if flip_exit_executor is not None:
        app.state.flip_exit_executor = flip_exit_executor

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


class TestManualFlipExit:
    """Tests for POST /api/execution/flip-exit/{arb_id}."""

    def test_allows_retry_for_exit_failed_position(self) -> None:
        """Manual close can re-attempt a failed automated exit."""
        position_repo = AsyncMock()
        position_repo.get_position_by_arb_id = AsyncMock(
            return_value={
                "arb_id": "arb-1",
                "market_id": "m1",
                "side": "yes",
                "entry_price": Decimal("0.42"),
                "status": "exit_failed",
            }
        )
        exit_executor = AsyncMock()
        exit_executor.execute_exit = AsyncMock(return_value="order-1")

        client = _make_client(
            flip_position_repo=position_repo,
            flip_exit_executor=exit_executor,
        )
        resp = client.post("/api/execution/flip-exit/arb-1")

        assert resp.status_code == 200
        assert resp.json()["order_id"] == "order-1"

    def test_rejects_when_exit_already_pending(self) -> None:
        """Manual close should not place a duplicate sell when exit is already pending."""
        position_repo = AsyncMock()
        position_repo.get_position_by_arb_id = AsyncMock(
            return_value={
                "arb_id": "arb-2",
                "market_id": "m2",
                "side": "yes",
                "entry_price": Decimal("0.42"),
                "status": "exit_pending",
            }
        )
        exit_executor = AsyncMock()

        client = _make_client(
            flip_position_repo=position_repo,
            flip_exit_executor=exit_executor,
        )
        resp = client.post("/api/execution/flip-exit/arb-2")

        assert resp.status_code == 409
        assert "already pending" in resp.text
        exit_executor.execute_exit.assert_not_awaited()


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


class TestGetBalances:
    """Tests for GET /api/execution/balances."""

    def test_no_capital_manager_returns_503(self) -> None:
        """Returns 503 when capital manager not available."""
        client = _make_client()
        resp = client.get("/api/execution/balances")
        assert resp.status_code == 503

    def test_successful_balances(self) -> None:
        """Returns balances and constraints on success."""
        cm = MagicMock()
        cm.refresh_balances = AsyncMock(
            return_value=(Decimal("0"), Decimal("150.00")),
        )
        cm.poly_balance = Decimal("0")
        cm.kalshi_balance = Decimal("150.00")
        cm.total_balance = Decimal("150.00")
        cm.suggest_size.return_value = Decimal("7.50")
        cm.check_exposure.return_value = (Decimal("0"), Decimal("75"), False)
        cm.daily_pnl = Decimal("0")
        cm.check_venue_reserve.return_value = (True, "OK")
        cm.check_daily_pnl.return_value = (Decimal("0"), Decimal("50"), False)
        cm.check_cooldown.return_value = (False, 0)
        cm.check_open_positions.return_value = (0, 5, False)

        client = _make_client(capital_manager=cm)
        resp = client.get("/api/execution/balances")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kalshi_balance"] == "150.00"
        assert data["open_positions"] == 0
        assert len(data["constraints"]) == 5
        assert all(c["ok"] for c in data["constraints"])

    def test_blocked_constraint(self) -> None:
        """Returns blocked constraint when exposure cap hit."""
        cm = MagicMock()
        cm.refresh_balances = AsyncMock(
            return_value=(Decimal("100"), Decimal("100")),
        )
        cm.poly_balance = Decimal("100")
        cm.kalshi_balance = Decimal("100")
        cm.total_balance = Decimal("200")
        cm.suggest_size.return_value = Decimal("10")
        cm.check_exposure.return_value = (Decimal("200"), Decimal("0"), True)
        cm.daily_pnl = Decimal("-30")
        cm.check_venue_reserve.return_value = (True, "OK")
        cm.check_daily_pnl.return_value = (Decimal("-30"), Decimal("50"), False)
        cm.check_cooldown.return_value = (False, 0)
        cm.check_open_positions.return_value = (1, 5, False)

        client = _make_client(capital_manager=cm)
        resp = client.get("/api/execution/balances")
        assert resp.status_code == 200
        data = resp.json()
        exposure_constraint = next(c for c in data["constraints"] if c["name"] == "Exposure Cap")
        assert exposure_constraint["ok"] is False


class TestAutoExecStatus:
    """Tests for GET /api/auto-execution/status per-pipeline breakers."""

    def test_per_pipeline_breakers(self) -> None:
        """Returns separate arb_breakers and flip_breakers arrays."""
        client = _make_client()
        arb_b = MagicMock()
        arb_b.get_state.return_value = [
            MagicMock(
                model_dump=MagicMock(return_value={"breaker_type": "loss", "tripped": False})
            ),
        ]
        flip_b = MagicMock()
        flip_b.get_state.return_value = [
            MagicMock(
                model_dump=MagicMock(return_value={"breaker_type": "failure", "tripped": True})
            ),
        ]
        client.app.state.arb_breakers = arb_b
        client.app.state.flip_breakers = flip_b
        resp = client.get("/api/auto-execution/status")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["arb_breakers"]) == 1
        assert data["arb_breakers"][0]["breaker_type"] == "loss"
        assert len(data["flip_breakers"]) == 1
        assert data["flip_breakers"][0]["tripped"] is True


class TestAutoExecPositions:
    """Tests for GET /api/auto-execution/positions pipeline_type field."""

    def test_positions_have_pipeline_type(self) -> None:
        """Arb and flip positions include pipeline_type field."""
        client = _make_client()
        auto_repo = AsyncMock()
        auto_repo.get_open_positions = AsyncMock(
            return_value=[{"arb_id": "a1", "entry_price": "0.50"}],
        )
        flip_repo = MagicMock()
        flip_repo.get_orphaned_positions = AsyncMock(
            return_value=[
                MagicMock(**{"items.return_value": [("market_id", "m1"), ("side", "yes")]})
            ],
        )

        from arb_scanner.api.deps import get_auto_exec_repo

        client.app.dependency_overrides[get_auto_exec_repo] = lambda: auto_repo
        client.app.state.flip_position_repo = flip_repo

        resp = client.get("/api/auto-execution/positions")
        assert resp.status_code == 200
        data = resp.json()
        arb_pos = [p for p in data if p.get("pipeline_type") == "arb"]
        flip_pos = [p for p in data if p.get("pipeline_type") == "flip"]
        assert len(arb_pos) >= 1
        assert len(flip_pos) >= 1
