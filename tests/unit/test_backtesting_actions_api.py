"""Tests for backtesting action API endpoints (analyze, report, sweep)."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from arb_scanner.api.app import create_app
from arb_scanner.api.deps import get_backtest_repo, get_config, get_flip_repo
from arb_scanner.api.routes_backtesting_actions import ImportRunJob, _run_import_job
from arb_scanner.models.config import (
    FeeSchedule,
    FeesConfig,
    Settings,
    StorageConfig,
)

_NOW = datetime.now(tz=timezone.utc)


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
    )


def _sample_trade_row() -> dict[str, Any]:
    """Return a sample trade row dict as the DB returns."""
    return {
        "id": 1,
        "market_name": "BTC above $80k?",
        "action": "Buy",
        "usdc_amount": Decimal("10.0"),
        "token_amount": Decimal("50"),
        "token_name": "Yes",
        "timestamp": _NOW,
        "tx_hash": "0xabc123",
        "condition_id": None,
        "imported_at": _NOW,
    }


@pytest.fixture()
def mock_backtest_repo() -> AsyncMock:
    """Mock BacktestingRepository."""
    repo = AsyncMock()
    repo.get_trades = AsyncMock(return_value=[_sample_trade_row()])
    repo.upsert_category_performance = AsyncMock()
    repo.upsert_position = AsyncMock()
    repo.upsert_optimal_param = AsyncMock()
    return repo


@pytest.fixture()
def mock_flip_repo() -> AsyncMock:
    """Mock FlippeningRepository."""
    repo = AsyncMock()
    repo.get_history = AsyncMock(return_value=[])
    return repo


@pytest.fixture()
def client(
    mock_backtest_repo: AsyncMock,
    mock_flip_repo: AsyncMock,
) -> TestClient:
    """Build a TestClient with mocked dependencies."""
    config = _test_config()
    with (
        patch("arb_scanner.storage.db.Database.connect", new_callable=AsyncMock),
        patch("arb_scanner.storage.db.Database.disconnect", new_callable=AsyncMock),
    ):
        app = create_app(config)
        app.dependency_overrides[get_backtest_repo] = lambda: mock_backtest_repo
        app.dependency_overrides[get_flip_repo] = lambda: mock_flip_repo
        app.dependency_overrides[get_config] = lambda: config
        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc


class TestAnalyzeEndpoint:
    """Tests for POST /api/backtesting/analyze."""

    def test_analyze_returns_portfolio(
        self,
        client: TestClient,
        mock_backtest_repo: AsyncMock,
    ) -> None:
        """Analyze reconstructs positions and returns portfolio."""
        resp = client.post("/api/backtesting/analyze")
        assert resp.status_code == 200
        data = resp.json()
        assert "portfolio" in data
        assert "category_performance" in data
        assert "trade_count" in data

    def test_analyze_persists_category_performance(
        self,
        client: TestClient,
        mock_backtest_repo: AsyncMock,
    ) -> None:
        """Analyze upserts category performance to DB."""
        client.post("/api/backtesting/analyze")
        assert mock_backtest_repo.upsert_category_performance.called

    def test_analyze_with_category_filter(
        self,
        client: TestClient,
        mock_backtest_repo: AsyncMock,
    ) -> None:
        """Analyze accepts category query param."""
        resp = client.post("/api/backtesting/analyze?category=nba")
        assert resp.status_code == 200

    def test_analyze_empty_trades(
        self,
        client: TestClient,
        mock_backtest_repo: AsyncMock,
    ) -> None:
        """Analyze handles no trades gracefully."""
        mock_backtest_repo.get_trades.return_value = []
        resp = client.post("/api/backtesting/analyze")
        assert resp.status_code == 200
        assert resp.json()["trade_count"] == 0


class TestImportAndRunEndpoint:
    """Tests for POST /api/backtesting/import-and-run."""

    @patch("arb_scanner.api.routes_backtesting_actions.run_import_workflow", new_callable=AsyncMock)
    def test_import_and_run_returns_workflow_payload(
        self,
        mock_run_workflow: AsyncMock,
        client: TestClient,
    ) -> None:
        """Import-and-run delegates to the automation workflow helper."""
        mock_run_workflow.return_value = {
            "import_result": {"inserted": 1, "duplicates": 0, "errors": 0},
            "portfolio": {"trade_count": 1},
            "signal_alignment": {},
            "category_performance": [],
            "trade_count": 1,
            "suggestions": [],
        }
        resp = client.post(
            "/api/backtesting/import-and-run",
            files={
                "file": (
                    "trades.csv",
                    b"marketName,action,usdcAmount,tokenAmount,tokenName,timestamp,hash\n",
                    "text/csv",
                )
            },
        )
        assert resp.status_code == 200
        assert resp.json()["import_result"]["inserted"] == 1


class TestImportAndRunJobEndpoint:
    """Tests for async import-and-run job endpoints."""

    @patch(
        "arb_scanner.api.routes_backtesting_actions._enqueue_import_job",
        new_callable=AsyncMock,
    )
    def test_start_import_and_run_job_returns_accepted(
        self,
        mock_enqueue_job: AsyncMock,
        client: TestClient,
    ) -> None:
        """Async upload route should return a queued job payload."""
        mock_enqueue_job.return_value = ImportRunJob(
            job_id="job-123",
            status="queued",
            stage="queued",
            progress=0.0,
            message="Queued import/backtest job",
            file_name="trades.csv",
            created_at=_NOW,
        )
        resp = client.post(
            "/api/backtesting/import-and-run/jobs",
            files={
                "file": (
                    "trades.csv",
                    b"marketName,action,usdcAmount,tokenAmount,tokenName,timestamp,hash\n",
                    "text/csv",
                )
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["job_id"] == "job-123"
        assert data["status"] == "queued"
        assert data["status_url"].endswith("/api/backtesting/import-and-run/jobs/job-123")

    def test_get_import_and_run_job_returns_snapshot(
        self,
        client: TestClient,
    ) -> None:
        """Status endpoint returns stored job metadata."""
        client.app.state.backtest_import_jobs["job-123"] = ImportRunJob(
            job_id="job-123",
            status="completed",
            stage="completed",
            progress=1.0,
            message="Done",
            file_name="trades.csv",
            created_at=_NOW,
            completed_at=_NOW,
            result={"suggestions": []},
        )

        resp = client.get("/api/backtesting/import-and-run/jobs/job-123")

        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "job-123"
        assert data["status"] == "completed"
        assert data["result"]["suggestions"] == []

    @pytest.mark.asyncio
    @patch("arb_scanner.api.routes_backtesting_actions.run_import_workflow", new_callable=AsyncMock)
    async def test_run_import_job_persists_completed_result(
        self,
        mock_run_workflow: AsyncMock,
        client: TestClient,
        mock_backtest_repo: AsyncMock,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Background worker should store the completed workflow result on app state."""

        async def _workflow(*args: Any, **kwargs: Any) -> dict[str, Any]:
            progress = kwargs["progress"]
            await progress("building_report", 0.45, "Rebuilding report")
            return {
                "import_result": {"inserted": 2, "duplicates": 0, "errors": 0},
                "portfolio": {"trade_count": 2},
                "signal_alignment": {},
                "category_performance": [],
                "trade_count": 2,
                "suggestions": [{"config_path": "flippening.categories.nba.max_hold_minutes"}],
            }

        mock_run_workflow.side_effect = _workflow
        client.app.state.backtest_import_jobs["job-123"] = ImportRunJob(
            job_id="job-123",
            status="queued",
            stage="queued",
            progress=0.0,
            message="Queued import/backtest job",
            file_name="trades.csv",
            created_at=_NOW,
        )

        await _run_import_job(
            client.app,
            job_id="job-123",
            content=b"csv",
            config=_test_config(),
            repo=mock_backtest_repo,
            flip_repo=mock_flip_repo,
        )

        job = client.app.state.backtest_import_jobs["job-123"]
        assert job.status == "completed"
        assert job.stage == "completed"
        assert job.progress == 1.0
        assert job.result == {
            "import_result": {"inserted": 2, "duplicates": 0, "errors": 0},
            "portfolio": {"trade_count": 2},
            "signal_alignment": {},
            "category_performance": [],
            "trade_count": 2,
            "suggestions": [{"config_path": "flippening.categories.nba.max_hold_minutes"}],
        }


