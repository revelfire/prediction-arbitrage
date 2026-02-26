"""Tests for the discovery health dashboard feature (015).

Covers API endpoints, repository method wiring, and alert persistence flow.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from arb_scanner.api.app import create_app
from arb_scanner.api.deps import get_flip_repo
from arb_scanner.flippening._orch_alerts import (
    _extract_category_from_alert,
    _persist_alerts,
)
from arb_scanner.flippening.market_classifier import DiscoveryHealthSnapshot
from arb_scanner.models.config import (
    CategoryConfig,
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


def _sample_health_snapshot() -> dict[str, Any]:
    """Return a sample discovery health snapshot dict."""
    return {
        "cycle_timestamp": _NOW.isoformat(),
        "total_scanned": 500,
        "sports_found": 12,
        "hit_rate": 0.024,
        "by_sport": '{"nba": 5, "nfl": 7}',
        "overrides_applied": 0,
        "exclusions_applied": 0,
        "unclassified_candidates": 10,
    }


def _sample_alert() -> dict[str, Any]:
    """Return a sample discovery alert dict."""
    return {
        "id": 1,
        "alert_text": "Category 'nba' returned 0 results for 3 cycles",
        "category": "nba",
        "resolved": False,
        "created_at": _NOW.isoformat(),
        "resolved_at": None,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_flip_repo() -> AsyncMock:
    """Create a mock FlippeningRepository for discovery health tests."""
    repo = AsyncMock()
    repo.get_active_signals = AsyncMock(return_value=[])
    repo.get_history = AsyncMock(return_value=[])
    repo.get_stats = AsyncMock(return_value=[])
    repo.get_recent_events = AsyncMock(return_value=[])
    repo.get_discovery_health = AsyncMock(return_value=[])
    repo.get_ws_telemetry = AsyncMock(return_value=[])
    repo.get_discovery_health_history = AsyncMock(return_value=[])
    repo.get_discovery_alerts = AsyncMock(return_value=[])
    repo.insert_discovery_alert = AsyncMock()
    repo.resolve_discovery_alerts = AsyncMock()
    return repo


@pytest.fixture()
def client(mock_flip_repo: AsyncMock) -> TestClient:
    """Build a TestClient with mocked DB and overridden dependencies."""
    config = _test_config()
    with (
        patch("arb_scanner.storage.db.Database.connect", new_callable=AsyncMock),
        patch("arb_scanner.storage.db.Database.disconnect", new_callable=AsyncMock),
    ):
        app = create_app(config)
        app.dependency_overrides[get_flip_repo] = lambda: mock_flip_repo
        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc


# ---------------------------------------------------------------------------
# Discovery health history endpoint
# ---------------------------------------------------------------------------


class TestDiscoveryHealthHistory:
    """Tests for GET /api/flippenings/discovery-health/history."""

    def test_history_empty(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns 200 with empty list when no snapshots exist."""
        resp = client.get("/api/flippenings/discovery-health/history")
        assert resp.status_code == 200
        assert resp.json() == []
        mock_flip_repo.get_discovery_health_history.assert_awaited_once()

    def test_history_with_data(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns snapshots ordered chronologically."""
        mock_flip_repo.get_discovery_health_history.return_value = [
            _sample_health_snapshot(),
        ]
        resp = client.get("/api/flippenings/discovery-health/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["total_scanned"] == 500

    def test_history_hours_param(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Passes hours parameter as since datetime to repo."""
        resp = client.get("/api/flippenings/discovery-health/history?hours=48")
        assert resp.status_code == 200
        mock_flip_repo.get_discovery_health_history.assert_awaited_once()
        call_args = mock_flip_repo.get_discovery_health_history.call_args
        since_arg = call_args.kwargs.get("since") or call_args[1].get("since")
        assert since_arg is not None

    def test_history_db_error(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns 503 when repository raises."""
        mock_flip_repo.get_discovery_health_history.side_effect = RuntimeError("down")
        resp = client.get("/api/flippenings/discovery-health/history")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Discovery alerts endpoint
# ---------------------------------------------------------------------------


class TestDiscoveryAlerts:
    """Tests for GET /api/flippenings/discovery-health/alerts."""

    def test_alerts_empty(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns 200 with empty list when no alerts exist."""
        resp = client.get("/api/flippenings/discovery-health/alerts")
        assert resp.status_code == 200
        assert resp.json() == []
        mock_flip_repo.get_discovery_alerts.assert_awaited_once_with(limit=20)

    def test_alerts_with_data(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns alert dicts from repository."""
        mock_flip_repo.get_discovery_alerts.return_value = [_sample_alert()]
        resp = client.get("/api/flippenings/discovery-health/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["category"] == "nba"

    def test_alerts_limit_param(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Passes limit parameter to repo."""
        resp = client.get("/api/flippenings/discovery-health/alerts?limit=5")
        assert resp.status_code == 200
        mock_flip_repo.get_discovery_alerts.assert_awaited_once_with(limit=5)

    def test_alerts_db_error(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns 503 when repository raises."""
        mock_flip_repo.get_discovery_alerts.side_effect = RuntimeError("down")
        resp = client.get("/api/flippenings/discovery-health/alerts")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Alert persistence helpers
# ---------------------------------------------------------------------------


class TestAlertPersistence:
    """Tests for _persist_alerts and _extract_category_from_alert."""

    def test_extract_category_nba(self) -> None:
        """Extracts category from alert mentioning a known category."""
        cats = {"nba": CategoryConfig(), "nfl": CategoryConfig()}
        result = _extract_category_from_alert(
            "Category 'nba' returned 0 results for 3 consecutive cycles",
            cats,
        )
        assert result == "nba"

    def test_extract_category_hit_rate(self) -> None:
        """Returns 'hit_rate' for hit rate alert messages."""
        cats: dict[str, CategoryConfig] = {}
        result = _extract_category_from_alert(
            "Classification hit rate 0.01 below threshold 0.02",
            cats,
        )
        assert result == "hit_rate"

    def test_extract_category_zero_drop(self) -> None:
        """Returns 'markets_zero' for zero-drop alert messages."""
        cats: dict[str, CategoryConfig] = {}
        result = _extract_category_from_alert(
            "Market discovery dropped to 0 results (previous: 12)",
            cats,
        )
        assert result == "markets_zero"

    def test_extract_category_unknown(self) -> None:
        """Returns empty string for unrecognized alert messages."""
        cats: dict[str, CategoryConfig] = {}
        result = _extract_category_from_alert("Some unknown alert", cats)
        assert result == ""

    @pytest.mark.asyncio()
    async def test_persist_alerts_inserts_and_resolves(self) -> None:
        """Inserts new alerts and resolves recovered categories."""
        repo = AsyncMock()
        repo.insert_discovery_alert = AsyncMock()
        repo.resolve_discovery_alerts = AsyncMock()

        cats = {"nba": CategoryConfig(), "nfl": CategoryConfig()}
        health = DiscoveryHealthSnapshot(
            total_scanned=500,
            markets_found=12,
            hit_rate=0.024,
            by_category={"nba": 5, "nfl": 0},
            by_category_type={"sport": 5},
            overrides_applied=0,
            exclusions_applied=0,
            unclassified_candidates=10,
        )
        alerts = [
            "Category 'nfl' returned 0 results for 3 consecutive cycles",
        ]

        await _persist_alerts(repo, alerts, cats, health)

        repo.insert_discovery_alert.assert_awaited_once()
        call_args = repo.insert_discovery_alert.call_args
        assert call_args[0][0] == alerts[0]
        assert call_args[0][1] == "nfl"

        # nba has count > 0 so it should be resolved
        repo.resolve_discovery_alerts.assert_awaited_once_with("nba")

    @pytest.mark.asyncio()
    async def test_persist_alerts_handles_insert_error(self) -> None:
        """Continues when insert fails for a single alert."""
        repo = AsyncMock()
        repo.insert_discovery_alert = AsyncMock(side_effect=RuntimeError("DB"))
        repo.resolve_discovery_alerts = AsyncMock()

        cats: dict[str, CategoryConfig] = {}
        health = DiscoveryHealthSnapshot(
            total_scanned=100,
            markets_found=0,
            hit_rate=0.0,
            by_category={},
            by_category_type={},
            overrides_applied=0,
            exclusions_applied=0,
            unclassified_candidates=0,
        )
        # Should not raise
        await _persist_alerts(repo, ["test alert"], cats, health)
