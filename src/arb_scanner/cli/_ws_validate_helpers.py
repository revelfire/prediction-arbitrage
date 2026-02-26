"""Helpers for the flip-ws-validate CLI command."""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from typing import Any

import structlog

from arb_scanner.flippening.ws_telemetry import WsTelemetry, classify_ws_message
from arb_scanner.models.config import FlippeningConfig, Settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="cli.ws_validate",
)


async def _discover_token_ids(settings: Settings | None) -> list[str]:
    """Auto-discover token IDs from sports markets."""
    if settings is None:
        return []
    try:
        from arb_scanner.flippening.sports_filter import classify_sports_markets
        from arb_scanner.ingestion.polymarket import PolymarketClient

        async with PolymarketClient(settings.venues.polymarket) as poly:
            markets = await poly.fetch_markets()
        sports, _ = classify_sports_markets(
            markets,
            settings.flippening.sports,
            settings.flippening,
        )
        return [sm.token_id for sm in sports[:20] if sm.token_id]
    except Exception as exc:
        logger.warning("token_auto_discover_failed", error=str(exc))
        return []


async def run_ws_validate(
    config: FlippeningConfig,
    token_ids: list[str] | None,
    count: int,
    timeout: int,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Connect to WS, capture messages, and produce a report."""
    if not token_ids:
        token_ids = await _discover_token_ids(settings)
        if token_ids:
            logger.info("auto_discovered_tokens", count=len(token_ids))

    telemetry = WsTelemetry()
    raw_messages: list[str] = []
    type_counts: Counter[str] = Counter()
    key_counts: Counter[str] = Counter()
    samples: dict[str, str] = {}

    try:
        import websockets

        ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        async with asyncio.timeout(timeout):
            async with websockets.connect(ws_url) as ws:
                if token_ids:
                    for tid in token_ids:
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "subscribe",
                                    "channel": "market",
                                    "assets_id": tid,
                                }
                            )
                        )

                captured = 0
                async for raw_msg in ws:
                    raw_str = (
                        raw_msg
                        if isinstance(raw_msg, str)
                        else raw_msg.decode("utf-8", errors="replace")
                    )
                    raw_messages.append(raw_str)

                    try:
                        data = json.loads(raw_str)
                    except json.JSONDecodeError:
                        type_counts["non_json"] += 1
                        if "non_json" not in samples:
                            samples["non_json"] = raw_str[:500]
                        captured += 1
                        if captured >= count:
                            break
                        continue

                    if isinstance(data, dict):
                        telemetry.record_schema(frozenset(data.keys()))
                        for k in data:
                            key_counts[k] += 1
                        msg_type = classify_ws_message(data)
                    else:
                        msg_type = "non_dict"

                    type_counts[msg_type] += 1
                    if msg_type not in samples:
                        samples[msg_type] = raw_str[:500]

                    captured += 1
                    if captured >= count:
                        break

    except ImportError:
        return {"error": "websockets package not installed"}
    except TimeoutError:
        pass
    except Exception as exc:
        logger.warning("ws_validate_error", error=str(exc))

    total = sum(type_counts.values())
    type_dist = {
        t: {"count": c, "pct": round(c / total * 100, 1) if total else 0}
        for t, c in type_counts.most_common()
    }
    key_freq = {
        k: {"count": c, "pct": round(c / total * 100, 1) if total else 0}
        for k, c in key_counts.most_common(20)
    }

    return {
        "total_messages": total,
        "type_distribution": type_dist,
        "key_frequency": key_freq,
        "schema_match_rate": round(telemetry.schema_match_rate, 4),
        "unique_schemas": len(telemetry.known_schemas),
        "samples": samples,
        "raw_messages": raw_messages,
    }


def render_ws_validate_table(report: dict[str, Any]) -> None:
    """Render a text report of WS validation results.

    Args:
        report: Report dict from run_ws_validate.
    """
    if "error" in report:
        sys.stdout.write(f"Error: {report['error']}\n")
        return

    sys.stdout.write(f"\nWS Validation Report ({report['total_messages']} messages)\n")
    sys.stdout.write("=" * 50 + "\n")

    sys.stdout.write("\nMessage Type Distribution:\n")
    sys.stdout.write(f"  {'Type':<20} {'Count':>6} {'Pct':>6}\n")
    sys.stdout.write("  " + "-" * 34 + "\n")
    for t, info in report.get("type_distribution", {}).items():
        sys.stdout.write(f"  {t:<20} {info['count']:>6} {info['pct']:>5.1f}%\n")

    sys.stdout.write("\nTop-Level Key Frequency:\n")
    sys.stdout.write(f"  {'Key':<25} {'Count':>6} {'Pct':>6}\n")
    sys.stdout.write("  " + "-" * 39 + "\n")
    for k, info in report.get("key_frequency", {}).items():
        sys.stdout.write(f"  {k:<25} {info['count']:>6} {info['pct']:>5.1f}%\n")

    sys.stdout.write(f"\nSchema match rate: {report['schema_match_rate']:.1%}\n")
    sys.stdout.write(f"Unique schemas seen: {report['unique_schemas']}\n")

    sys.stdout.write("\nSample Messages (per type):\n")
    for msg_type, sample in report.get("samples", {}).items():
        sys.stdout.write(f"\n  [{msg_type}]\n  {sample[:200]}\n")


def save_jsonl(raw_messages: list[str], path: str) -> int:
    """Save raw messages as JSONL.

    Args:
        raw_messages: List of raw message strings.
        path: Output file path.

    Returns:
        Number of lines written.
    """
    with open(path, "w") as f:
        for msg in raw_messages:
            f.write(msg + "\n")
    return len(raw_messages)