class TestReportEndpoint:
    """Tests for POST /api/backtesting/report."""

    def test_report_returns_signal_alignment(
        self,
        client: TestClient,
        mock_backtest_repo: AsyncMock,
    ) -> None:
        """Report includes signal alignment data."""
        resp = client.post("/api/backtesting/report")
        assert resp.status_code == 200
        data = resp.json()
        assert "portfolio" in data
        assert "signal_alignment" in data
        assert "category_performance" in data

    def test_report_with_filters(
        self,
        client: TestClient,
    ) -> None:
        """Report accepts category, since, until filters."""
        resp = client.post(
            "/api/backtesting/report?category=nba"
            "&since=2026-01-01T00:00:00&until=2026-12-31T00:00:00",
        )
        assert resp.status_code == 200

    def test_report_empty_trades(
        self,
        client: TestClient,
        mock_backtest_repo: AsyncMock,
    ) -> None:
        """Report handles empty trade set."""
        mock_backtest_repo.get_trades.return_value = []
        resp = client.post("/api/backtesting/report")
        assert resp.status_code == 200


class TestSweepEndpoint:
    """Tests for POST /api/backtesting/sweep."""

    @patch("arb_scanner.cli._replay_helpers.run_sweep", new_callable=AsyncMock)
    def test_sweep_calls_run_sweep(
        self,
        mock_run_sweep: AsyncMock,
        client: TestClient,
        mock_backtest_repo: AsyncMock,
    ) -> None:
        """Sweep endpoint delegates to run_sweep helper."""
        mock_run_sweep.return_value = {
            "param_name": "spike_threshold_pct",
            "results": [[0.10, {"win_rate": 0.6, "avg_pnl": 1.5}]],
        }
        resp = client.post(
            "/api/backtesting/sweep",
            json={
                "category": "nba",
                "param": "spike_threshold_pct",
                "min": 0.05,
                "max": 0.20,
                "step": 0.05,
            },
        )
        assert resp.status_code == 200

    def test_sweep_missing_body_returns_422(self, client: TestClient) -> None:
        """Sweep without request body returns validation error."""
        resp = client.post("/api/backtesting/sweep")
        assert resp.status_code == 422


