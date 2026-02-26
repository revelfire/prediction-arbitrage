"""Async helper and renderer for the flip-discover CLI command."""

from __future__ import annotations

import sys
from typing import Any

from arb_scanner.flippening.market_classifier import classify_markets
from arb_scanner.ingestion.polymarket import PolymarketClient
from arb_scanner.models.config import CategoryConfig


async def run_discover(
    config: Any,
    categories: dict[str, CategoryConfig],
) -> dict[str, Any]:
    """Fetch markets from Polymarket and run category classification.

    Args:
        config: Application settings.
        categories: Category configs keyed by ID.

    Returns:
        Dict with summary stats, matched market list, and unclassified count.
    """
    async with PolymarketClient(config.venues.polymarket) as poly:
        markets = await poly.fetch_markets()

    category_markets, health = classify_markets(markets, categories, config.flippening)

    matched = [
        {
            "event_id": sm.market.event_id[:12],
            "title": str(sm.market.raw_data.get("groupItemTitle", sm.market.title)),
            "category": sm.category,
            "category_type": sm.category_type,
            "classification_method": sm.classification_method,
            "token_id": sm.token_id,
        }
        for sm in category_markets
    ]

    return {
        "total_scanned": health.total_scanned,
        "markets_found": health.markets_found,
        "hit_rate": round(health.hit_rate, 4),
        "by_category": health.by_category,
        "by_category_type": health.by_category_type,
        "overrides_applied": health.overrides_applied,
        "exclusions_applied": health.exclusions_applied,
        "unclassified_candidates": health.unclassified_candidates,
        "unclassified_sample": health.unclassified_sample,
        "matched": matched,
    }


def render_discover_table(result: dict[str, Any], *, verbose: bool) -> None:
    """Render discovery results as a human-readable summary table.

    Prints summary stats and, when ``verbose`` is True, a per-market table
    with event ID (first 12 chars), category, type, method, and title.

    Args:
        result: Discovery result dict produced by :func:`run_discover`.
        verbose: When True, print each matched market row.
    """
    sys.stdout.write("Market Discovery\n")
    sys.stdout.write("=" * 40 + "\n")
    sys.stdout.write(f"  Total scanned:         {result['total_scanned']}\n")
    sys.stdout.write(f"  Markets found:         {result['markets_found']}\n")
    sys.stdout.write(f"  Hit rate:              {result['hit_rate']:.2%}\n")
    sys.stdout.write(f"  Overrides applied:     {result['overrides_applied']}\n")
    sys.stdout.write(f"  Exclusions applied:    {result['exclusions_applied']}\n")
    sys.stdout.write(f"  Unclassified cands:    {result['unclassified_candidates']}\n")
    sys.stdout.write("\nBy category:\n")
    for cat, count in sorted(result.get("by_category", {}).items()):
        sys.stdout.write(f"  {cat:<10} {count}\n")
    if not result.get("by_category"):
        sys.stdout.write("  (none)\n")
    sys.stdout.write("\nBy category type:\n")
    for cat_type, count in sorted(result.get("by_category_type", {}).items()):
        sys.stdout.write(f"  {cat_type:<14} {count}\n")
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
    hdr = f"  {'ID':<14} {'Category':<10} {'Type':<8} {'Method':<16} {'Token':<14} Title"
    sys.stdout.write(hdr + "\n")
    sys.stdout.write("  " + "-" * (len(hdr) - 2) + "\n")
    for m in matched:
        title = str(m["title"])[:50]
        token = str(m.get("token_id", ""))[:14]
        cat = str(m.get("category", ""))[:10]
        cat_type = str(m.get("category_type", ""))[:8]
        sys.stdout.write(
            f"  {m['event_id']:<14} {cat:<10} {cat_type:<8} "
            f"{m['classification_method']:<16} {token:<14} {title}\n"
        )
