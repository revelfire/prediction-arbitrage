"""T032 - Integration tests for the Claude-powered semantic matcher.

Mocks the Anthropic Claude API responses using the fixture at
tests/fixtures/claude_match_response.json to test MatchResult parsing,
malformed response handling, and batching logic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb_scanner.matching.semantic import (
    _parse_match_results,
    evaluate_pairs,
)
from arb_scanner.models.config import ClaudeConfig
from arb_scanner.models.market import Market, Venue

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_NOW = datetime.now(tz=timezone.utc)


def _load_fixture(name: str) -> Any:
    """Load a JSON fixture file by name."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _make_market(venue: Venue, event_id: str, title: str) -> Market:
    """Build a Market with specific venue, event_id, and title."""
    return Market(
        venue=venue,
        event_id=event_id,
        title=title,
        description="Test",
        resolution_criteria="Test criteria",
        yes_bid=Decimal("0.40"),
        yes_ask=Decimal("0.45"),
        no_bid=Decimal("0.50"),
        no_ask=Decimal("0.55"),
        volume_24h=Decimal("1000"),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW,
    )


def _make_pair(
    poly_id: str,
    kalshi_id: str,
    poly_title: str = "Test Poly",
    kalshi_title: str = "Test Kalshi",
) -> tuple[Market, Market, float]:
    """Build a (poly_market, kalshi_market, bm25_score) tuple."""
    poly = _make_market(Venue.POLYMARKET, poly_id, poly_title)
    kalshi = _make_market(Venue.KALSHI, kalshi_id, kalshi_title)
    return (poly, kalshi, 5.0)


# ---------------------------------------------------------------------------
# MatchResult parsing from Claude response
# ---------------------------------------------------------------------------


class TestMatchResultParsing:
    """Verify Claude JSON responses are parsed into MatchResult models."""

    def test_parse_valid_match_response(self) -> None:
        """Verify a valid Claude JSON response produces a MatchResult."""
        fixture = _load_fixture("claude_match_response.json")
        text = fixture[0]["content"][0]["text"]
        pairs = [
            _make_pair(
                "0x1234567890abcdef1234567890abcdef12345678",
                "BTC-100K-26",
            ),
        ]

        results = _parse_match_results(text, pairs, ttl_hours=24)
        assert len(results) == 1
        assert results[0].poly_event_id == "0x1234567890abcdef1234567890abcdef12345678"
        assert results[0].kalshi_event_id == "BTC-100K-26"
        assert results[0].safe_to_arb is True
        assert results[0].match_confidence == 0.95

    def test_parse_non_matching_response(self) -> None:
        """Verify a non-matching Claude response has safe_to_arb=False."""
        fixture = _load_fixture("claude_match_response.json")
        text = fixture[1]["content"][0]["text"]
        pairs = [
            _make_pair(
                "0x5678901234abcdef5678901234abcdef56789012",
                "AI-MODEL-GPT5-JUN26",
            ),
        ]

        results = _parse_match_results(text, pairs, ttl_hours=24)
        assert len(results) == 1
        assert results[0].safe_to_arb is False
        assert results[0].resolution_equivalent is False
        assert len(results[0].resolution_risks) > 0

    def test_parse_sets_ttl_expires(self) -> None:
        """Verify ttl_expires is set based on ttl_hours parameter."""
        fixture = _load_fixture("claude_match_response.json")
        text = fixture[0]["content"][0]["text"]
        pairs = [
            _make_pair(
                "0x1234567890abcdef1234567890abcdef12345678",
                "BTC-100K-26",
            ),
        ]

        results = _parse_match_results(text, pairs, ttl_hours=48)
        assert len(results) == 1
        assert results[0].ttl_expires > results[0].matched_at


# ---------------------------------------------------------------------------
# Malformed response handling
# ---------------------------------------------------------------------------