class TestApplyConfigSuggestions:
    """Tests for POST /api/backtesting/config/apply."""

    def test_apply_persists_yaml(
        self,
        mock_backtest_repo: AsyncMock,
        mock_flip_repo: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """Applies a suggestion to config.yaml without restarting in tests."""
        config = _test_config()
        config_path = tmp_path / "config.yaml"
        config_path.write_text("flippening:\n  categories: {}\n", encoding="utf-8")
        object.__setattr__(config, "_config_path", str(config_path))

        with (
            patch("arb_scanner.storage.db.Database.connect", new_callable=AsyncMock),
            patch("arb_scanner.storage.db.Database.disconnect", new_callable=AsyncMock),
        ):
            app = create_app(config)
            app.state.config_path = str(config_path)
            app.dependency_overrides[get_backtest_repo] = lambda: mock_backtest_repo
            app.dependency_overrides[get_flip_repo] = lambda: mock_flip_repo
            app.dependency_overrides[get_config] = lambda: config
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/api/backtesting/config/apply",
                    json={
                        "suggestions": [
                            {
                                "config_path": "flippening.categories.nba.min_confidence",
                                "suggested_value": 0.72,
                            }
                        ],
                        "restart": False,
                    },
                )

        assert resp.status_code == 200
        updated = config_path.read_text(encoding="utf-8")
        assert "nba:" in updated
        assert "min_confidence: 0.72" in updated

    def test_apply_rejects_unsupported_paths(
        self,
        client: TestClient,
    ) -> None:
        """Rejects writes outside approved backtesting config surfaces."""
        resp = client.post(
            "/api/backtesting/config/apply",
            json={
                "suggestions": [
                    {
                        "config_path": "dashboard.port",
                        "suggested_value": 9000,
                    }
                ],
                "restart": False,
            },
        )
        assert resp.status_code == 400
