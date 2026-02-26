"""T015 - Unit tests for FastAPI dashboard API routes.

Tests all API routes using FastAPI TestClient with mocked database
repositories. The lifespan is handled by patching Database.connect/disconnect
so no real PostgreSQL connection is required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from arb_scanner.api.app import create_app
from arb_scanner.api.deps import get_analytics_repo, get_config, get_flip_repo, get_repo
from arb_scanner.models.analytics import (
    AlertType,
    PairSummary,
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


def _sample_opportunity() -> dict[str, Any]:
    """Return a sample opportunity dict as the DB would return."""
    return {
        "id": "opp-001",
        "poly_event_id": "poly-evt-001",
        "kalshi_event_id": "kalshi-evt-001",
        "buy_venue": "polymarket",
        "sell_venue": "kalshi",
        "net_spread_pct": "0.05",
        "net_profit": "0.03",
        "detected_at": _NOW.isoformat(),
    }


def _sample_alert() -> TrendAlert:
    """Return a sample TrendAlert model for mock data."""
    return TrendAlert(
        alert_type=AlertType.convergence,
        poly_event_id="poly-evt-001",
        kalshi_event_id="kalshi-evt-001",
        spread_before=Decimal("0.10"),
        spread_after=Decimal("0.03"),
        message="Spread converged from 10% to 3%",
        dispatched_at=_NOW,
    )


def _sample_pair_summary() -> PairSummary:
    """Return a sample PairSummary model."""
    return PairSummary(
        poly_event_id="poly-evt-001",
        kalshi_event_id="kalshi-evt-001",
        peak_spread=Decimal("0.12"),
        min_spread=Decimal("0.02"),
        avg_spread=Decimal("0.06"),
        total_detections=15,
        first_seen=_NOW,
        last_seen=_NOW,
    )


def _sample_spread_snapshot() -> SpreadSnapshot:
    """Return a sample SpreadSnapshot model."""
    return SpreadSnapshot(
        detected_at=_NOW,
        net_spread_pct=Decimal("0.05"),
        annualized_return=Decimal("1.20"),
        depth_risk=False,
        max_size=Decimal("500"),
    )


def _sample_health_summary() -> ScanHealthSummary:
    """Return a sample ScanHealthSummary model."""
    return ScanHealthSummary(
        hour=_NOW,
        scan_count=12,
        avg_duration_s=3.5,
        total_llm_calls=60,
        total_opps=8,
        total_errors=1,
    )


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
def mock_flip_repo() -> AsyncMock:
    """Create a mock FlippeningRepository with pre-configured async methods."""
    repo = AsyncMock()
    repo.get_active_signals = AsyncMock(return_value=[])
    repo.get_history = AsyncMock(return_value=[])
    repo.get_stats = AsyncMock(return_value=[])
    repo.get_recent_events = AsyncMock(return_value=[])
    repo.get_discovery_health = AsyncMock(return_value=[])
    return repo


@pytest.fixture()
def client(
    mock_repo: AsyncMock,
    mock_analytics_repo: AsyncMock,
    mock_flip_repo: AsyncMock,
) -> TestClient:
    """Build a TestClient with mocked DB and overridden dependencies."""
    config = _test_config()

    with (
        patch("arb_scanner.storage.db.Database.connect", new_callable=AsyncMock),
        patch("arb_scanner.storage.db.Database.disconnect", new_callable=AsyncMock),
    ):
        app = create_app(config)
        app.dependency_overrides[get_repo] = lambda: mock_repo
        app.dependency_overrides[get_analytics_repo] = lambda: mock_analytics_repo
        app.dependency_overrides[get_flip_repo] = lambda: mock_flip_repo
        app.dependency_overrides[get_config] = lambda: config

        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc


# ---------------------------------------------------------------------------
# Root endpoint
# ---------------------------------------------------------------------------


class TestRootEndpoint:
    """Tests for the root HTML dashboard endpoint."""

    def test_root_returns_html(self, client: TestClient) -> None:
        """GET / returns 200 with HTML content-type."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Opportunities routes
# ---------------------------------------------------------------------------


