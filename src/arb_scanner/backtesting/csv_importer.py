"""CSV importer for Polymarket trade history exports."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from arb_scanner.models.backtesting import ImportedTrade, TradeAction


def parse_csv(path: Path) -> list[ImportedTrade]:
    """Parse a Polymarket CSV export file into ImportedTrade models.

    Args:
        path: Path to the CSV file.

    Returns:
        List of validated ImportedTrade objects.

    Raises:
        ValueError: If any rows fail validation.
        FileNotFoundError: If the file does not exist.
    """
    content = path.read_bytes()
    return parse_csv_bytes(content)


def parse_csv_bytes(content: bytes) -> list[ImportedTrade]:
    """Parse raw CSV bytes into ImportedTrade models.

    Args:
        content: Raw CSV file content (handles BOM).

    Returns:
        List of validated ImportedTrade objects.

    Raises:
        ValueError: If any rows fail validation.
    """
    text = content.decode("utf-8-sig").strip()
    if not text:
        return []

    reader = csv.DictReader(io.StringIO(text))
    trades: list[ImportedTrade] = []
    errors: list[str] = []

    for i, row in enumerate(reader, start=2):
        try:
            trade = _parse_row(row)
            trades.append(trade)
        except (ValueError, KeyError, InvalidOperation) as exc:
            errors.append(f"Row {i}: {exc}")

    if errors:
        raise ValueError(f"{len(errors)} row(s) failed validation:\n" + "\n".join(errors))

    return trades


def _parse_row(row: dict[str, str]) -> ImportedTrade:
    """Convert a single CSV row dict into an ImportedTrade."""
    action_raw = row["action"].strip()
    try:
        action = TradeAction(action_raw)
    except ValueError:
        raise ValueError(f"unknown action '{action_raw}'") from None

    ts_raw = row["timestamp"].strip()
    try:
        ts = datetime.fromtimestamp(int(ts_raw), tz=UTC)
    except (ValueError, OSError):
        raise ValueError(f"invalid timestamp '{ts_raw}'") from None

    return ImportedTrade(
        market_name=row["marketName"].strip(),
        action=action,
        usdc_amount=Decimal(row["usdcAmount"].strip()),
        token_amount=Decimal(row["tokenAmount"].strip()),
        token_name=row["tokenName"].strip(),
        timestamp=ts,
        tx_hash=row["hash"].strip(),
    )
