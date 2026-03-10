"""Unit tests for AI critic prompt templates."""

from __future__ import annotations

from arb_scanner.execution._critic_prompts import (
    CRITIC_SYSTEM_PROMPT,
    FLIPPENING_CRITIC_SYSTEM_PROMPT,
    build_arb_critic_prompt,
    build_flip_critic_prompt,
)


class TestCriticSystemPrompt:
    """Tests for CRITIC_SYSTEM_PROMPT constant."""

    def test_is_non_empty_string(self) -> None:
        """System prompt is a non-empty string."""
        assert isinstance(CRITIC_SYSTEM_PROMPT, str)
        assert len(CRITIC_SYSTEM_PROMPT) > 50

    def test_contains_json_instruction(self) -> None:
        """System prompt instructs Claude to respond with JSON."""
        assert "JSON" in CRITIC_SYSTEM_PROMPT

    def test_flip_prompt_is_non_empty(self) -> None:
        """Flippening system prompt is a non-empty string."""
        assert isinstance(FLIPPENING_CRITIC_SYSTEM_PROMPT, str)
        assert len(FLIPPENING_CRITIC_SYSTEM_PROMPT) > 50


class TestBuildArbCriticPrompt:
    """Tests for build_arb_critic_prompt()."""

    def test_returns_string_with_market_info(self) -> None:
        """Prompt includes market title, spread, and confidence."""
        ticket = {"arb_id": "t1", "ticket_type": "arbitrage"}
        context = {
            "spread_pct": 0.05,
            "confidence": 0.85,
            "category": "nba",
            "title": "Lakers vs Celtics game outcome",
            "poly_yes_price": 0.55,
            "kalshi_yes_price": 0.45,
            "poly_depth": 200,
            "kalshi_depth": 150,
            "price_age_seconds": 10,
        }
        prompt = build_arb_critic_prompt(ticket, context)
        assert isinstance(prompt, str)
        assert "Lakers vs Celtics" in prompt
        assert "0.05" in prompt
        assert "0.85" in prompt
        assert "nba" in prompt
        assert "t1" in prompt

    def test_with_mechanical_flags(self) -> None:
        """Prompt includes mechanical flags section."""
        ticket = {"arb_id": "t2"}
        context = {
            "mechanical_flags": ["stale_data: price age 120s", "low_depth_poly: 5"],
            "title": "Test market",
        }
        prompt = build_arb_critic_prompt(ticket, context)
        assert "stale_data" in prompt
        assert "low_depth_poly" in prompt

    def test_with_empty_context(self) -> None:
        """Prompt handles empty/missing context values gracefully."""
        ticket = {}
        prompt = build_arb_critic_prompt(ticket, {})
        assert isinstance(prompt, str)
        assert "N/A" in prompt


class TestBuildFlipCriticPrompt:
    """Tests for build_flip_critic_prompt()."""

    def test_returns_string_with_flip_info(self) -> None:
        """Prompt includes entry price, side, and deviation."""
        ticket = {"arb_id": "f1"}
        context = {
            "title": "NBA Game",
            "entry_price": 0.45,
            "side": "yes",
            "baseline_deviation_pct": 0.15,
            "market_id": "m-1",
        }
        prompt = build_flip_critic_prompt(ticket, context)
        assert isinstance(prompt, str)
        assert "0.45" in prompt
        assert "yes" in prompt
        assert "0.15" in prompt
