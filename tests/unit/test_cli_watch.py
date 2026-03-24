"""Tests for arb watcher opportunity shaping."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from arb_scanner.cli.watch import _build_arb_opp_dict
from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult

_NOW = datetime.now(tz=timezone.utc)
_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _market(venue: Venue, event_id: str, title: str) -> Market:
    return Market(
        venue=venue,
        event_id=event_id,
        title=title,
        description="Test",
        resolution_criteria="Test",
        yes_bid=Decimal("0.50"),
        yes_ask=Decimal("0.52"),
        no_bid=Decimal("0.46"),
        no_ask=Decimal("0.48"),
        volume_24h=Decimal("1000"),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW,
        raw_data={"clobTokenIds": '["poly-token-1"]'},
    )


def _opp() -> ArbOpportunity:
    return ArbOpportunity(
        match=MatchResult(
            poly_event_id="poly-evt",
            kalshi_event_id="kalshi-evt",
            match_confidence=0.82,
            resolution_equivalent=True,
            resolution_risks=[],
            safe_to_arb=True,
            reasoning="safe",
            matched_at=_NOW,
            ttl_expires=_FUTURE,
        ),
        poly_market=_market(Venue.POLYMARKET, "poly-evt", "Poly title"),
        kalshi_market=_market(Venue.KALSHI, "kalshi-evt", "Kalshi title"),
        buy_venue=Venue.POLYMARKET,
        sell_venue=Venue.KALSHI,
        cost_per_contract=Decimal("0.92"),
        gross_profit=Decimal("0.08"),
        net_profit=Decimal("0.05"),
        net_spread_pct=Decimal("0.021"),
        max_size=Decimal("75"),
        depth_risk=False,
        detected_at=_NOW,
    )


def test_build_arb_opp_dict_uses_match_confidence() -> None:
    """Arb watcher should pass match confidence into auto-exec."""
    opp_dict = _build_arb_opp_dict(_opp())

    assert opp_dict["confidence"] == 0.82
    assert opp_dict["spread_pct"] == 0.021
    assert opp_dict["arb_id"] == "poly-evt_kalshi-evt"
