"""Unit tests for the AI trade critic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.execution.trade_critic import TradeCritic
from arb_scanner.models._auto_exec_config import CriticConfig
from arb_scanner.models.config import ClaudeConfig


def _make_critic(**overrides: object) -> TradeCritic:
    """Build a TradeCritic with default configs."""
    critic_cfg = CriticConfig(**overrides)  # type: ignore[arg-type]
    claude_cfg = ClaudeConfig(api_key="test-key")
    return TradeCritic(critic_cfg, claude_cfg)


def _base_context(**overrides: object) -> dict:
    """Build a base market context dict."""
    ctx: dict = {
        "price_age_seconds": 10,
        "spread_pct": 0.04,
        "poly_depth": 100,
        "kalshi_depth": 80,
        "poly_yes_price": 0.55,
        "kalshi_yes_price": 0.45,
        "title": "Will event X happen?",
    }
    ctx.update(overrides)
    return ctx


class TestCheckMechanicalFlags:
    """Tests for TradeCritic._check_mechanical_flags."""

    def test_stale_data_flag(self) -> None:
        """Flags stale data when price_age_seconds exceeds threshold."""
        critic = _make_critic(price_staleness_seconds=30)
        ctx = _base_context(price_age_seconds=60)
        flags = critic._check_mechanical_flags({}, {}, ctx)
        assert any("stale_data" in f for f in flags)

    def test_anomalous_spread_flag(self) -> None:
        """Flags anomalous spread when above threshold."""
        critic = _make_critic(anomaly_spread_pct=0.20)
        ctx = _base_context(spread_pct=0.35)
        flags = critic._check_mechanical_flags({}, {}, ctx)
        assert any("anomalous_spread" in f for f in flags)

    def test_low_depth_poly_flag(self) -> None:
        """Flags low Polymarket book depth."""
        critic = _make_critic(min_book_depth_contracts=50)
        ctx = _base_context(poly_depth=5, kalshi_depth=100)
        flags = critic._check_mechanical_flags({}, {}, ctx)
        assert any("low_depth_poly_depth" in f for f in flags)

    def test_low_depth_kalshi_flag(self) -> None:
        """Flags low Kalshi book depth."""
        critic = _make_critic(min_book_depth_contracts=50)
        ctx = _base_context(poly_depth=100, kalshi_depth=3)
        flags = critic._check_mechanical_flags({}, {}, ctx)
        assert any("low_depth_kalshi_depth" in f for f in flags)

    def test_category_risk_keywords(self) -> None:
        """Flags risk keywords found in market title."""
        critic = _make_critic()
        for word in ("cancelled", "postponed", "suspended", "voided", "disputed"):
            ctx = _base_context(title=f"Game {word} due to weather")
            flags = critic._check_mechanical_flags({}, {}, ctx)
            assert any("category_risk" in f for f in flags), f"Expected flag for '{word}'"

    def test_no_flags_clean_context(self) -> None:
        """No flags when all values are within thresholds."""
        critic = _make_critic()
        ctx = _base_context()
        flags = critic._check_mechanical_flags({}, {}, ctx)
        assert flags == []


class TestEvaluate:
    """Tests for TradeCritic.evaluate()."""

    @pytest.mark.asyncio()
    async def test_skipped_when_no_flags(self) -> None:
        """Returns skipped=True when no mechanical flags detected."""
        critic = _make_critic()
        ctx = _base_context()
        result = await critic.evaluate({}, {}, ctx)
        assert result.skipped is True
        assert result.approved is True

    @pytest.mark.asyncio()
    async def test_skipped_when_disabled(self) -> None:
        """Returns skipped=True when critic is disabled."""
        critic = _make_critic(enabled=False)
        ctx = _base_context(price_age_seconds=999)
        result = await critic.evaluate({}, {}, ctx)
        assert result.skipped is True
        assert result.approved is True

    @pytest.mark.asyncio()
    async def test_too_many_flags_auto_rejects(self) -> None:
        """Auto-rejects when flag count exceeds max_risk_flags."""
        critic = _make_critic(
            max_risk_flags=1,
            price_staleness_seconds=5,
            anomaly_spread_pct=0.01,
            min_book_depth_contracts=200,
        )
        ctx = _base_context(
            price_age_seconds=100,
            spread_pct=0.50,
            poly_depth=5,
            kalshi_depth=5,
        )
        result = await critic.evaluate({}, {}, ctx)
        assert result.approved is False
        assert "Too many mechanical flags" in result.reasoning
        assert len(result.risk_flags) > 1


class TestParseVerdict:
    """Tests for TradeCritic._parse_verdict."""

    def test_valid_json(self) -> None:
        """Parses valid JSON response from Claude."""
        critic = _make_critic()
        raw = '{"approved": false, "risk_flags": ["stale"], "reasoning": "old", "confidence": 0.7}'
        v = critic._parse_verdict(raw, ["mech_flag"])
        assert v.approved is False
        assert v.risk_flags == ["stale"]
        assert v.reasoning == "old"
        assert v.confidence == 0.7

    def test_markdown_wrapped_json(self) -> None:
        """Parses JSON wrapped in markdown code fences."""
        critic = _make_critic()
        raw = '```json\n{"approved": true, "risk_flags": [], "reasoning": "ok", "confidence": 0.9}\n```'
        v = critic._parse_verdict(raw, [])
        assert v.approved is True
        assert v.confidence == 0.9

    def test_invalid_json_falls_back_to_approve(self) -> None:
        """Falls back to approved=True on parse failure."""
        critic = _make_critic()
        raw = "This is not JSON at all"
        v = critic._parse_verdict(raw, ["flag_a"])
        assert v.approved is True
        assert v.error == "parse_failed"
        assert v.risk_flags == ["flag_a"]


class TestCallCritic:
    """Tests for TradeCritic._call_critic with mocked Anthropic client."""

    @pytest.mark.asyncio()
    async def test_successful_call(self) -> None:
        """Successful Claude API call returns parsed verdict."""
        critic = _make_critic()

        mock_content = MagicMock()
        mock_content.text = (
            '{"approved": true, "risk_flags": [], "reasoning": "ok", "confidence": 0.9}'
        )
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            ctx = _base_context(mechanical_flags=["stale_data"])
            result = await critic._call_critic({}, {}, ctx)

        assert result.approved is True
        assert result.confidence == 0.9

    @pytest.mark.asyncio()
    async def test_timeout_returns_approved_with_error(self) -> None:
        """Timeout returns approved=True with error field set."""
        critic = _make_critic(timeout_seconds=0.001)

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=TimeoutError("timed out"))

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            ctx = _base_context(mechanical_flags=["low_depth"])
            result = await critic._call_critic({}, {}, ctx)

        assert result.approved is True
        assert result.error == "timeout"
        assert "low_depth" in result.risk_flags

    @pytest.mark.asyncio()
    async def test_api_error_returns_approved_with_error(self) -> None:
        """General API error returns approved=True (fail-open)."""
        critic = _make_critic()

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API down"))

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            result = await critic._call_critic({}, {}, {})

        assert result.approved is True
        assert "API down" in (result.error or "")
