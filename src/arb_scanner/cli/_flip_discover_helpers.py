"""Async helper and renderer for the flip-discover CLI command."""

from __future__ import annotations

import sys
from typing import Any

from arb_scanner.flippening.sports_filter import classify_sports_markets
from arb_scanner.ingestion.polymarket import PolymarketClient


async def run_discover(config: Any, allowed: list[str]) -> dict[str, Any]:
    """Fetch markets from Polymarket and run sports classification.

    Args:
        config: Application settings.
        allowed: Lowercase sport slugs to classify.

    Returns:
        Dict with summary stats, matched market list, and unclassified count.
    """
    async with PolymarketClient(config.venues.polymarket) as poly:
        markets = await poly.fetch_markets()

    sports_markets, health = classify_sports_markets(markets, allowed, config.flippening)

    matched = [
        {
            "event_id": sm.market.event_id[:12],
            "title": str(sm.market.raw_data.get("groupItemTitle", sm.market.title)),
            "sport": sm.sport,
            "classification_method": sm.classification_method,
            "token_id": sm.token_id,
        }
        for sm in sports_markets
    ]

    return {
        "total_scanned": health.total_scanned,
        "sports_found": health.sports_found,
        "hit_rate": round(health.hit_rate, 4),
        "by_sport": health.by_sport,
        "overrides_applied": health.overrides_applied,
        "exclusions_applied": health.exclusions_applied,
        "unclassified_candidates": health.unclassified_candidates,
        "unclassified_sample": health.unclassified_sample,
        "matched": matched,
    }


def render_discover_table(result: dict[str, Any], *, verbose: bool) -> None:
    """Render discovery results as a human-readable summary table.

    Prints summary stats and, when ``verbose`` is True, a per-market table
    with event ID (first 12 chars), sport, classification method, and title.

    Args:
        result: Discovery result dict produced by :func:`run_discover`.
        verbose: When True, print each matched market row.
    """
    sys.stdout.write("Sports Market Discovery\n")
    sys.stdout.write("=" * 40 + "\n")
    sys.stdout.write(f"  Total scanned:         {result['total_scanned']}\n")
    sys.stdout.write(f"  Sports found:          {result['sports_found']}\n")
    sys.stdout.write(f"  Hit rate:              {result['hit_rate']:.2%}\n")
    sys.stdout.write(f"  Overrides applied:     {result['overrides_applied']}\n")
    sys.stdout.write(f"  Exclusions applied:    {result['exclusions_applied']}\n")
    sys.stdout.write(f"  Unclassified cands:    {result['unclassified_candidates']}\n")
    sys.stdout.write("\nBy sport:\n")
    for sport, count in sorted(result["by_sport"].items()):
        sys.stdout.write(f"  {sport:<10} {count}\n")
    if not result["by_sport"]:
        sys.stdout.write("  (none)\n")
    unclassified = result.get("unclassified_sample", [])
    if unclassified:
        sys.stdout.write("\nTop unclassified candidates (review for overrides/exclusions):\n")
        for uc in unclassified:
            title = str(uc.get("title", ""))[:60]
            slug = str(uc.get("slug", ""))[:30]
            sys.stdout.write(f"  {slug:<30} {title}\n")
    if verbose:
        _render_verbose(result["matched"])


def _render_verbose(matched: list[dict[str, Any]]) -> None:
    """Print a per-market table for verbose mode."""
    sys.stdout.write("\nMatched markets:\n")
    hdr = f"  {'ID':<14} {'Sport':<8} {'Method':<16} {'Token':<14} Title"
    sys.stdout.write(hdr + "\n")
    sys.stdout.write("  " + "-" * (len(hdr) - 2) + "\n")
    for m in matched:
        title = str(m["title"])[:50]
        token = str(m.get("token_id", ""))[:14]
        sys.stdout.write(
            f"  {m['event_id']:<14} {m['sport']:<8} {m['classification_method']:<16} {token:<14} {title}\n"
        )
