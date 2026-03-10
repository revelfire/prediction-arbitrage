"""Unit tests for the arbitrage trade critic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.execution._critic_prompts import CRITIC_SYSTEM_PROMPT
from arb_scanner.execution.arb_critic import ArbTradeCritic
from arb_scanner.models._auto_exec_config import CriticConfig
from arb_scanner.models.config import ClaudeConfig


def _critic(**overrides: object) -> ArbTradeCritic:
    cc = CriticConfig(**overrides)  # type: ignore[arg-type]
    claude = ClaudeConfig(api_key="test-key")
    return ArbTradeCritic(cc, claude)


def _context(**overrides: object) -> dict:
    base = {
        "title": "NBA Game Spread",
        "category": "nba",
        "confidence": 0.85,
        "spread_pct": 0.08,
        "poly_yes_price": 0.55,
        "kalshi_yes_price": 0.47,
        "poly_depth": 500,
        "kalshi_depth": 300,
        "price_age_seconds": 10,
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


class TestArbCriticMechanical:
    """Tests for mechanical flag detection."""

    @pytest.mark.asyncio
    async def test_clean_verdict_no_flags(self) -> None:
        """Returns clean verdict when no flags raised."""
        critic = _critic()
        verdict = await critic.evaluate({"arb_id": "a1"}, _context())
        assert verdict.approved is True
        assert verdict.skipped is True

    @pytest.mark.asyncio
    async def test_stale_price_flag(self) -> None:
        """Raises stale price flag when age exceeds threshold."""
        critic = _critic(price_staleness_seconds=30)
        ctx = _context(price_age_seconds=60)
        with patch(
            "arb_scanner.execution.arb_critic.ArbTradeCritic._call_claude",
            new_callable=AsyncMock,
        ) as mock_claude:
            from arb_scanner.models.auto_execution import CriticVerdict as CV

            mock_claude.return_value = CV(approved=True, risk_flags=["stale_data: price age 60s"])
            await critic.evaluate({"arb_id": "a1"}, ctx)
            assert mock_claude.called

    @pytest.mark.asyncio
    async def test_low_poly_depth_flag(self) -> None:
        """Raises low poly depth flag."""
        critic = _critic(min_book_depth_contracts=100)
        ctx = _context(poly_depth=5)
        with patch(
            "arb_scanner.execution.arb_critic.ArbTradeCritic._call_claude",
            new_callable=AsyncMock,
        ) as mock_claude:
            from arb_scanner.models.auto_execution import CriticVerdict as CV

            mock_claude.return_value = CV(approved=True, risk_flags=["low_depth_poly_depth: 5"])
            verdict = await critic.evaluate({"arb_id": "a1"}, ctx)
            assert mock_claude.called
            assert any("low_depth_poly_depth" in f for f in verdict.risk_flags)

    @pytest.mark.asyncio
    async def test_low_kalshi_depth_flag(self) -> None:
        """Raises low kalshi depth flag."""
        critic = _critic(min_book_depth_contracts=100)
        ctx = _context(kalshi_depth=3)
        with patch(
            "arb_scanner.execution.arb_critic.ArbTradeCritic._call_claude",
            new_callable=AsyncMock,
        ) as mock_claude:
            from arb_scanner.models.auto_execution import CriticVerdict as CV

            mock_claude.return_value = CV(approved=True, risk_flags=["low_depth_kalshi_depth: 3"])
            await critic.evaluate({"arb_id": "a1"}, ctx)
            assert mock_claude.called

    @pytest.mark.asyncio
    async def test_anomalous_spread_flag(self) -> None:
        """Raises anomalous spread flag."""
        critic = _critic(anomaly_spread_pct=0.20)
        ctx = _context(spread_pct=0.35)
        with patch(
            "arb_scanner.execution.arb_critic.ArbTradeCritic._call_claude",
            new_callable=AsyncMock,
        ) as mock_claude:
            from arb_scanner.models.auto_execution import CriticVerdict as CV

            mock_claude.return_value = CV(approved=True, risk_flags=["anomalous_spread"])
            await critic.evaluate({"arb_id": "a1"}, ctx)
            assert mock_claude.called

    @pytest.mark.asyncio
    async def test_uses_arb_system_prompt(self) -> None:
        """Uses CRITIC_SYSTEM_PROMPT, not flippening prompt."""
        critic = _critic()
        ctx = _context(price_age_seconds=120)

        with patch("anthropic.AsyncAnthropic") as MockClient:
            mock_response = MagicMock()
            mock_response.content = [
                MagicMock(
                    text='{"approved": true, "risk_flags": [], "reasoning": "", "confidence": 0.9}'
                )
            ]
            instance = MockClient.return_value
            instance.messages = MagicMock()
            instance.messages.create = AsyncMock(return_value=mock_response)

            await critic.evaluate({"arb_id": "a1"}, ctx)
            call_kwargs = instance.messages.create.call_args.kwargs
            assert call_kwargs["system"] == CRITIC_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_too_many_flags_blocks(self) -> None:
        """Blocks when flags exceed max_risk_flags."""
        critic = _critic(max_risk_flags=0, price_staleness_seconds=1)
        ctx = _context(price_age_seconds=60, spread_pct=0.50, poly_depth=1, kalshi_depth=1)
        verdict = await critic.evaluate({"arb_id": "a1"}, ctx)
        assert verdict.approved is False
        assert "Too many" in verdict.reasoning

    @pytest.mark.asyncio
    async def test_disabled_critic_approves(self) -> None:
        """Disabled critic auto-approves."""
        critic = _critic(enabled=False)
        verdict = await critic.evaluate({"arb_id": "a1"}, _context())
        assert verdict.approved is True
        assert verdict.skipped is True


class TestArbCriticVerification:
    """US5: Verify arb critic uses depth checks."""

    def test_depth_flags_in_mechanical_checks(self) -> None:
        """Arb critic produces depth-related flags for low depth."""
        critic = _critic(min_book_depth_contracts=100)
        ctx = _context(poly_depth=5, kalshi_depth=3)
        flags = critic._check_mechanical_flags(ctx)
        assert any("depth" in f for f in flags)

    def test_system_prompt_is_arb(self) -> None:
        """Uses CRITIC_SYSTEM_PROMPT in _call_claude."""
        import inspect

        src = inspect.getsource(ArbTradeCritic._call_claude)
        assert "CRITIC_SYSTEM_PROMPT" in src

    def test_no_ticket_type_in_source(self) -> None:
        """The arb_critic source contains no ticket_type conditionals."""
        import inspect

        import arb_scanner.execution.arb_critic as mod

        src = inspect.getsource(mod)
        assert "ticket_type" not in src
