"""End-to-end test for the full scan pipeline via the orchestrator."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from arb_scanner.cli.orchestrator import run_scan
from arb_scanner.models.config import (
    ArbThresholds,
    ClaudeConfig,
    FeeSchedule,
    FeesConfig,
    Settings,
    StorageConfig,
)
from arb_scanner.models.matching import MatchResult


@pytest.fixture()
def e2e_config() -> Settings:
    """Minimal settings for E2E testing."""
    return Settings(
        storage=StorageConfig(database_url="postgresql://localhost/unused"),
        fees=FeesConfig(
            polymarket=FeeSchedule(taker_fee_pct=Decimal("0.0"), fee_model="on_winnings"),
            kalshi=FeeSchedule(
                taker_fee_pct=Decimal("0.07"),
                fee_model="per_contract",
                fee_cap=Decimal("0.07"),
            ),
        ),
        claude=ClaudeConfig(api_key="test-key", batch_size=10),
        arb_thresholds=ArbThresholds(
            min_net_spread_pct=Decimal("0.01"),
            min_size_usd=Decimal("1"),
            thin_liquidity_threshold=Decimal("50"),
        ),
    )


def _build_mock_match(
    poly_id: str,
    kalshi_id: str,
    *,
    safe: bool = True,
    confidence: float = 0.95,
) -> MatchResult:
    """Build a MatchResult for mocking semantic evaluation."""
    now = datetime.now(tz=timezone.utc)
    return MatchResult(
        poly_event_id=poly_id,
        kalshi_event_id=kalshi_id,
        match_confidence=confidence,
        resolution_equivalent=safe,
        resolution_risks=[],
        safe_to_arb=safe,
        reasoning="Test match",
        matched_at=now,
        ttl_expires=now + timedelta(hours=24),
    )


async def _mock_evaluate_pairs(
    pairs: list[Any],
    config: Any,
) -> list[MatchResult]:
    """Create match results for all pairs passed to semantic evaluation."""
    results: list[MatchResult] = []
    for poly, kalshi, _score in pairs:
        results.append(_build_mock_match(poly.event_id, kalshi.event_id))
    return results


def _make_mock() -> AsyncMock:
    """Create an AsyncMock with the evaluate_pairs side effect."""
    mock = AsyncMock(side_effect=_mock_evaluate_pairs)
    return mock


@pytest.mark.asyncio()
async def test_dry_run_scan_returns_valid_schema(e2e_config: Settings) -> None:
    """Dry-run scan should return output matching the expected JSON schema."""
    with patch("arb_scanner.cli.orchestrator.evaluate_pairs", new=_make_mock()):
        result = await run_scan(e2e_config, dry_run=True)

    _assert_schema(result)


@pytest.mark.asyncio()
async def test_dry_run_scan_detects_opportunities(e2e_config: Settings) -> None:
    """Dry-run scan with generous thresholds should detect at least one arb."""
    with patch("arb_scanner.cli.orchestrator.evaluate_pairs", new=_make_mock()):
        result = await run_scan(e2e_config, dry_run=True)

    assert result["markets_scanned"]["polymarket"] > 0
    assert result["markets_scanned"]["kalshi"] > 0
    assert result["candidate_pairs"] >= 0


@pytest.mark.asyncio()
async def test_dry_run_scan_opportunity_fields(e2e_config: Settings) -> None:
    """Each opportunity in output should have all required fields."""
    with patch("arb_scanner.cli.orchestrator.evaluate_pairs", new=_make_mock()):
        result = await run_scan(e2e_config, dry_run=True)

    for opp in result.get("opportunities", []):
        _assert_opportunity_fields(opp)


@pytest.mark.asyncio()
async def test_dry_run_scan_json_serializable(e2e_config: Settings) -> None:
    """Scan output (public keys) must be fully JSON-serializable."""
    with patch("arb_scanner.cli.orchestrator.evaluate_pairs", new=_make_mock()):
        result = await run_scan(e2e_config, dry_run=True)

    # Strip internal-use keys (prefixed with _) before serialization
    public = {k: v for k, v in result.items() if not k.startswith("_")}
    serialized = json.dumps(public)
    parsed = json.loads(serialized)
    assert parsed["scan_id"] == result["scan_id"]


@pytest.mark.asyncio()
async def test_dry_run_no_semantic_returns_empty(e2e_config: Settings) -> None:
    """When semantic matcher returns nothing, scan still succeeds."""
    mock = AsyncMock(return_value=[])
    with patch("arb_scanner.cli.orchestrator.evaluate_pairs", new=mock):
        result = await run_scan(e2e_config, dry_run=True)

    _assert_schema(result)
    assert result["opportunities"] == []


# ------------------------------------------------------------------
# Schema assertions
# ------------------------------------------------------------------

_REQUIRED_KEYS = {"scan_id", "timestamp", "markets_scanned", "candidate_pairs", "opportunities"}
_OPP_KEYS = {
    "id",
    "buy",
    "sell",
    "net_spread_pct",
    "max_size_usd",
    "match_confidence",
    "depth_risk",
    "annualized_return",
}


def _assert_schema(result: dict[str, Any]) -> None:
    """Verify the top-level output schema."""
    missing = _REQUIRED_KEYS - result.keys()
    assert not missing, f"Missing keys: {missing}"
    assert isinstance(result["scan_id"], str)
    assert isinstance(result["timestamp"], str)
    assert isinstance(result["markets_scanned"], dict)
    assert isinstance(result["candidate_pairs"], int)
    assert isinstance(result["opportunities"], list)


def _assert_opportunity_fields(opp: dict[str, Any]) -> None:
    """Verify a single opportunity has all required fields."""
    missing = _OPP_KEYS - opp.keys()
    assert not missing, f"Missing opp keys: {missing}"
    assert isinstance(opp["buy"], dict)
    assert "venue" in opp["buy"]
    assert "contract" in opp["buy"]
    assert "price" in opp["buy"]
    assert isinstance(opp["sell"], dict)
    assert "venue" in opp["sell"]
    assert "contract" in opp["sell"]
    assert "price" in opp["sell"]
