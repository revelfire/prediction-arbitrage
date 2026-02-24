"""Live API tests for Claude semantic matching.

Requires LIVE_TESTS=1 and ANTHROPIC_API_KEY environment variables.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arb_scanner.matching.semantic import evaluate_pairs
from arb_scanner.models.config import ClaudeConfig
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult

from tests.live.conftest import requires_anthropic, requires_live

_CLAUDE_TIMEOUT = 60.0


def _make_market(venue: Venue, event_id: str, title: str) -> Market:
    """Build a minimal Market for testing."""
    return Market(
        venue=venue,
        event_id=event_id,
        title=title,
        description=f"Test market: {title}",
        resolution_criteria="Resolves based on official sources.",
        yes_bid=Decimal("0.45"),
        yes_ask=Decimal("0.55"),
        no_bid=Decimal("0.45"),
        no_ask=Decimal("0.55"),
        volume_24h=Decimal("10000"),
        expiry=datetime(2026, 12, 31, tzinfo=UTC),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=datetime.now(tz=UTC),
        raw_data={},
    )


def _claude_config() -> ClaudeConfig:
    """Build a ClaudeConfig from the environment."""
    return ClaudeConfig(api_key=os.environ["ANTHROPIC_API_KEY"])


@pytest.mark.live
class TestClaudeLive:
    """Live integration tests for Claude semantic matching."""

    @requires_live
    @requires_anthropic
    @pytest.mark.asyncio
    async def test_evaluate_single_pair_returns_match_result(self) -> None:
        """evaluate_pairs with one pair returns exactly one MatchResult."""
        poly = _make_market(
            Venue.POLYMARKET,
            "poly-fed-rate-001",
            "Will the Fed cut rates in 2026?",
        )
        kalshi = _make_market(
            Venue.KALSHI,
            "kalshi-fed-rate-001",
            "Federal Reserve rate cut 2026",
        )
        results = await asyncio.wait_for(
            evaluate_pairs([(poly, kalshi, 0.85)], _claude_config()),
            timeout=_CLAUDE_TIMEOUT,
        )
        assert isinstance(results, list)
        assert len(results) == 1
        assert isinstance(results[0], MatchResult)

    @requires_live
    @requires_anthropic
    @pytest.mark.asyncio
    async def test_match_confidence_is_float_in_range(self) -> None:
        """MatchResult.match_confidence is a float in [0.0, 1.0]."""
        poly = _make_market(
            Venue.POLYMARKET,
            "poly-fed-rate-002",
            "Will the Fed cut rates in 2026?",
        )
        kalshi = _make_market(
            Venue.KALSHI,
            "kalshi-fed-rate-002",
            "Federal Reserve rate cut 2026",
        )
        results = await asyncio.wait_for(
            evaluate_pairs([(poly, kalshi, 0.85)], _claude_config()),
            timeout=_CLAUDE_TIMEOUT,
        )
        result = results[0]
        assert isinstance(result.match_confidence, float)
        assert 0.0 <= result.match_confidence <= 1.0

    @requires_live
    @requires_anthropic
    @pytest.mark.asyncio
    async def test_reasoning_is_non_empty(self) -> None:
        """MatchResult.reasoning is a non-empty string."""
        poly = _make_market(
            Venue.POLYMARKET,
            "poly-fed-rate-003",
            "Will the Fed cut rates in 2026?",
        )
        kalshi = _make_market(
            Venue.KALSHI,
            "kalshi-fed-rate-003",
            "Federal Reserve rate cut 2026",
        )
        results = await asyncio.wait_for(
            evaluate_pairs([(poly, kalshi, 0.85)], _claude_config()),
            timeout=_CLAUDE_TIMEOUT,
        )
        result = results[0]
        assert isinstance(result.reasoning, str)
        assert len(result.reasoning) > 0

    @requires_live
    @requires_anthropic
    @pytest.mark.asyncio
    async def test_dissimilar_pair_low_confidence(self) -> None:
        """Clearly different markets should get low match confidence."""
        poly = _make_market(
            Venue.POLYMARKET,
            "poly-btc-100k",
            "Will Bitcoin exceed $100,000 by end of 2026?",
        )
        kalshi = _make_market(
            Venue.KALSHI,
            "kalshi-superbowl",
            "Kansas City Chiefs win Super Bowl LXI",
        )
        # Small sleep to avoid rate limiting
        await asyncio.sleep(1.0)
        results = await asyncio.wait_for(
            evaluate_pairs([(poly, kalshi, 0.20)], _claude_config()),
            timeout=_CLAUDE_TIMEOUT,
        )
        result = results[0]
        # Very different markets should have low confidence
        assert result.match_confidence < 0.5
        assert not result.safe_to_arb
