"""Tests for arb watcher opportunity shaping."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from arb_scanner.cli.watch import _build_arb_opp_dict
from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult

_NOW = datetime.now(tz=timezone.utc)
_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _market(
    venue: Venue,
    event_id: str,
    title: str,
    *,
    yes_ask: str,
    no_ask: str,
    volume_24h: str,
) -> Market:
    raw_data: dict[str, object]
    if venue == Venue.POLYMARKET:
        raw_data = {"clobTokenIds": '["poly-token-yes", "poly-token-no"]'}
    else:
        raw_data = {"ticker": f"{event_id}-TICKER"}
    return Market(
        venue=venue,
        event_id=event_id,
        title=title,
        description="Test",
        resolution_criteria="Test",
        yes_bid=max(Decimal(yes_ask) - Decimal("0.02"), Decimal("0.01")),
        yes_ask=Decimal(yes_ask),
        no_bid=max(Decimal(no_ask) - Decimal("0.02"), Decimal("0.01")),
        no_ask=Decimal(no_ask),
        volume_24h=Decimal(volume_24h),
        fees_pct=Decimal("0.02"),
        fee_model="on_winnings",
        last_updated=_NOW - timedelta(seconds=7),
        raw_data=raw_data,
    )


def _opp(
    *, buy_venue: Venue = Venue.POLYMARKET, sell_venue: Venue = Venue.KALSHI
) -> ArbOpportunity:
    return ArbOpportunity(
        id="opp-1",
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
        poly_market=_market(
            Venue.POLYMARKET,
            "poly-evt",
            "Poly title",
            yes_ask="0.52",
            no_ask="0.48",
            volume_24h="150",
        ),
        kalshi_market=_market(
            Venue.KALSHI,
            "kalshi-evt",
            "Kalshi title",
            yes_ask="0.41",
            no_ask="0.59",
            volume_24h="120",
        ),
        buy_venue=buy_venue,
        sell_venue=sell_venue,
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
    assert opp_dict["arb_id"] == "opp-1"
    assert opp_dict["poly_yes_price"] == 0.52
    assert opp_dict["kalshi_yes_price"] == 0.41
    assert opp_dict["poly_depth"] > 0
    assert opp_dict["kalshi_depth"] > 0
    assert opp_dict["price_age_seconds"] >= 7


def test_build_arb_opp_dict_shapes_poly_yes_kalshi_no_legs() -> None:
    """Default arb direction should buy YES on Poly and NO on Kalshi."""
    opp_dict = _build_arb_opp_dict(_opp())

    assert opp_dict["leg_1"] == {
        "venue": "polymarket",
        "action": "buy",
        "side": "yes",
        "price": 0.52,
        "market_id": "poly-evt",
        "token_id": "poly-token-yes",
    }
    assert opp_dict["leg_2"] == {
        "venue": "kalshi",
        "action": "buy",
        "side": "no",
        "price": 0.59,
        "market_id": "kalshi-evt",
        "ticker": "kalshi-evt-TICKER",
    }


def test_build_arb_opp_dict_shapes_kalshi_yes_poly_no_legs() -> None:
    """Reverse arb direction should buy YES on Kalshi and NO on Poly."""
    opp_dict = _build_arb_opp_dict(_opp(buy_venue=Venue.KALSHI, sell_venue=Venue.POLYMARKET))

    assert opp_dict["leg_1"] == {
        "venue": "kalshi",
        "action": "buy",
        "side": "yes",
        "price": 0.41,
        "market_id": "kalshi-evt",
        "ticker": "kalshi-evt-TICKER",
    }
    assert opp_dict["leg_2"] == {
        "venue": "polymarket",
        "action": "buy",
        "side": "no",
        "price": 0.48,
        "market_id": "poly-evt",
        "token_id": "poly-token-no",
    }
