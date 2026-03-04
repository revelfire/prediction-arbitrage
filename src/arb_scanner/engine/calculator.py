"""Arbitrage spread and fee calculation engine.

Calculates cross-venue arbitrage opportunities between Polymarket and Kalshi
by comparing prices after venue-specific fees.
"""

from datetime import datetime, timezone
from decimal import Decimal

import structlog

from arb_scanner.models.arbitrage import ArbOpportunity
from arb_scanner.models.config import ArbThresholds, FeesConfig, FeeSchedule
from arb_scanner.models.market import Market, Venue
from arb_scanner.models.matching import MatchResult

_ONE = Decimal("1.00")
_ZERO = Decimal("0.00")
_DAYS_PER_YEAR = Decimal("365")

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module=__name__)


def _compute_fee(price_paid: Decimal, schedule: FeeSchedule) -> Decimal:
    """Compute the taker fee for a single leg of a trade.

    Args:
        price_paid: The price paid for the contract (0 to 1).
        schedule: The venue's fee schedule from config.

    Returns:
        The fee amount as a Decimal.
    """
    if schedule.fee_model == "on_winnings":
        net_winnings = _ONE - price_paid
        return schedule.taker_fee_pct * net_winnings
    # per_contract model
    fee = schedule.taker_fee_pct
    if schedule.fee_cap is not None:
        fee = min(fee, schedule.fee_cap)
    return fee


def _days_to_expiry(expiry: datetime | None) -> int | None:
    """Calculate days until expiry from now.

    Args:
        expiry: The expiry datetime, or None if unknown.

    Returns:
        Days until expiry as int, or None if expiry is unknown.
    """
    if expiry is None:
        return None
    now = datetime.now(tz=timezone.utc)
    delta = expiry - now
    return max(delta.days, 1)


