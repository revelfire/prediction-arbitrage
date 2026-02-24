"""Execution ticket generator for arbitrage opportunities.

Converts detected arb opportunities into structured execution tickets
for human operator review and approval.
"""

from decimal import Decimal

import structlog

from arb_scanner.models.arbitrage import ArbOpportunity, ExecutionTicket
from arb_scanner.models.market import Venue

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module=__name__)


def _build_leg(
    venue: Venue,
    side: str,
    price: Decimal,
    size: Decimal,
) -> dict[str, object]:
    """Build a single leg dictionary for an execution ticket.

    Args:
        venue: The venue for this leg.
        side: "YES" or "NO".
        price: The price per contract.
        size: The max tradeable size in USD.

    Returns:
        A dictionary with venue, side, price, and size.
    """
    return {
        "venue": venue.value,
        "side": side,
        "price": price,
        "size": size,
    }


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


def generate_ticket(opp: ArbOpportunity) -> ExecutionTicket:
    """Generate an execution ticket from an arb opportunity.

    Creates a two-leg ticket: leg_1 buys YES on the buy venue,
    leg_2 buys NO on the sell venue.

    Args:
        opp: The arb opportunity to convert into a ticket.

    Returns:
        An ExecutionTicket with pending status for operator review.
    """
    yes_price, no_price = _get_prices(opp)
    leg_1 = _build_leg(opp.buy_venue, "YES", yes_price, opp.max_size)
    leg_2 = _build_leg(opp.sell_venue, "NO", no_price, opp.max_size)
    ticket = ExecutionTicket(
        arb_id=opp.id,
        leg_1=leg_1,
        leg_2=leg_2,
        expected_cost=opp.cost_per_contract * opp.max_size,
        expected_profit=opp.net_profit * opp.max_size,
        status="pending",
    )
    logger.info(
        "ticket_generated",
        arb_id=opp.id,
        expected_profit=str(ticket.expected_profit),
    )
    return ticket
