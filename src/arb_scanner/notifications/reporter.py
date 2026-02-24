"""Markdown report formatter for arbitrage opportunities."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from arb_scanner.models.analytics import PairSummary, ScanHealthSummary, SpreadSnapshot
from arb_scanner.models.arbitrage import ArbOpportunity, ExecutionTicket
from arb_scanner.models.market import Venue

_HEADER = (
    "| Contract | Buy Venue | Buy Price | Sell Venue | Sell Price "
    "| Net Spread % | Max Size | Confidence | Annualized | Ticket |\n"
    "|----------|-----------|-----------|------------|------------"
    "|--------------|----------|------------|------------|--------|\n"
)


def format_markdown_report(
    opps: list[ArbOpportunity],
    tickets: list[ExecutionTicket] | None = None,
) -> str:
    """Format arbitrage opportunities as a Markdown table.

    Opportunities are sorted by net_spread_pct descending.

    Args:
        opps: List of arbitrage opportunities to format.
        tickets: Optional execution tickets keyed by arb_id.

    Returns:
        A Markdown-formatted report string.
    """
    if not opps:
        return "No arbitrage opportunities found.\n"

    ticket_map = _build_ticket_map(tickets)
    sorted_opps = sorted(opps, key=lambda o: o.net_spread_pct, reverse=True)
    rows = [_format_row(opp, ticket_map) for opp in sorted_opps]
    return f"# Arbitrage Opportunities Report\n\n{_HEADER}{''.join(rows)}"


def _build_ticket_map(
    tickets: list[ExecutionTicket] | None,
) -> dict[str, ExecutionTicket]:
    """Build a lookup from arb_id to ExecutionTicket."""
    if not tickets:
        return {}
    return {t.arb_id: t for t in tickets}


def _format_row(opp: ArbOpportunity, ticket_map: dict[str, ExecutionTicket]) -> str:
    """Format a single opportunity as a Markdown table row.

    Args:
        opp: The arbitrage opportunity.
        ticket_map: Lookup of arb_id to execution ticket.

    Returns:
        A single Markdown table row string.
    """
    buy_venue, buy_price, sell_venue, sell_price = _extract_legs(opp)
    ann = f"{float(opp.annualized_return):.0%}" if opp.annualized_return else "N/A"
    ticket_status = ticket_map.get(opp.id, None)
    status_str = ticket_status.status if ticket_status else "-"
    title = _truncate(opp.poly_market.title, 40)

    return (
        f"| {title} "
        f"| {buy_venue} | ${buy_price:.2f} "
        f"| {sell_venue} | ${sell_price:.2f} "
        f"| {float(opp.net_spread_pct):.2%} "
        f"| ${float(opp.max_size):.0f} "
        f"| {opp.match.match_confidence:.0%} "
        f"| {ann} "
        f"| {status_str} |\n"
    )


def _extract_legs(opp: ArbOpportunity) -> tuple[str, float, str, float]:
    """Extract buy/sell venue names and prices from an opportunity.

    Args:
        opp: The arbitrage opportunity.

    Returns:
        Tuple of (buy_venue_name, buy_price, sell_venue_name, sell_price).
    """
    if opp.buy_venue == Venue.POLYMARKET:
        buy_price = float(opp.poly_market.yes_ask)
        sell_price = float(opp.kalshi_market.no_ask)
    else:
        buy_price = float(opp.kalshi_market.yes_ask)
        sell_price = float(opp.poly_market.no_ask)
    return opp.buy_venue.value, buy_price, opp.sell_venue.value, sell_price


def format_tickets_table(tickets: list[dict[str, Any]]) -> str:
    """Format execution tickets as an ASCII table.

    Args:
        tickets: List of ticket dicts from the repository.

    Returns:
        Formatted table string.
    """
    if not tickets:
        return "No execution tickets found.\n"
    header = f"{'ARB_ID':36} {'STATUS':10} {'COST':>10} {'PROFIT':>10} {'CREATED':19}\n"
    sep = "-" * 89 + "\n"
    lines = [header, sep]
    for t in tickets:
        created = _format_dt(t.get("created_at"))
        lines.append(
            f"{str(t['arb_id']):36} {str(t['status']):10} "
            f"{_fmt_decimal(t.get('expected_cost')):>10} "
            f"{_fmt_decimal(t.get('expected_profit')):>10} {created:19}\n"
        )
    return "".join(lines)


def format_matches_table(matches: list[dict[str, Any]]) -> str:
    """Format match results as an ASCII table for the match-audit command.

    Args:
        matches: List of match result dicts from the repository.

    Returns:
        Formatted table string.
    """
    if not matches:
        return "No match results found.\n"
    header = (
        f"{'POLY_ID':20} {'KALSHI_ID':20} {'CONF':>5} "
        f"{'EQ':>3} {'SAFE':>4} {'REASONING':30} {'EXPIRED':>7}\n"
    )
    sep = "-" * 95 + "\n"
    lines = [header, sep]
    for m in matches:
        expired = _is_expired(m.get("ttl_expires"))
        reasoning = _truncate(str(m.get("reasoning", "")), 30)
        lines.append(
            f"{_truncate(str(m['poly_event_id']), 20):20} "
            f"{_truncate(str(m['kalshi_event_id']), 20):20} "
            f"{m['match_confidence']:5.2f} "
            f"{'Y' if m.get('resolution_equivalent') else 'N':>3} "
            f"{'Y' if m.get('safe_to_arb') else 'N':>4} "
            f"{reasoning:30} {'YES' if expired else 'no':>7}\n"
        )
    return "".join(lines)


def format_spread_history(pair_label: str, snapshots: list[SpreadSnapshot]) -> str:
    """Format spread snapshots as an ASCII table.

    Args:
        pair_label: Human-readable label for the pair (e.g. "abc123 / KALSHI-XYZ").
        snapshots: List of SpreadSnapshot models to render.

    Returns:
        Formatted table string, or "(no data)" if snapshots is empty.
    """
    if not snapshots:
        return "(no data)\n"
    header = (
        f"Spread History: {pair_label}  ({len(snapshots)} data points)\n"
        f"{'DETECTED_AT':19}  {'NET_SPREAD':>10}  {'ANNUALIZED':>10}"
        f"  {'DEPTH_RISK':>10}  {'MAX_SIZE':>10}\n"
    )
    sep = "-" * 69 + "\n"
    lines = [header, sep]
    for s in snapshots:
        ann = (
            f"{float(s.annualized_return) * 100:.1f}%" if s.annualized_return is not None else "N/A"
        )
        lines.append(
            f"{s.detected_at.strftime('%Y-%m-%d %H:%M'):19}"
            f"  {float(s.net_spread_pct) * 100:9.2f}%"
            f"  {ann:>10}"
            f"  {'Yes' if s.depth_risk else 'No':>10}"
            f"  ${float(s.max_size):>9.0f}\n"
        )
    return "".join(lines)


def format_stats_report(
    summaries: list[PairSummary],
    health: list[ScanHealthSummary],
    top_n: int = 10,
) -> str:
    """Format pair summaries and scan health as an ASCII report.

    Args:
        summaries: List of PairSummary models (sorted by peak_spread desc).
        health: List of ScanHealthSummary models.
        top_n: Maximum number of pair summaries to display.

    Returns:
        Formatted report string with two sections.
    """
    parts: list[str] = []
    # Section 1: Top Pairs
    if not summaries:
        parts.append("Top Pairs by Peak Spread\n(no data)\n")
    else:
        parts.append(_format_pairs_section(summaries[:top_n]))
    parts.append("")
    # Section 2: Scanner Health
    if not health:
        parts.append("Scanner Health\n(no data)\n")
    else:
        parts.append(_format_health_section(health))
    return "\n".join(parts)


def _format_pairs_section(summaries: list[PairSummary]) -> str:
    """Format the top pairs section of the stats report."""
    header = (
        f"Top Pairs by Peak Spread\n"
        f"{'POLY_ID':20} {'KALSHI_ID':20} {'PEAK':>7} {'AVG':>7}"
        f" {'DETECTIONS':>10} {'FIRST_SEEN':19} {'LAST_SEEN':19}\n"
    )
    sep = "-" * 108 + "\n"
    lines = [header, sep]
    for s in summaries:
        lines.append(
            f"{_truncate(s.poly_event_id, 20):20}"
            f" {_truncate(s.kalshi_event_id, 20):20}"
            f" {float(s.peak_spread) * 100:6.2f}%"
            f" {float(s.avg_spread) * 100:6.2f}%"
            f" {s.total_detections:>10}"
            f" {s.first_seen.strftime('%Y-%m-%d %H:%M'):19}"
            f" {s.last_seen.strftime('%Y-%m-%d %H:%M'):19}\n"
        )
    return "".join(lines)


def _format_health_section(health: list[ScanHealthSummary]) -> str:
    """Format the scanner health section of the stats report."""
    header = (
        f"Scanner Health\n"
        f"{'HOUR':19} {'SCANS':>6} {'AVG_DURATION':>12}"
        f" {'LLM_CALLS':>10} {'OPPS_FOUND':>10} {'ERRORS':>7}\n"
    )
    sep = "-" * 70 + "\n"
    lines = [header, sep]
    for h in health:
        lines.append(
            f"{h.hour.strftime('%Y-%m-%d %H:%M'):19}"
            f" {h.scan_count:>6}"
            f" {h.avg_duration_s:11.1f}s"
            f" {h.total_llm_calls:>10}"
            f" {h.total_opps:>10}"
            f" {h.total_errors:>7}\n"
        )
    return "".join(lines)


def write_output(text: str) -> None:
    """Write text to stdout without print.

    Args:
        text: Text to write.
    """
    sys.stdout.write(text)


def _truncate(text: str, max_len: int) -> str:
    """Truncate a string to max_len, appending ellipsis if needed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _fmt_decimal(value: Any) -> str:
    """Format a decimal-like value to 2 decimal places."""
    if value is None:
        return "N/A"
    return f"{float(value):.2f}"


def _format_dt(value: Any) -> str:
    """Format a datetime value as ISO short string."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value) if value else "N/A"


def _is_expired(ttl_expires: Any) -> bool:
    """Check if a TTL timestamp is in the past."""
    if not isinstance(ttl_expires, datetime):
        return False
    return ttl_expires < datetime.now(tz=timezone.utc)