class TestOpportunitiesRoutes:
    """Tests for /api/opportunities and /api/pairs/* endpoints."""

    def test_opportunities_empty(self, client: TestClient, mock_repo: AsyncMock) -> None:
        """GET /api/opportunities returns 200 with empty list."""
        resp = client.get("/api/opportunities")
        assert resp.status_code == 200
        assert resp.json() == []
        mock_repo.get_recent_opportunities.assert_awaited_once_with(50)

    def test_opportunities_with_data(self, client: TestClient, mock_repo: AsyncMock) -> None:
        """GET /api/opportunities returns opportunity dicts from repo."""
        sample = _sample_opportunity()
        mock_repo.get_recent_opportunities.return_value = [sample]

        resp = client.get("/api/opportunities")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "opp-001"

    def test_opportunities_limit_param(self, client: TestClient, mock_repo: AsyncMock) -> None:
        """GET /api/opportunities?limit=5 passes limit to repo."""
        resp = client.get("/api/opportunities?limit=5")
        assert resp.status_code == 200
        mock_repo.get_recent_opportunities.assert_awaited_once_with(5)

    def test_opportunities_since_param(
        self, client: TestClient, mock_analytics_repo: AsyncMock
    ) -> None:
        """GET /api/opportunities?since=<iso> uses analytics repo date range."""
        resp = client.get("/api/opportunities?since=2026-01-01T00:00:00")
        assert resp.status_code == 200
        mock_analytics_repo.get_opportunities_date_range.assert_awaited_once()
        call_args = mock_analytics_repo.get_opportunities_date_range.call_args
        since_arg = call_args[0][0]
        assert since_arg.year == 2026
        assert since_arg.month == 1

    def test_opportunities_db_error(self, client: TestClient, mock_repo: AsyncMock) -> None:
        """GET /api/opportunities returns 503 when repo raises."""
        mock_repo.get_recent_opportunities.side_effect = RuntimeError("DB down")

        resp = client.get("/api/opportunities")
        assert resp.status_code == 503

    def test_pair_summaries(self, client: TestClient, mock_analytics_repo: AsyncMock) -> None:
        """GET /api/pairs/summaries returns 200 with pair summaries."""
        mock_analytics_repo.get_pair_summaries.return_value = [_sample_pair_summary()]

        resp = client.get("/api/pairs/summaries")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["poly_event_id"] == "poly-evt-001"

    def test_pair_summaries_hours_param(
        self, client: TestClient, mock_analytics_repo: AsyncMock
    ) -> None:
        """GET /api/pairs/summaries?hours=48 passes lookback to repo."""
        mock_analytics_repo.get_pair_summaries.return_value = []

        resp = client.get("/api/pairs/summaries?hours=48")
        assert resp.status_code == 200
        mock_analytics_repo.get_pair_summaries.assert_awaited_once()

    def test_pair_history(self, client: TestClient, mock_analytics_repo: AsyncMock) -> None:
        """GET /api/pairs/<poly>/<kalshi>/history returns 200."""
        mock_analytics_repo.get_spread_history.return_value = [_sample_spread_snapshot()]

        resp = client.get("/api/pairs/POLY1/KALSHI1/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        mock_analytics_repo.get_spread_history.assert_awaited_once()
        call_args = mock_analytics_repo.get_spread_history.call_args
        assert call_args[0][0] == "POLY1"
        assert call_args[0][1] == "KALSHI1"


# ---------------------------------------------------------------------------
# Health routes
# ---------------------------------------------------------------------------


class TestHealthRoutes:
    """Tests for /api/health and /api/health/scans endpoints."""

    def test_health_metrics(self, client: TestClient, mock_analytics_repo: AsyncMock) -> None:
        """GET /api/health returns 200 with health summaries."""
        mock_analytics_repo.get_scan_health.return_value = [_sample_health_summary()]

        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["scan_count"] == 12

    def test_health_scans(self, client: TestClient, mock_analytics_repo: AsyncMock) -> None:
        """GET /api/health/scans returns 200 with scan log entries."""
        mock_analytics_repo.get_recent_scan_logs.return_value = [
            {"id": "scan-001", "duration_s": 2.5}
        ]

        resp = client.get("/api/health/scans")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        mock_analytics_repo.get_recent_scan_logs.assert_awaited_once_with(20)

    def test_health_db_error(self, client: TestClient, mock_analytics_repo: AsyncMock) -> None:
        """GET /api/health returns 500 when repo raises."""
        mock_analytics_repo.get_scan_health.side_effect = RuntimeError("DB down")

        resp = client.get("/api/health")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Alert routes
# ---------------------------------------------------------------------------


class TestAlertRoutes:
    """Tests for /api/alerts endpoint."""

    def test_alerts_empty(self, client: TestClient, mock_analytics_repo: AsyncMock) -> None:
        """GET /api/alerts returns 200 with empty list."""
        resp = client.get("/api/alerts")
        assert resp.status_code == 200
        assert resp.json() == []
        mock_analytics_repo.get_recent_alerts.assert_awaited_once_with(limit=20, alert_type=None)

    def test_alerts_with_data(self, client: TestClient, mock_analytics_repo: AsyncMock) -> None:
        """GET /api/alerts returns alert dicts from repo."""
        mock_analytics_repo.get_recent_alerts.return_value = [_sample_alert()]

        resp = client.get("/api/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["alert_type"] == "convergence"

    def test_alerts_type_filter(self, client: TestClient, mock_analytics_repo: AsyncMock) -> None:
        """GET /api/alerts?type=convergence passes type to repo."""
        resp = client.get("/api/alerts?type=convergence")
        assert resp.status_code == 200
        mock_analytics_repo.get_recent_alerts.assert_awaited_once_with(
            limit=20, alert_type="convergence"
        )


# ---------------------------------------------------------------------------
# Match routes
# ---------------------------------------------------------------------------


class TestMatchRoutes:
    """Tests for /api/matches endpoint."""

    def test_matches_empty(self, client: TestClient, mock_repo: AsyncMock) -> None:
        """GET /api/matches returns 200 with empty list."""
        resp = client.get("/api/matches")
        assert resp.status_code == 200
        assert resp.json() == []
        mock_repo.get_all_matches.assert_awaited_once_with(
            include_expired=False, min_confidence=0.0
        )

    def test_matches_filters(self, client: TestClient, mock_repo: AsyncMock) -> None:
        """GET /api/matches with filters passes them to repo."""
        resp = client.get("/api/matches?include_expired=true&min_confidence=0.5")
        assert resp.status_code == 200
        mock_repo.get_all_matches.assert_awaited_once_with(include_expired=True, min_confidence=0.5)


# ---------------------------------------------------------------------------
# Ticket routes
# ---------------------------------------------------------------------------


class TestTicketRoutes:
    """Tests for /api/tickets and action endpoints."""

    def test_tickets_pending(self, client: TestClient, mock_repo: AsyncMock) -> None:
        """GET /api/tickets returns 200 with pending tickets."""
        mock_repo.get_pending_tickets.return_value = [{"arb_id": "abc123", "status": "pending"}]

        resp = client.get("/api/tickets")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["arb_id"] == "abc123"

    def test_ticket_approve(self, client: TestClient, mock_repo: AsyncMock) -> None:
        """POST /api/tickets/abc123/approve returns approved status."""
        resp = client.post("/api/tickets/abc123/approve")
        assert resp.status_code == 200
        assert resp.json() == {"status": "approved"}
        mock_repo.update_ticket_status.assert_awaited_once_with("abc123", "approved")

    def test_ticket_expire(self, client: TestClient, mock_repo: AsyncMock) -> None:
        """POST /api/tickets/abc123/expire returns expired status."""
        resp = client.post("/api/tickets/abc123/expire")
        assert resp.status_code == 200
        assert resp.json() == {"status": "expired"}
        mock_repo.update_ticket_status.assert_awaited_once_with("abc123", "expired")


# ---------------------------------------------------------------------------
# Scan routes
# ---------------------------------------------------------------------------


class TestScanRoutes:
    """Tests for /api/scan trigger endpoint."""

    def test_scan_trigger(self, client: TestClient) -> None:
        """POST /api/scan triggers a scan and returns the result."""
        mock_result: dict[str, Any] = {
            "scan_id": "test-scan",
            "opportunities": [],
            "duration_s": 1.5,
        }
        with patch(
            "arb_scanner.cli.orchestrator.run_scan",
            new_callable=AsyncMock,
            return_value={**mock_result, "_raw_opps": []},
        ) as mock_scan:
            resp = client.post("/api/scan")

        assert resp.status_code == 200
        data = resp.json()
        assert data["scan_id"] == "test-scan"
        assert "_raw_opps" not in data
        mock_scan.assert_awaited_once()

    def test_scan_trigger_failure(self, client: TestClient) -> None:
        """POST /api/scan returns 500 when scan raises."""
        with patch(
            "arb_scanner.cli.orchestrator.run_scan",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Scan exploded"),
        ):
            resp = client.post("/api/scan")

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Unknown route
# ---------------------------------------------------------------------------


class TestUnknownRoute:
    """Tests for unregistered route handling."""

    def test_unknown_route_404(self, client: TestClient) -> None:
        """GET /api/nonexistent returns 404."""
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Discovery health routes
# ---------------------------------------------------------------------------


class TestDiscoveryHealthRoute:
    """Tests for /api/flippenings/discovery-health endpoint."""

    def test_discovery_health_empty(self, client: TestClient, mock_flip_repo: AsyncMock) -> None:
        """GET /api/flippenings/discovery-health returns 200 with empty list."""
        mock_flip_repo.get_discovery_health.return_value = []
        resp = client.get("/api/flippenings/discovery-health")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_discovery_health_with_data(
        self, client: TestClient, mock_flip_repo: AsyncMock
    ) -> None:
        """GET /api/flippenings/discovery-health returns health snapshots."""
        mock_flip_repo.get_discovery_health.return_value = [
            {"total_scanned": 500, "sports_found": 12, "hit_rate": 0.024}
        ]
        resp = client.get("/api/flippenings/discovery-health")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["total_scanned"] == 500

    def test_discovery_health_limit_param(
        self, client: TestClient, mock_flip_repo: AsyncMock
    ) -> None:
        """GET /api/flippenings/discovery-health?limit=5 passes limit to repo."""
        mock_flip_repo.get_discovery_health.return_value = []
        resp = client.get("/api/flippenings/discovery-health?limit=5")
        assert resp.status_code == 200
        mock_flip_repo.get_discovery_health.assert_awaited_once_with(limit=5)

    def test_discovery_health_db_error(self, client: TestClient, mock_flip_repo: AsyncMock) -> None:
        """GET /api/flippenings/discovery-health returns 503 when repo raises."""
        mock_flip_repo.get_discovery_health.side_effect = RuntimeError("DB down")
        resp = client.get("/api/flippenings/discovery-health")
        assert resp.status_code == 503


class TestWsHealthRoute:
    """Tests for /api/flippenings/ws-health endpoint."""

    def test_ws_health_empty(self, client: TestClient, mock_flip_repo: AsyncMock) -> None:
        """GET /api/flippenings/ws-health returns 200 with empty list."""
        mock_flip_repo.get_ws_telemetry.return_value = []
        resp = client.get("/api/flippenings/ws-health")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_ws_health_with_data(self, client: TestClient, mock_flip_repo: AsyncMock) -> None:
        """GET /api/flippenings/ws-health returns telemetry snapshots."""
        mock_flip_repo.get_ws_telemetry.return_value = [
            {"messages_received": 1000, "schema_match_rate": 0.95}
        ]
        resp = client.get("/api/flippenings/ws-health")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["messages_received"] == 1000

    def test_ws_health_db_error(self, client: TestClient, mock_flip_repo: AsyncMock) -> None:
        """GET /api/flippenings/ws-health returns 503 when repo raises."""
        mock_flip_repo.get_ws_telemetry.side_effect = RuntimeError("DB down")
        resp = client.get("/api/flippenings/ws-health")
        assert resp.status_code == 503
