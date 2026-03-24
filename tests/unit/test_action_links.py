"""Tests for notification action link URL builders."""

from __future__ import annotations

from arb_scanner.notifications.action_links import (
    dashboard_position_url,
    exit_action_url,
    polymarket_market_url,
)


class TestDashboardPositionUrl:
    def test_builds_url(self) -> None:
        url = dashboard_position_url("https://arb.spillwave.com", "arb-123")
        assert url == "https://arb.spillwave.com/#positions/arb-123"

    def test_appends_token(self) -> None:
        url = dashboard_position_url("https://arb.spillwave.com", "arb-123", "secret")
        assert "token=secret" in url

    def test_strips_trailing_slash(self) -> None:
        url = dashboard_position_url("https://arb.spillwave.com/", "arb-123")
        assert "com//#" not in url

    def test_empty_base_url(self) -> None:
        assert dashboard_position_url("", "arb-123") == ""

    def test_no_token(self) -> None:
        url = dashboard_position_url("https://arb.spillwave.com", "arb-123", "")
        assert "token" not in url


class TestExitActionUrl:
    def test_builds_url(self) -> None:
        url = exit_action_url("https://arb.spillwave.com", "arb-123", "tok")
        assert "/api/execution/flip-exit/arb-123" in url
        assert "token=tok" in url

    def test_empty_base_url(self) -> None:
        assert exit_action_url("", "arb-123") == ""


class TestPolymarketMarketUrl:
    def test_builds_url(self) -> None:
        url = polymarket_market_url("some-slug")
        assert url == "https://polymarket.com/event/some-slug"

    def test_empty_slug(self) -> None:
        assert polymarket_market_url("") == ""
