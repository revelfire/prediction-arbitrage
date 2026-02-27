"""Execution ticket generator for arbitrage opportunities.

Converts detected arb opportunities into structured execution tickets
for human operator review and approval.
"""

from decimal import Decimal

import structlog

from arb_scanner.models.arbitrage import ArbOpportunity, ExecutionTicket
from arb_scanner.models.market import Market, Venue

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module=__name__)


def _market_url(market: Market) -> str:
    """Build a venue URL for a market from its raw data.

    Args:
        market: The market with raw venue data.

    Returns:
        URL string, or empty string if slug/ticker unavailable.
    """
    if market.venue == Venue.POLYMARKET:
        slug = str(market.raw_data.get("slug", ""))
        return f"https://polymarket.com/event/{slug}" if slug else ""
    ticker = str(market.raw_data.get("event_ticker", market.event_id))
    return f"https://kalshi.com/markets/{ticker}" if ticker else ""


def _build_leg(
    venue: Venue,
    title: str,
    side: str,
    price: Decimal,
    size: Decimal,
    market_url: str = "",
) -> dict[str, object]:
    """Build a single leg dictionary for an execution ticket.

    Args:
        venue: The venue for this leg.
        title: Market title on this venue.
        side: "YES" or "NO".
        price: The price per contract.
        size: The max tradeable size in USD.
        market_url: Direct link to the market on the venue.

    Returns:
        A dictionary with venue, title, side, price, size, and market_url.
    """
    leg: dict[str, object] = {
        "venue": venue.value,
        "title": title,
        "side": side,
        "price": price,
        "size": size,
    }
    if market_url:
        leg["market_url"] = market_url
    return leg


def _get_prices(
    opp: ArbOpportunity,
) -> tuple[Decimal, Decimal]:
    """Extract the buy-YES and buy-NO prices for the chosen direction.

    Args:
        opp: The arb opportunity with buy/sell venue info.

    Returns:
        Tuple of (yes_price on buy venue, no_price on sell venue).
    """
    if opp.buy_venue == Venue.POLYMARKET:
        return opp.poly_market.yes_ask, opp.kalshi_market.no_ask
    return opp.kalshi_market.yes_ask, opp.poly_market.no_ask


def generate_ticket(
    opp: ArbOpportunity,
    *,
    min_expected_profit_usd: Decimal = Decimal("1.00"),
) -> ExecutionTicket | None:
    """Generate an execution ticket from an arb opportunity.

    Creates a two-leg ticket: leg_1 buys YES on the buy venue,
    leg_2 buys NO on the sell venue. Returns None if expected
    profit is below the minimum threshold.

    Args:
        opp: The arb opportunity to convert into a ticket.
        min_expected_profit_usd: Minimum expected profit to create ticket.

    Returns:
        An ExecutionTicket with pending status, or None if unprofitable.
    """
    expected_profit = opp.net_profit * opp.max_size
    if expected_profit < min_expected_profit_usd:
        logger.debug("ticket_skipped_below_min_profit", arb_id=opp.id)
        return None
    yes_price, no_price = _get_prices(opp)
    buy_market = opp.poly_market if opp.buy_venue == Venue.POLYMARKET else opp.kalshi_market
    sell_market = opp.kalshi_market if opp.buy_venue == Venue.POLYMARKET else opp.poly_market
    leg_1 = _build_leg(
        opp.buy_venue,
        buy_market.title,
        "YES",
        yes_price,
        opp.max_size,
        _market_url(buy_market),
    )
    leg_2 = _build_leg(
        opp.sell_venue,
        sell_market.title,
        "NO",
        no_price,
        opp.max_size,
        _market_url(sell_market),
    )
    ticket = ExecutionTicket(
        arb_id=opp.id,
        leg_1=leg_1,
        leg_2=leg_2,
        expected_cost=opp.cost_per_contract * opp.max_size,
        expected_profit=expected_profit,
        status="pending",
    )
    logger.info(
        "ticket_generated",
        arb_id=opp.id,
        expected_profit=str(ticket.expected_profit),
    )
    return ticket
