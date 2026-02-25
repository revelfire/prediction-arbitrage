"""T023 - Integration tests for FastAPI dashboard API routes.

Tests full request -> route -> (mocked) repo -> serialized response cycles.
Uses the same TestClient approach as unit tests but with richer mock data
to verify response shapes and end-to-end behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from arb_scanner.api.app import create_app
from arb_scanner.api.deps import get_analytics_repo, get_config, get_repo
from arb_scanner.models.analytics import (
    AlertType,
    ScanHealthSummary,
    SpreadSnapshot,
    TrendAlert,
)
from arb_scanner.models.config import (
    FeeSchedule,
    FeesConfig,
    Settings,
    StorageConfig,
)

_NOW = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _test_config() -> Settings:
    """Build a minimal Settings object for testing."""
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


def _realistic_opportunities() -> list[dict[str, Any]]:
    """Return a list of realistic opportunity dicts as the DB would return."""
    return [
        {
            "id": "opp-001",
            "poly_event_id": "poly-evt-001",
            "kalshi_event_id": "kalshi-evt-001",
            "buy_venue": "polymarket",
            "sell_venue": "kalshi",
            "net_spread_pct": "0.05",
            "max_size": "1000.00",
            "detected_at": _NOW.isoformat(),
        },
        {
            "id": "opp-002",
            "poly_event_id": "poly-evt-002",
            "kalshi_event_id": "kalshi-evt-002",
            "buy_venue": "kalshi",
            "sell_venue": "polymarket",
            "net_spread_pct": "0.08",
            "max_size": "500.00",
            "detected_at": _NOW.isoformat(),
        },
    ]


def _realistic_spread_snapshots() -> list[SpreadSnapshot]:
    """Return a list of SpreadSnapshot models for pair history testing."""
    return [
        SpreadSnapshot(
            detected_at=_NOW,
            net_spread_pct=Decimal("0.05"),
            annualized_return=Decimal("1.20"),
            depth_risk=False,
            max_size=Decimal("500"),
        ),
        SpreadSnapshot(
            detected_at=_NOW,
            net_spread_pct=Decimal("0.08"),
            annualized_return=Decimal("2.10"),
            depth_risk=True,
            max_size=Decimal("250"),
        ),
    ]


def _realistic_trend_alerts() -> list[TrendAlert]:
    """Return a list of TrendAlert models for alert testing."""
    return [
        TrendAlert(
            alert_type=AlertType.convergence,
            poly_event_id="poly-evt-001",
            kalshi_event_id="kalshi-evt-001",
            spread_before=Decimal("0.10"),
            spread_after=Decimal("0.03"),
            message="Spread converged from 10% to 3%",
            dispatched_at=_NOW,
        ),
        TrendAlert(
            alert_type=AlertType.divergence,
            poly_event_id="poly-evt-002",
            kalshi_event_id="kalshi-evt-002",
            spread_before=Decimal("0.03"),
            spread_after=Decimal("0.12"),
            message="Spread diverged from 3% to 12%",
            dispatched_at=_NOW,
        ),
    ]


def _realistic_health_summaries() -> list[ScanHealthSummary]:
    """Return a list of ScanHealthSummary models for health endpoint testing."""
    return [
        ScanHealthSummary(
            hour=_NOW,
            scan_count=12,
            avg_duration_s=3.5,
            total_llm_calls=60,
            total_opps=8,
            total_errors=1,
        ),
        ScanHealthSummary(
            hour=_NOW,
            scan_count=10,
            avg_duration_s=4.2,
            total_llm_calls=50,
            total_opps=5,
            total_errors=0,
        ),
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_repo() -> AsyncMock:
    """Create a mock Repository with pre-configured async methods."""
    repo = AsyncMock()
    repo.get_recent_opportunities = AsyncMock(return_value=[])
    repo.get_all_matches = AsyncMock(return_value=[])
    repo.get_pending_tickets = AsyncMock(return_value=[])
    repo.update_ticket_status = AsyncMock()
    return repo


@pytest.fixture()
def mock_analytics_repo() -> AsyncMock:
    """Create a mock AnalyticsRepository with pre-configured async methods."""
    repo = AsyncMock()
    repo.get_pair_summaries = AsyncMock(return_value=[])
    repo.get_spread_history = AsyncMock(return_value=[])
    repo.get_scan_health = AsyncMock(return_value=[])
    repo.get_recent_scan_logs = AsyncMock(return_value=[])
    repo.get_recent_alerts = AsyncMock(return_value=[])
    repo.get_opportunities_date_range = AsyncMock(return_value=[])
    return repo


@pytest.fixture()
def client(mock_repo: AsyncMock, mock_analytics_repo: AsyncMock) -> TestClient:
    """Build a TestClient with mocked DB and overridden dependencies."""
    config = _test_config()

    with (
        patch("arb_scanner.storage.db.Database.connect", new_callable=AsyncMock),
        patch("arb_scanner.storage.db.Database.disconnect", new_callable=AsyncMock),
    ):
        app = create_app(config)
        app.dependency_overrides[get_repo] = lambda: mock_repo
        app.dependency_overrides[get_analytics_repo] = lambda: mock_analytics_repo
        app.dependency_overrides[get_config] = lambda: config

        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestAPIIntegration:
    """Integration tests verifying full request/response cycles."""

    def test_opportunities_response_shape(self, client: TestClient, mock_repo: AsyncMock) -> None:
        """GET /api/opportunities returns items with all expected keys."""
        mock_repo.get_recent_opportunities.return_value = _realistic_opportunities()

        resp = client.get("/api/opportunities")
        assert resp.status_code == 200

        data = resp.json()
        assert len(data) == 2

        expected_keys = {
            "id",
            "poly_event_id",
            "kalshi_event_id",
            "buy_venue",
            "sell_venue",
            "net_spread_pct",
            "max_size",
            "detected_at",
        }
        for item in data:
            assert expected_keys.issubset(set(item.keys())), (
                f"Missing keys: {expected_keys - set(item.keys())}"
            )
            assert isinstance(item["id"], str)
            assert item["buy_venue"] in ("polymarket", "kalshi")
            assert item["sell_venue"] in ("polymarket", "kalshi")

    def test_pair_history_response_shape(
        self, client: TestClient, mock_analytics_repo: AsyncMock
    ) -> None:
        """GET /api/pairs/POLY/KALSHI/history returns spread snapshots."""
        mock_analytics_repo.get_spread_history.return_value = _realistic_spread_snapshots()

        resp = client.get("/api/pairs/POLY/KALSHI/history")
        assert resp.status_code == 200

        data = resp.json()
        assert len(data) == 2

        expected_keys = {"detected_at", "net_spread_pct", "depth_risk", "max_size"}
        for item in data:
            assert expected_keys.issubset(set(item.keys())), (
                f"Missing keys: {expected_keys - set(item.keys())}"
            )
            assert isinstance(item["depth_risk"], bool)

    def test_alert_type_filter_end_to_end(
        self, client: TestClient, mock_analytics_repo: AsyncMock
    ) -> None:
        """GET /api/alerts?type=convergence filters alerts by type."""
        mock_analytics_repo.get_recent_alerts.return_value = _realistic_trend_alerts()

        resp = client.get("/api/alerts?type=convergence")
        assert resp.status_code == 200

        mock_analytics_repo.get_recent_alerts.assert_awaited_once_with(
            limit=20, alert_type="convergence"
        )

    def test_ticket_approve_updates_status(self, client: TestClient, mock_repo: AsyncMock) -> None:
        """POST /api/tickets/test-id/approve updates ticket status."""
        resp = client.post("/api/tickets/test-id/approve")
        assert resp.status_code == 200
        assert resp.json() == {"status": "approved"}

        mock_repo.update_ticket_status.assert_awaited_once_with("test-id", "approved")

    def test_scan_trigger_returns_result(self, client: TestClient) -> None:
        """POST /api/scan returns scan result without _raw_opps."""
        scan_result: dict[str, Any] = {
            "scan_id": "scan-integration-001",
            "timestamp": _NOW.isoformat(),
            "markets_scanned": 150,
            "opportunities": [
                {
                    "id": "opp-scan-001",
                    "net_spread_pct": "0.04",
                    "buy_venue": "polymarket",
                }
            ],
            "_raw_opps": [{"internal": "data"}],
        }

        with patch(
            "arb_scanner.cli.orchestrator.run_scan",
            new_callable=AsyncMock,
            return_value=scan_result,
        ):
            resp = client.post("/api/scan")

        assert resp.status_code == 200
        data = resp.json()
        assert data["scan_id"] == "scan-integration-001"
        assert data["timestamp"] == _NOW.isoformat()
        assert data["markets_scanned"] == 150
        assert "opportunities" in data
        assert "_raw_opps" not in data

    def test_static_file_serving(self, client: TestClient) -> None:
        """Static file endpoints serve HTML, CSS, and JS correctly."""
        # Root serves index.html
        resp_root = client.get("/")
        assert resp_root.status_code == 200
        assert "text/html" in resp_root.headers["content-type"]

        # Static CSS
        resp_css = client.get("/static/style.css")
        assert resp_css.status_code == 200

        # Static JS
        resp_js = client.get("/static/app.js")
        assert resp_js.status_code == 200

    def test_db_error_returns_503(self, client: TestClient, mock_repo: AsyncMock) -> None:
        """GET /api/opportunities returns 503 with JSON error on DB failure."""
        mock_repo.get_recent_opportunities.side_effect = Exception("Connection lost")

        resp = client.get("/api/opportunities")
        assert resp.status_code == 503

        data = resp.json()
        assert "detail" in data

    def test_health_with_real_model_data(
        self, client: TestClient, mock_analytics_repo: AsyncMock
    ) -> None:
        """GET /api/health returns health summaries with expected fields."""
        mock_analytics_repo.get_scan_health.return_value = _realistic_health_summaries()

        resp = client.get("/api/health")
        assert resp.status_code == 200

        data = resp.json()
        assert len(data) == 2

        expected_keys = {"hour", "scan_count", "avg_duration_s", "total_llm_calls"}
        for item in data:
            assert expected_keys.issubset(set(item.keys())), (
                f"Missing keys: {expected_keys - set(item.keys())}"
            )
            assert isinstance(item["scan_count"], int)
            assert isinstance(item["avg_duration_s"], float)
            assert isinstance(item["total_llm_calls"], int)
