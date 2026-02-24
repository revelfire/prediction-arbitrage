"""Internal helpers for CLI commands (scan rendering, config loading)."""

from __future__ import annotations

import json
import sys
from typing import Any


def load_config_safe(dry_run: bool) -> Any:
    """Load config, providing defaults for dry-run when config file is missing.

    Args:
        dry_run: Whether this is a dry-run invocation.

    Returns:
        Settings object.
    """
    if dry_run:
        return _dry_run_config()
    from arb_scanner.config.loader import load_config

    return load_config()


def _dry_run_config() -> Any:
    """Build a minimal Settings for dry-run mode.

    Returns:
        Settings object with fixture-friendly defaults.
    """
    from decimal import Decimal

    from arb_scanner.models.config import (
        ArbThresholds,
        ClaudeConfig,
        FeeSchedule,
        FeesConfig,
        Settings,
        StorageConfig,
    )

    return Settings(
        storage=StorageConfig(database_url="postgresql://localhost/unused"),
        fees=FeesConfig(
            polymarket=FeeSchedule(taker_fee_pct=Decimal("0.0"), fee_model="on_winnings"),
            kalshi=FeeSchedule(
                taker_fee_pct=Decimal("0.07"),
                fee_model="per_contract",
                fee_cap=Decimal("0.07"),
            ),
        ),
        claude=ClaudeConfig(api_key="dry-run-unused"),
        arb_thresholds=ArbThresholds(min_net_spread_pct=Decimal("0.01")),
    )


def determine_exit_code(result: dict[str, Any]) -> int:
    """Determine the CLI exit code from scan results.

    Args:
        result: Scan result dict.

    Returns:
        0 for success, 2 for partial failure (some markets missing).
    """
    scanned = result.get("markets_scanned", {})
    poly = scanned.get("polymarket", 0)
    kalshi = scanned.get("kalshi", 0)
    if poly == 0 or kalshi == 0:
        return 2
    return 0


def render_output(result: dict[str, Any], fmt: str) -> None:
    """Write scan results to stdout in the requested format.

    Args:
        result: Scan result dict.
        fmt: Output format (json or table).
    """
    if fmt == "table":
        render_table(result)
    else:
        clean = {k: v for k, v in result.items() if not k.startswith("_")}
        sys.stdout.write(json.dumps(clean, indent=2) + "\n")


def render_table(result: dict[str, Any]) -> None:
    """Render scan results as an ASCII table.

    Args:
        result: Scan result dict.
    """
    scanned = result.get("markets_scanned", {})
    opps = result.get("opportunities", [])
    header = (
        f"Scan {result['scan_id'][:8]}... | "
        f"Poly: {scanned.get('polymarket', 0)} | "
        f"Kalshi: {scanned.get('kalshi', 0)} | "
        f"Pairs: {result.get('candidate_pairs', 0)} | "
        f"Opps: {len(opps)}"
    )
    sys.stdout.write(header + "\n")
    if not opps:
        sys.stdout.write("No opportunities found.\n")
        return
    sys.stdout.write(f"{'ID':8} {'Buy':12} {'Sell':12} {'Spread%':>8} {'Size$':>8}\n")
    sys.stdout.write("-" * 52 + "\n")
    for opp in opps:
        sys.stdout.write(
            f"{opp['id'][:8]:8} "
            f"{opp['buy']['venue']:12} "
            f"{opp['sell']['venue']:12} "
            f"{opp['net_spread_pct']:8.2%} "
            f"{opp['max_size_usd']:8.0f}\n"
        )


def format_report_markdown(rows: list[dict[str, Any]]) -> str:
    """Format raw opportunity rows as a simple Markdown table.

    Args:
        rows: Opportunity dicts from the repository.

    Returns:
        Markdown-formatted table string.
    """
    if not rows:
        return "No recent opportunities found.\n"
    hdr = "| ID | Buy | Sell | Net Spread % | Max Size | Detected |\n"
    sep = "|----|-----|------|--------------|----------|----------|\n"
    lines = [f"# Recent Opportunities\n\n{hdr}{sep}"]
    for r in rows:
        lines.append(
            f"| {str(r['id'])[:8]} "
            f"| {r.get('buy_venue', '')} "
            f"| {r.get('sell_venue', '')} "
            f"| {_fmt_pct(r.get('net_spread_pct'))} "
            f"| ${_fmt_dec(r.get('max_size'))} "
            f"| {str(r.get('detected_at', ''))[:19]} |\n"
        )
    return "".join(lines)


def _fmt_pct(value: Any) -> str:
    """Format a value as percentage string."""
    if value is None:
        return "N/A"
    return f"{float(value):.2%}"


def _fmt_dec(value: Any) -> str:
    """Format a decimal-like value to a string."""
    if value is None:
        return "N/A"
    return f"{float(value):.0f}"
