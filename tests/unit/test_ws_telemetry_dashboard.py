"""Tests for the WebSocket telemetry dashboard feature (016).

Covers API endpoints, Pydantic models, repository method wiring,
and edge cases (idle state, DB errors).
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
from arb_scanner.models.config import (
    FeeSchedule,
    FeesConfig,
    Settings,
    StorageConfig,
)
from arb_scanner.models.ws_telemetry import (
    ConnectionState,
    WsHealthSummary,
    WsTelemetryEvent,
    WsTelemetrySnapshot,
)

_NOW = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _test_config() -> Settings:
    """Build a minimal Settings object for testing."""
    return Settings(
        storage=StorageConfig(
            database_url="postgresql://test:test@localhost/test",
        ),
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


def _sample_telemetry_snapshot() -> dict[str, Any]:
    """Return a sample WS telemetry snapshot dict."""
    return {
        "snapshot_time": _NOW.isoformat(),
        "messages_received": 1200,
        "messages_parsed": 1100,
        "messages_failed": 50,
        "messages_ignored": 50,
        "schema_match_rate": 0.92,
        "book_cache_hit_rate": 0.85,
        "connection_state": "connected",
    }


def _sample_event() -> dict[str, Any]:
    """Return a sample stall/reconnect event dict."""
    return {
        "event_time": _NOW.isoformat(),
        "event_type": "stall_detected",
        "prev_state": "connected",
        "new_state": "connected",
        "messages_received_at_event": 500,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_flip_repo() -> AsyncMock:
    """Create a mock FlippeningRepository for WS telemetry tests."""
    repo = AsyncMock()
    repo.get_active_signals = AsyncMock(return_value=[])
    repo.get_history = AsyncMock(return_value=[])
    repo.get_stats = AsyncMock(return_value=[])
    repo.get_recent_events = AsyncMock(return_value=[])
    repo.get_discovery_health = AsyncMock(return_value=[])
    repo.get_ws_telemetry = AsyncMock(return_value=[])
    repo.get_ws_telemetry_latest = AsyncMock(return_value=None)
    repo.get_ws_telemetry_history = AsyncMock(return_value=[])
    repo.get_ws_telemetry_events = AsyncMock(return_value=[])
    repo.get_discovery_health_history = AsyncMock(return_value=[])
    repo.get_discovery_alerts = AsyncMock(return_value=[])
    return repo


@pytest.fixture()
def client(mock_flip_repo: AsyncMock) -> TestClient:
    """Build a TestClient with mocked DB and overridden dependencies."""
    config = _test_config()
    with (
        patch(
            "arb_scanner.storage.db.Database.connect",
            new_callable=AsyncMock,
        ),
        patch(
            "arb_scanner.storage.db.Database.disconnect",
            new_callable=AsyncMock,
        ),
    ):
        app = create_app(config)
        app.dependency_overrides[get_flip_repo] = lambda: mock_flip_repo
        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc


# ---------------------------------------------------------------------------
# GET /api/flippening/ws-telemetry (latest)
# ---------------------------------------------------------------------------


class TestWsTelemetryLatest:
    """Tests for GET /api/flippening/ws-telemetry."""

    def test_latest_returns_none_when_empty(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns null when no telemetry snapshots exist."""
        resp = client.get("/api/flippening/ws-telemetry")
        assert resp.status_code == 200
        assert resp.json() is None
        mock_flip_repo.get_ws_telemetry_latest.assert_awaited_once()

    def test_latest_returns_snapshot(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns the latest telemetry snapshot dict."""
        mock_flip_repo.get_ws_telemetry_latest.return_value = _sample_telemetry_snapshot()
        resp = client.get("/api/flippening/ws-telemetry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages_received"] == 1200
        assert data["connection_state"] == "connected"

    def test_latest_db_error(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns 503 when repository raises."""
        mock_flip_repo.get_ws_telemetry_latest.side_effect = RuntimeError("down")
        resp = client.get("/api/flippening/ws-telemetry")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/flippening/ws-telemetry/history
# ---------------------------------------------------------------------------


class TestWsTelemetryHistory:
    """Tests for GET /api/flippening/ws-telemetry/history."""

    def test_history_empty(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns 200 with empty list when no snapshots exist."""
        resp = client.get("/api/flippening/ws-telemetry/history")
        assert resp.status_code == 200
        assert resp.json() == []
        mock_flip_repo.get_ws_telemetry_history.assert_awaited_once()

    def test_history_with_data(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns telemetry snapshots ordered chronologically."""
        mock_flip_repo.get_ws_telemetry_history.return_value = [
            _sample_telemetry_snapshot(),
        ]
        resp = client.get("/api/flippening/ws-telemetry/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["messages_received"] == 1200

    def test_history_hours_param(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Passes hours parameter as since datetime to repo."""
        resp = client.get(
            "/api/flippening/ws-telemetry/history?hours=48",
        )
        assert resp.status_code == 200
        mock_flip_repo.get_ws_telemetry_history.assert_awaited_once()
        call_args = mock_flip_repo.get_ws_telemetry_history.call_args
        since_arg = call_args.kwargs.get("since")
        assert since_arg is not None

    def test_history_db_error(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns 503 when repository raises."""
        mock_flip_repo.get_ws_telemetry_history.side_effect = RuntimeError("down")
        resp = client.get("/api/flippening/ws-telemetry/history")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/flippening/ws-telemetry/events
# ---------------------------------------------------------------------------


class TestWsTelemetryEvents:
    """Tests for GET /api/flippening/ws-telemetry/events."""

    def test_events_empty(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns 200 with empty list when no events exist."""
        resp = client.get("/api/flippening/ws-telemetry/events")
        assert resp.status_code == 200
        assert resp.json() == []
        mock_flip_repo.get_ws_telemetry_events.assert_awaited_once_with(
            limit=50,
        )

    def test_events_with_data(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns event dicts from repository."""
        mock_flip_repo.get_ws_telemetry_events.return_value = [
            _sample_event(),
        ]
        resp = client.get("/api/flippening/ws-telemetry/events")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["event_type"] == "stall_detected"

    def test_events_limit_param(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Passes limit parameter to repo."""
        resp = client.get("/api/flippening/ws-telemetry/events?limit=10")
        assert resp.status_code == 200
        mock_flip_repo.get_ws_telemetry_events.assert_awaited_once_with(
            limit=10,
        )

    def test_events_db_error(
        self,
        client: TestClient,
        mock_flip_repo: AsyncMock,
    ) -> None:
        """Returns 503 when repository raises."""
        mock_flip_repo.get_ws_telemetry_events.side_effect = RuntimeError("down")
        resp = client.get("/api/flippening/ws-telemetry/events")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Pydantic model validation
# ---------------------------------------------------------------------------


class TestWsTelemetryModels:
    """Tests for WS telemetry Pydantic models."""

    def test_snapshot_defaults(self) -> None:
        """WsTelemetrySnapshot has sensible defaults."""
        snap = WsTelemetrySnapshot(snapshot_time=_NOW)
        assert snap.messages_received == 0
        assert snap.schema_match_rate == 1.0
        assert snap.connection_state == "unknown"

    def test_snapshot_from_dict(self) -> None:
        """WsTelemetrySnapshot can be built from a DB row dict."""
        snap = WsTelemetrySnapshot(**_sample_telemetry_snapshot())
        assert snap.messages_received == 1200
        assert snap.schema_match_rate == 0.92

    def test_event_model(self) -> None:
        """WsTelemetryEvent validates correctly."""
        evt = WsTelemetryEvent(
            event_time=_NOW,
            event_type="stall_detected",
            prev_state="connected",
            new_state="connected",
            messages_received_at_event=500,
        )
        assert evt.event_type == "stall_detected"
        assert evt.prev_state == "connected"

    def test_event_optional_prev_state(self) -> None:
        """WsTelemetryEvent allows None prev_state."""
        evt = WsTelemetryEvent(
            event_time=_NOW,
            event_type="ws_connected",
            new_state="connected",
        )
        assert evt.prev_state is None

    def test_health_summary_defaults(self) -> None:
        """WsHealthSummary has idle defaults."""
        summary = WsHealthSummary()
        assert summary.status == ConnectionState.idle
        assert summary.cache_hit_rate == 0.0
        assert summary.cache_max_size == 200

    def test_connection_state_enum(self) -> None:
        """ConnectionState enum has expected members."""
        assert ConnectionState.connected.value == "connected"
        assert ConnectionState.stalled.value == "stalled"
        assert ConnectionState.disconnected.value == "disconnected"
        assert ConnectionState.idle.value == "idle"

    def test_snapshot_schema_rate_clamped(self) -> None:
        """Schema match rate is validated between 0 and 1."""
        snap = WsTelemetrySnapshot(
            snapshot_time=_NOW,
            schema_match_rate=0.5,
        )
        assert snap.schema_match_rate == 0.5