class TestMalformedResponseHandling:
    """Verify malformed responses fall back to safe_to_arb=False."""

    def test_invalid_json_returns_fallback(self) -> None:
        """Verify non-JSON text produces fallback MatchResults."""
        pairs = [_make_pair("poly-1", "kalshi-1")]
        results = _parse_match_results("not valid json at all", pairs, ttl_hours=24)
        assert len(results) == 1
        assert results[0].safe_to_arb is False
        assert results[0].match_confidence == 0.0
        assert "could not parse" in results[0].reasoning.lower()

    def test_empty_response_returns_fallback(self) -> None:
        """Verify an empty string produces fallback MatchResults."""
        pairs = [_make_pair("poly-2", "kalshi-2")]
        results = _parse_match_results("", pairs, ttl_hours=24)
        assert len(results) == 1
        assert results[0].safe_to_arb is False

    def test_partial_json_returns_fallback(self) -> None:
        """Verify truncated JSON produces fallback MatchResults."""
        pairs = [_make_pair("poly-3", "kalshi-3")]
        results = _parse_match_results('{"poly_event_id": "poly-3"', pairs, ttl_hours=24)
        assert len(results) == 1
        assert results[0].safe_to_arb is False

    def test_fallback_preserves_event_ids(self) -> None:
        """Verify fallback results preserve the original event IDs."""
        pairs = [_make_pair("poly-fb", "kalshi-fb")]
        results = _parse_match_results("garbage", pairs, ttl_hours=24)
        assert results[0].poly_event_id == "poly-fb"
        assert results[0].kalshi_event_id == "kalshi-fb"


# ---------------------------------------------------------------------------
# Batching logic
# ---------------------------------------------------------------------------


class TestBatchingLogic:
    """Verify pairs are batched correctly and API is called per batch."""

    @pytest.mark.asyncio()
    async def test_batch_size_controls_api_calls(self) -> None:
        """Verify batch_size=2 with 4 pairs makes 2 API calls."""
        fixture = _load_fixture("claude_match_response.json")
        response_text = fixture[0]["content"][0]["text"]

        pairs = [
            _make_pair("poly-b1", "kalshi-b1"),
            _make_pair("poly-b2", "kalshi-b2"),
            _make_pair("poly-b3", "kalshi-b3"),
            _make_pair("poly-b4", "kalshi-b4"),
        ]

        config = ClaudeConfig(
            api_key="test-key",
            model="test-model",
            batch_size=2,
            match_cache_ttl_hours=24,
        )

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=response_text)]

        with patch("arb_scanner.matching.semantic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_message)
            mock_cls.return_value = mock_client

            results = await evaluate_pairs(pairs, config)

            assert mock_client.messages.create.call_count == 2
            assert len(results) > 0

    @pytest.mark.asyncio()
    async def test_single_batch_with_small_input(self) -> None:
        """Verify 2 pairs with batch_size=5 makes only 1 API call."""
        fixture = _load_fixture("claude_match_response.json")
        response_text = fixture[0]["content"][0]["text"]

        pairs = [
            _make_pair("poly-s1", "kalshi-s1"),
            _make_pair("poly-s2", "kalshi-s2"),
        ]

        config = ClaudeConfig(
            api_key="test-key",
            model="test-model",
            batch_size=5,
            match_cache_ttl_hours=24,
        )

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=response_text)]

        with patch("arb_scanner.matching.semantic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_message)
            mock_cls.return_value = mock_client

            await evaluate_pairs(pairs, config)
            assert mock_client.messages.create.call_count == 1

    @pytest.mark.asyncio()
    async def test_empty_pairs_no_api_calls(self) -> None:
        """Verify no API calls are made when pairs list is empty."""
        config = ClaudeConfig(
            api_key="test-key",
            model="test-model",
            batch_size=5,
        )

        results = await evaluate_pairs([], config)
        assert results == []

    @pytest.mark.asyncio()
    async def test_api_error_produces_fallbacks(self) -> None:
        """Verify API errors produce fallback MatchResults with safe_to_arb=False."""
        pairs = [_make_pair("poly-err", "kalshi-err")]

        config = ClaudeConfig(
            api_key="test-key",
            model="test-model",
            batch_size=5,
            match_cache_ttl_hours=24,
        )

        with patch("arb_scanner.matching.semantic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API down"))
            mock_cls.return_value = mock_client

            results = await evaluate_pairs(pairs, config)
            assert len(results) == 1
            assert results[0].safe_to_arb is False
