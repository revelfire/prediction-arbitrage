"""Unit tests for bearer token authentication middleware."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from arb_scanner.api.app import create_app
from arb_scanner.models.config import (
    DashboardConfig,
    FeeSchedule,
    FeesConfig,
    Settings,
    StorageConfig,
)


def _config(*, auth_token: str | None = None) -> Settings:
    """Build a minimal Settings with optional auth token."""
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
        dashboard=DashboardConfig(auth_token=auth_token),
    )


def _client(*, auth_token: str | None = None) -> TestClient:
    """Build a test client with optional auth."""
    config = _config(auth_token=auth_token)
    app = create_app(config, no_db=True)
    return TestClient(app, raise_server_exceptions=False)


# --- Auth disabled (no token configured) ---


class TestAuthDisabled:
    """When auth_token is None, all requests pass through."""

    def test_dashboard_accessible_without_token(self) -> None:
        """Dashboard root should be accessible without any auth."""
        client = _client()
        resp = client.get("/")
        assert resp.status_code == 200

    def test_api_not_blocked_by_auth(self) -> None:
        """API endpoints should not get 401 when auth is disabled."""
        client = _client()
        resp = client.get("/api/health")
        # May be 503 (no DB) but should NOT be 401
        assert resp.status_code != 401

    def test_no_meta_tag_when_no_token(self) -> None:
        """Dashboard HTML should not contain api-token meta tag."""
        client = _client()
        resp = client.get("/")
        assert 'name="api-token"' not in resp.text


# --- Auth enabled ---


class TestAuthEnabled:
    """When auth_token is set, requests require valid bearer token."""

    TOKEN = "test-secret-token-abc123"

    def test_health_exempt_from_auth(self) -> None:
        """/api/health should not return 401 (exempt from auth)."""
        client = _client(auth_token=self.TOKEN)
        resp = client.get("/api/health")
        # May be 503 (no DB) but should NOT be 401
        assert resp.status_code != 401

    def test_api_rejected_without_token(self) -> None:
        """API endpoints should return 401 without token."""
        client = _client(auth_token=self.TOKEN)
        resp = client.get("/api/opportunities")
        assert resp.status_code == 401
        assert resp.json() == {"error": "Unauthorized"}

    def test_api_rejected_with_wrong_token(self) -> None:
        """API endpoints should return 401 with wrong token."""
        client = _client(auth_token=self.TOKEN)
        resp = client.get(
            "/api/opportunities",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_api_allowed_with_bearer_header(self) -> None:
        """API endpoints with correct bearer header should not get 401."""
        client = _client(auth_token=self.TOKEN)
        resp = client.get(
            "/api/health",
            headers={"Authorization": f"Bearer {self.TOKEN}"},
        )
        # Should pass auth (may be 503 due to no DB, but not 401)
        assert resp.status_code != 401

    def test_api_allowed_with_query_param(self) -> None:
        """API endpoints with correct query param should not get 401."""
        client = _client(auth_token=self.TOKEN)
        resp = client.get(f"/api/health?token={self.TOKEN}")
        assert resp.status_code != 401

    def test_dashboard_rejected_without_token(self) -> None:
        """Dashboard root should return 401 without token."""
        client = _client(auth_token=self.TOKEN)
        resp = client.get("/")
        assert resp.status_code == 401

    def test_dashboard_allowed_with_bearer(self) -> None:
        """Dashboard root should work with correct bearer."""
        client = _client(auth_token=self.TOKEN)
        resp = client.get(
            "/",
            headers={"Authorization": f"Bearer {self.TOKEN}"},
        )
        assert resp.status_code == 200

    def test_meta_tag_injected_when_token_set(self) -> None:
        """Dashboard HTML should contain api-token meta tag."""
        client = _client(auth_token=self.TOKEN)
        resp = client.get(
            "/",
            headers={"Authorization": f"Bearer {self.TOKEN}"},
        )
        assert resp.status_code == 200
        assert f'name="api-token" content="{self.TOKEN}"' in resp.text

    def test_static_files_exempt_from_auth(self) -> None:
        """Static CSS/JS are exempt so the dashboard page can load assets."""
        client = _client(auth_token=self.TOKEN)
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