def _evaluate_direction(
    buy_yes_price: Decimal,
    buy_no_price: Decimal,
    buy_yes_schedule: FeeSchedule,
    buy_no_schedule: FeeSchedule,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Evaluate arb metrics for one direction (buy YES on A, buy NO on B).

    Args:
        buy_yes_price: Ask price for YES on venue A.
        buy_no_price: Ask price for NO on venue B.
        buy_yes_schedule: Fee schedule for the YES venue.
        buy_no_schedule: Fee schedule for the NO venue.

    Returns:
        Tuple of (cost_per_contract, gross_profit, fee_yes, fee_no).
    """
    cost = buy_yes_price + buy_no_price
    gross = _ONE - cost
    fee_yes = _compute_fee(buy_yes_price, buy_yes_schedule)
    fee_no = _compute_fee(buy_no_price, buy_no_schedule)
    return cost, gross, fee_yes, fee_no


def _liquidity_size(
    poly_market: Market,
    kalshi_market: Market,
    buy_venue: Venue,
) -> Decimal:
    """Estimate max tradeable size from 24h volume as a liquidity proxy.

    Args:
        poly_market: The Polymarket market.
        kalshi_market: The Kalshi market.
        buy_venue: Which venue is the YES-buy side.

    Returns:
        Minimum of the two venues' 24h volumes as a USD size estimate.
    """
    if buy_venue == Venue.POLYMARKET:
        return min(poly_market.volume_24h, kalshi_market.volume_24h)
    return min(kalshi_market.volume_24h, poly_market.volume_24h)


def _build_opportunity(
    poly_market: Market,
    kalshi_market: Market,
    match: MatchResult,
    buy_venue: Venue,
    sell_venue: Venue,
    cost: Decimal,
    gross: Decimal,
    net: Decimal,
    max_size: Decimal,
    thresholds: ArbThresholds,
) -> ArbOpportunity | None:
    """Build an ArbOpportunity if it passes threshold filters.

    Args:
        poly_market: The Polymarket market.
        kalshi_market: The Kalshi market.
        match: The match result linking the two markets.
        buy_venue: Venue where YES is bought.
        sell_venue: Venue where NO is bought (effectively selling YES).
        cost: Total cost per contract.
        gross: Gross profit before fees.
        net: Net profit after fees.
        max_size: Estimated max size in USD.
        thresholds: Arb detection thresholds.

    Returns:
        An ArbOpportunity if profitable and above thresholds, else None.
    """
    if net <= _ZERO:
        return None
    if cost <= _ZERO or cost < thresholds.min_cost_per_contract:
        return None
    spread_pct = net / cost
    if spread_pct < thresholds.min_net_spread_pct:
        return None
    if spread_pct > thresholds.max_net_spread_pct:
        return None
    if max_size < thresholds.min_size_usd:
        return None
    expiry = poly_market.expiry or kalshi_market.expiry
    days = _days_to_expiry(expiry)
    ann_return: Decimal | None = None
    if days is not None:
        ann_return = spread_pct * (_DAYS_PER_YEAR / Decimal(days))
    depth_risk = max_size < thresholds.thin_liquidity_threshold
    return ArbOpportunity(
        match=match,
        poly_market=poly_market,
        kalshi_market=kalshi_market,
        buy_venue=buy_venue,
        sell_venue=sell_venue,
        cost_per_contract=cost,
        gross_profit=gross,
        net_profit=net,
        net_spread_pct=spread_pct,
        max_size=max_size,
        annualized_return=ann_return,
        depth_risk=depth_risk,
        detected_at=datetime.now(tz=timezone.utc),
    )


def calculate_arb(
    poly_market: Market,
    kalshi_market: Market,
    match: MatchResult,
    fees: FeesConfig,
    thresholds: ArbThresholds,
) -> ArbOpportunity | None:
    """Calculate arb opportunity for a matched pair.

    Evaluates both directions (buy YES on Poly + NO on Kalshi, and vice versa)
    and returns the more profitable direction, or None if no profitable arb.

    Args:
        poly_market: The Polymarket market.
        kalshi_market: The Kalshi market.
        match: The match result linking the two markets.
        fees: Fee configuration for both venues.
        thresholds: Arb detection thresholds.

    Returns:
        An ArbOpportunity if a profitable arb exists, else None.
    """
    if not match.safe_to_arb:
        logger.info("match_not_safe", poly=poly_market.event_id, kalshi=kalshi_market.event_id)
        return None

    # Skip markets where any ask price is below floor (missing CLOB data)
    floor = thresholds.min_ask_price
    poly_has_prices = poly_market.yes_ask >= floor and poly_market.no_ask >= floor
    kalshi_has_prices = kalshi_market.yes_ask >= floor and kalshi_market.no_ask >= floor
    if not poly_has_prices or not kalshi_has_prices:
        logger.debug(
            "phantom_price_skip",
            poly=poly_market.event_id,
            kalshi=kalshi_market.event_id,
            poly_yes=str(poly_market.yes_ask),
            kalshi_yes=str(kalshi_market.yes_ask),
        )
        return None

    # Direction A: buy YES on Poly, buy NO on Kalshi
    cost_a, gross_a, fee_a_yes, fee_a_no = _evaluate_direction(
        poly_market.yes_ask, kalshi_market.no_ask, fees.polymarket, fees.kalshi
    )
    net_a = gross_a - fee_a_yes - fee_a_no

    # Direction B: buy YES on Kalshi, buy NO on Poly
    cost_b, gross_b, fee_b_yes, fee_b_no = _evaluate_direction(
        kalshi_market.yes_ask, poly_market.no_ask, fees.kalshi, fees.polymarket
    )
    net_b = gross_b - fee_b_yes - fee_b_no

    # Pick the better direction
    if net_a >= net_b:
        buy_v, sell_v, cost, gross, net = (
            Venue.POLYMARKET,
            Venue.KALSHI,
            cost_a,
            gross_a,
            net_a,
        )
    else:
        buy_v, sell_v, cost, gross, net = (
            Venue.KALSHI,
            Venue.POLYMARKET,
            cost_b,
            gross_b,
            net_b,
        )

    max_size = _liquidity_size(poly_market, kalshi_market, buy_v)
    opp = _build_opportunity(
        poly_market,
        kalshi_market,
        match,
        buy_v,
        sell_v,
        cost,
        gross,
        net,
        max_size,
        thresholds,
    )
    if opp is not None:
        logger.info(
            "arb_detected",
            buy_venue=buy_v.value,
            sell_venue=sell_v.value,
            net_spread_pct=str(opp.net_spread_pct),
            net_profit=str(opp.net_profit),
        )
    return opp


def calculate_arbs(
    matched_pairs: list[tuple[Market, Market, MatchResult]],
    fees: FeesConfig,
    thresholds: ArbThresholds,
) -> list[ArbOpportunity]:
    """Calculate arb opportunities for all matched pairs.

    Filters to profitable opportunities only.

    Args:
        matched_pairs: List of (poly_market, kalshi_market, match) tuples.
        fees: Fee configuration for both venues.
        thresholds: Arb detection thresholds.

    Returns:
        List of profitable ArbOpportunity objects.
    """
    results: list[ArbOpportunity] = []
    for poly_market, kalshi_market, match in matched_pairs:
        opp = calculate_arb(poly_market, kalshi_market, match, fees, thresholds)
        if opp is not None:
            results.append(opp)
    logger.info("arb_scan_complete", total_pairs=len(matched_pairs), arbs_found=len(results))
    return results
