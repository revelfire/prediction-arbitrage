"""Unit tests for the flippening trade critic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.execution._critic_prompts import FLIPPENING_CRITIC_SYSTEM_PROMPT
from arb_scanner.execution.flip_critic import FlipTradeCritic
from arb_scanner.models._auto_exec_config import CriticConfig
from arb_scanner.models.config import ClaudeConfig


def _critic(**overrides: object) -> FlipTradeCritic:
    cc = CriticConfig(**overrides)  # type: ignore[arg-type]
    claude = ClaudeConfig(api_key="test-key")
    return FlipTradeCritic(cc, claude)


def _context(**overrides: object) -> dict:
    base = {
        "title": "NBA Game",
        "category": "nba",
        "confidence": 0.85,
        "entry_price": 0.45,
        "side": "YES",
        "baseline_deviation_pct": 0.15,
        "market_id": "market-1",
        "price_age_seconds": 10,
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


class TestFlipCriticMechanical:
    """Tests for mechanical flag detection."""

    @pytest.mark.asyncio
    async def test_clean_verdict_no_flags(self) -> None:
        """Returns clean verdict when no flags raised."""
        critic = _critic()
        verdict = await critic.evaluate({"arb_id": "f1"}, _context())
        assert verdict.approved is True
        assert verdict.skipped is True

    @pytest.mark.asyncio
    async def test_stale_price_flag(self) -> None:
        """Raises stale price flag when age exceeds threshold."""
        critic = _critic(price_staleness_seconds=30)
        ctx = _context(price_age_seconds=60)
        # With only 1 flag, critic will try to call Claude — mock it
        with patch(
            "arb_scanner.execution.flip_critic.FlipTradeCritic._call_claude",
            new_callable=AsyncMock,
        ) as mock_claude:
            from arb_scanner.models.auto_execution import CriticVerdict as CV

            mock_claude.return_value = CV(approved=True, risk_flags=["stale_data: price age 60s"])
            verdict = await critic.evaluate({"arb_id": "f1"}, ctx)
            assert mock_claude.called
            assert any("stale_data" in f for f in verdict.risk_flags)

    @pytest.mark.asyncio
    async def test_anomalous_deviation_flag(self) -> None:
        """Raises anomalous deviation flag when deviation > 0.90."""
        critic = _critic()
        ctx = _context(baseline_deviation_pct=0.95)
        with patch(
            "arb_scanner.execution.flip_critic.FlipTradeCritic._call_claude",
            new_callable=AsyncMock,
        ) as mock_claude:
            from arb_scanner.models.auto_execution import CriticVerdict as CV

            mock_claude.return_value = CV(approved=True, risk_flags=["anomalous_deviation"])
            await critic.evaluate({"arb_id": "f1"}, ctx)
            assert mock_claude.called

    @pytest.mark.asyncio
    async def test_no_depth_checks(self) -> None:
        """Does NOT check poly_depth or kalshi_depth."""
        critic = _critic()
        ctx = _context(poly_depth=1, kalshi_depth=1)  # Very low but should be ignored
        verdict = await critic.evaluate({"arb_id": "f1"}, ctx)
        assert verdict.approved is True
        flags = ctx.get("mechanical_flags", [])
        assert not any("depth" in f for f in flags)

    @pytest.mark.asyncio
    async def test_uses_flip_system_prompt(self) -> None:
        """Uses FLIPPENING_CRITIC_SYSTEM_PROMPT, not arb prompt."""
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

            await critic.evaluate({"arb_id": "f1"}, ctx)
            call_kwargs = instance.messages.create.call_args.kwargs
            assert call_kwargs["system"] == FLIPPENING_CRITIC_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_too_many_flags_blocks(self) -> None:
        """Blocks when flags exceed max_risk_flags."""
        critic = _critic(max_risk_flags=0, price_staleness_seconds=1)
        ctx = _context(price_age_seconds=60, baseline_deviation_pct=0.95)
        ctx["title"] = "cancelled game"  # adds category_risk flag too
        verdict = await critic.evaluate({"arb_id": "f1"}, ctx)
        assert verdict.approved is False
        assert "Too many" in verdict.reasoning

    @pytest.mark.asyncio
    async def test_disabled_critic_approves(self) -> None:
        """Disabled critic auto-approves."""
        critic = _critic(enabled=False)
        verdict = await critic.evaluate({"arb_id": "f1"}, _context())
        assert verdict.approved is True
        assert verdict.skipped is True


class TestFlipCriticVerification:
    """US5: Verify flip critic has no arb-specific checks."""

    def test_no_depth_flags_in_mechanical_checks(self) -> None:
        """Flip critic does NOT produce depth-related flags."""
        critic = _critic()
        ctx = _context(poly_depth=1, kalshi_depth=1)
        flags = critic._check_mechanical_flags(ctx)
        assert not any("depth" in f for f in flags)

    def test_system_prompt_is_flippening(self) -> None:
        """Uses FLIPPENING_CRITIC_SYSTEM_PROMPT in _call_claude."""
        import inspect

        src = inspect.getsource(FlipTradeCritic._call_claude)
        assert "FLIPPENING_CRITIC_SYSTEM_PROMPT" in src

    def test_no_ticket_type_in_source(self) -> None:
        """The flip_critic source contains no ticket_type conditionals."""
        import inspect

        import arb_scanner.execution.flip_critic as mod

        src = inspect.getsource(mod)
        assert "ticket_type" not in src
