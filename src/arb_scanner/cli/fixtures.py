"""Fixture loading and output formatting for the scan orchestrator."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.scan_log import ScanLog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="cli.fixtures")

_FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


# ------------------------------------------------------------------
# Fixture loading (dry-run)
# ------------------------------------------------------------------


def load_fixture_markets() -> tuple[list[Market], list[Market]]:
    """Load Polymarket and Kalshi markets from test fixture JSON files.

    Returns:
        Tuple of (polymarket_markets, kalshi_markets).
    """
    poly_raw = _read_fixture("polymarket_markets.json")
    kalshi_raw = _read_fixture("kalshi_markets.json")
    poly_items: list[dict[str, Any]] = poly_raw if isinstance(poly_raw, list) else []
    kalshi_data = kalshi_raw.get("markets", []) if isinstance(kalshi_raw, dict) else []
    poly_markets = [m for m in (_parse_poly(r) for r in poly_items) if m is not None]
    kalshi_markets = [m for m in (_parse_kalshi(r) for r in kalshi_data) if m is not None]
    logger.info("fixtures_loaded", polymarket=len(poly_markets), kalshi=len(kalshi_markets))
    return poly_markets, kalshi_markets


def _read_fixture(name: str) -> Any:
    """Read and parse a JSON fixture file.

    Args:
        name: Filename relative to the fixtures directory.

    Returns:
        Parsed JSON data.
    """
    path = _FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_poly(raw: dict[str, Any]) -> Market | None:
    """Parse a fixture Polymarket market dict into a Market.

    Args:
        raw: Raw fixture dict.

    Returns:
        A Market or None on parse failure.
    """
    try:
        prices = json.loads(raw.get("outcomePrices", "[]"))
        yes = Decimal(str(prices[0])) if prices else Decimal("0")
        no = Decimal(str(prices[1])) if len(prices) > 1 else Decimal("0")
        spread = Decimal("0.01")
        return Market(
            venue=Venue.POLYMARKET,
            event_id=str(raw["id"]),
            title=str(raw["question"]),
            description=str(raw.get("description", "")),
            resolution_criteria=str(raw.get("resolution_source", "")),
            yes_bid=max(yes - spread, Decimal("0")),
            yes_ask=min(yes + spread, Decimal("1")),
            no_bid=max(no - spread, Decimal("0")),
            no_ask=min(no + spread, Decimal("1")),
            volume_24h=Decimal(str(raw.get("volume", "0"))),
            expiry=_parse_dt(raw.get("endDate")),
            fees_pct=Decimal("0.02"),
            fee_model="on_winnings",
            last_updated=datetime.now(tz=UTC),
        )
    except Exception:
        return None


def _parse_kalshi(raw: dict[str, Any]) -> Market | None:
    """Parse a fixture Kalshi market dict into a Market.

    Args:
        raw: Raw fixture dict.

    Returns:
        A Market or None on parse failure.
    """
    try:
        yes_bid = Decimal(str(raw.get("yes_bid_dollars", "0")))
        yes_ask = Decimal(str(raw.get("yes_ask_dollars", "0")))
        no_bid = Decimal(str(raw.get("no_bid_dollars", "0")))
        no_ask = Decimal(str(raw.get("no_ask_dollars", "0")))
        return Market(
            venue=Venue.KALSHI,
            event_id=str(raw["ticker"]),
            title=str(raw["title"]),
            description=str(raw.get("subtitle", "")),
            resolution_criteria=str(raw.get("rules_primary", "")),
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            volume_24h=Decimal(str(raw.get("volume_dollars_24h_fp", "0"))),
            expiry=_parse_dt(raw.get("expiration_time")),
            fees_pct=Decimal("0.07"),
            fee_model="per_contract",
            last_updated=datetime.now(tz=UTC),
        )
    except Exception:
        return None


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO datetime string.

    Args:
        value: Raw string value.

    Returns:
        Parsed datetime or None.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------
# Output builders
# ------------------------------------------------------------------


def build_scan_log(
    scan_id: str,
    started_at: datetime,
    completed_at: datetime,
    poly_count: int,
    kalshi_count: int,
    candidate_count: int,
    opp_count: int,
    errors: list[str],
) -> ScanLog:
    """Build a ScanLog record from pipeline results.

    Args:
        scan_id: Unique scan identifier.
        started_at: Scan start time.
        completed_at: Scan end time.
        poly_count: Number of Polymarket markets fetched.
        kalshi_count: Number of Kalshi markets fetched.
        candidate_count: Number of matched candidate pairs.
        opp_count: Number of detected opportunities.
        errors: Accumulated errors.

    Returns:
        A populated ScanLog.
    """
    return ScanLog(
        id=scan_id,
        started_at=started_at,
        completed_at=completed_at,
        poly_markets_fetched=poly_count,
        kalshi_markets_fetched=kalshi_count,
        candidate_pairs=candidate_count,
        llm_evaluations=candidate_count,
        opportunities_found=opp_count,
        errors=errors,
    )


def build_output(
    scan_id: str,
    timestamp: datetime,
    poly_count: int,
    kalshi_count: int,
    candidate_count: int,
    opps: list[ArbOpportunity],
) -> dict[str, Any]:
    """Build the CLI output dict matching the JSON schema.

    Args:
        scan_id: Unique scan identifier.
        timestamp: Scan timestamp.
        poly_count: Number of Polymarket markets.
        kalshi_count: Number of Kalshi markets.
        candidate_count: Number of candidate pairs.
        opps: Detected opportunities.

    Returns:
        Output dict with scan metadata and opportunities.
    """
    return {
        "scan_id": scan_id,
        "timestamp": timestamp.isoformat(),
        "markets_scanned": {
            "polymarket": poly_count,
            "kalshi": kalshi_count,
        },
        "candidate_pairs": candidate_count,
        "opportunities": [_format_opportunity(o) for o in opps],
    }


def _format_opportunity(opp: ArbOpportunity) -> dict[str, Any]:
    """Format a single opportunity for JSON output.

    Args:
        opp: Arb opportunity to format.

    Returns:
        Dict matching the CLI output schema for an opportunity.
    """
    buy_contract = (
        opp.poly_market.title if opp.buy_venue == Venue.POLYMARKET else opp.kalshi_market.title
    )
    sell_contract = (
        opp.kalshi_market.title if opp.sell_venue == Venue.KALSHI else opp.poly_market.title
    )
    buy_price = (
        opp.poly_market.yes_ask if opp.buy_venue == Venue.POLYMARKET else opp.kalshi_market.yes_ask
    )
    sell_price = (
        opp.kalshi_market.no_ask if opp.sell_venue == Venue.KALSHI else opp.poly_market.no_ask
    )
    ann = float(opp.annualized_return) if opp.annualized_return is not None else None
    return {
        "id": opp.id,
        "buy": {
            "venue": opp.buy_venue.value,
            "contract": buy_contract,
            "price": float(buy_price),
        },
        "sell": {
            "venue": opp.sell_venue.value,
            "contract": sell_contract,
            "price": float(sell_price),
        },
        "net_spread_pct": float(opp.net_spread_pct),
        "max_size_usd": float(opp.max_size),
        "match_confidence": opp.match.match_confidence,
        "depth_risk": opp.depth_risk,
        "annualized_return": ann,
    }
