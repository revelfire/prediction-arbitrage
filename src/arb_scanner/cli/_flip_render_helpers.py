"""Render helpers and DB fetchers for flip-history and flip-stats commands."""

from __future__ import annotations

import sys
from typing import Any


async def fetch_history(
    config: Any,
    limit: int,
    category: str | None,
    category_type: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch flippening history from the database.

    Args:
        config: Application settings.
        limit: Max rows.
        category: Optional category filter.
        category_type: Optional category type filter.

    Returns:
        List of history records.
    """
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.flippening_repository import FlippeningRepository

    async with Database(config.storage.database_url) as db:
        repo = FlippeningRepository(db.pool)
        return await repo.get_history(
            limit=limit,
            category=category,
            category_type=category_type,
        )


async def fetch_stats(
    config: Any,
    category: str | None,
    category_type: str | None,
    since: Any,
) -> list[dict[str, Any]]:
    """Fetch flippening stats from the database.

    Args:
        config: Application settings.
        category: Optional category filter.
        category_type: Optional category type filter.
        since: Optional start datetime.

    Returns:
        Stats dictionary.
    """
    from arb_scanner.storage.db import Database
    from arb_scanner.storage.flippening_repository import FlippeningRepository

    async with Database(config.storage.database_url) as db:
        repo = FlippeningRepository(db.pool)
        return await repo.get_stats(
            category=category,
            category_type=category_type,
            since=since,
        )


def render_history_table(rows: list[dict[str, Any]]) -> None:
    """Render history as a text table.

    Args:
        rows: History records.
    """
    if not rows:
        sys.stdout.write("No flippening history found.\n")
        return
    header = (
        f"{'Category':<10} {'Type':<8} {'Side':<4} {'Entry':>7} {'Exit':>7} {'P&L':>8} {'Hold':>6}"
    )
    sys.stdout.write(header + "\n")
    sys.stdout.write("-" * len(header) + "\n")
    for row in rows:
        cat = str(row.get("category", "") or row.get("sport", ""))[:10]
        cat_type = str(row.get("category_type", "sport"))[:8]
        side = str(row.get("side", ""))[:4]
        entry = f"{float(row.get('entry_price', 0)):.2f}"
        exit_p = f"{float(row.get('exit_price', 0)):.2f}"
        pnl = f"{float(row.get('realized_pnl', 0)):+.2f}"
        hold = f"{float(row.get('hold_minutes', 0)):.0f}m"
        sys.stdout.write(
            f"{cat:<10} {cat_type:<8} {side:<4} {entry:>7} {exit_p:>7} {pnl:>8} {hold:>6}\n",
        )


def render_stats(rows: list[dict[str, Any]]) -> None:
    """Render stats summary.

    Args:
        rows: List of per-category stats dictionaries.
    """
    if not rows:
        sys.stdout.write("No flippening stats found.\n")
        return
    sys.stdout.write("Flippening Stats\n")
    sys.stdout.write("=" * 40 + "\n")
    for row in rows:
        cat = row.get("category", "") or row.get("sport", "all")
        cat_type = row.get("category_type", "sport")
        sys.stdout.write(f"\n  Category: {cat} ({cat_type})\n")
        sys.stdout.write(f"  Signals:  {row.get('total_signals', 0)}\n")
        win_rate = float(row.get("win_rate_pct", 0))
        sys.stdout.write(f"  Win rate: {win_rate:.1f}%\n")
        avg_pnl = row.get("avg_pnl", 0)
        sys.stdout.write(f"  Avg P&L:  {float(avg_pnl):+.4f}\n")
        avg_hold = row.get("avg_hold_minutes", 0)
        sys.stdout.write(f"  Avg hold: {float(avg_hold):.0f} min\n")
