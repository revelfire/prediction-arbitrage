"""API routes for trade history and backtesting dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile

from arb_scanner.api.deps import get_backtest_repo
from arb_scanner.backtesting.csv_importer import parse_csv_bytes

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="api.backtesting",
)
router = APIRouter(prefix="/api/backtesting", tags=["backtesting"])


@router.post("/import")
async def import_trades(
    file: UploadFile,
    repo: Any = Depends(get_backtest_repo),
) -> dict[str, Any]:
    """Import trades from a Polymarket CSV upload.

    Args:
        file: Uploaded CSV file.
        repo: Backtesting repository.

    Returns:
        Import result with inserted/duplicate counts.
    """
    try:
        content = await file.read()
        trades = parse_csv_bytes(content)
        result = await repo.import_trades(trades)
        out: dict[str, Any] = result.model_dump()
        return out
    except Exception as exc:
        logger.error("import_failed", error=str(exc))
        raise HTTPException(400, f"Import failed: {exc}") from exc


@router.get("/trades")
async def get_trades(
    market_name: str | None = None,
    action: str | None = None,
    limit: int = 200,
    repo: Any = Depends(get_backtest_repo),
) -> list[dict[str, Any]]:
    """Fetch imported trades with optional filters.

    Args:
        market_name: Filter by market name substring.
        action: Filter by trade action.
        limit: Maximum rows.
        repo: Backtesting repository.

    Returns:
        List of trade dicts.
    """
    try:
        result: list[dict[str, Any]] = await repo.get_trades(
            market_name=market_name,
            action=action,
            limit=limit,
        )
        return result
    except Exception as exc:
        logger.error("trades_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/positions")
async def get_positions(
    status: str | None = None,
    repo: Any = Depends(get_backtest_repo),
) -> list[dict[str, Any]]:
    """Fetch trade positions with optional status filter.

    Args:
        status: Position status filter (open/closed/resolved).
        repo: Backtesting repository.

    Returns:
        List of position dicts.
    """
    try:
        result: list[dict[str, Any]] = await repo.get_positions(
            status=status,
        )
        return result
    except Exception as exc:
        logger.error("positions_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/portfolio")
async def get_portfolio(
    repo: Any = Depends(get_backtest_repo),
) -> dict[str, Any]:
    """Fetch aggregate portfolio metrics.

    Args:
        repo: Backtesting repository.

    Returns:
        Portfolio summary dict.
    """
    try:
        summary: dict[str, Any] = await repo.get_portfolio_summary()
        return _serialize_decimals(summary)
    except Exception as exc:
        logger.error("portfolio_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/daily-pnl")
async def get_daily_pnl(
    since: str | None = None,
    repo: Any = Depends(get_backtest_repo),
) -> list[dict[str, Any]]:
    """Fetch daily realized P&L for chart rendering.

    Args:
        since: ISO date string to filter from.
        repo: Backtesting repository.

    Returns:
        List of {trade_date, daily_pnl} dicts.
    """
    try:
        since_dt = datetime.fromisoformat(since) if since else None
        rows: list[dict[str, Any]] = await repo.get_daily_pnl(
            since=since_dt,
        )
        return [_serialize_decimals(r) for r in rows]
    except Exception as exc:
        logger.error("daily_pnl_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/signal-comparison")
async def get_signal_comparison(
    repo: Any = Depends(get_backtest_repo),
) -> dict[str, Any]:
    """Fetch signal alignment breakdown from category performance.

    Args:
        repo: Backtesting repository.

    Returns:
        Dict with alignment counts and P&L.
    """
    try:
        cats = await repo.get_category_performance()
        aligned_count = 0
        contrary_count = 0
        no_signal_count = 0
        for c in cats:
            tc = c.get("trade_count", 0)
            rate = c.get("signal_alignment_rate", 0) or 0
            aligned_count += int(tc * float(rate))
            contrary_count += int(tc * (1 - float(rate)) * 0.5)
            no_signal_count += tc - aligned_count - contrary_count
        if not cats:
            positions = await repo.get_positions()
            no_signal_count = len(positions)
        return {
            "aligned": {"count": max(aligned_count, 0), "total_pnl": 0},
            "contrary": {"count": max(contrary_count, 0), "total_pnl": 0},
            "no_signal": {"count": max(no_signal_count, 0), "total_pnl": 0},
        }
    except Exception as exc:
        logger.error("signal_comparison_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/category-performance")
async def get_category_performance(
    repo: Any = Depends(get_backtest_repo),
) -> list[dict[str, Any]]:
    """Fetch per-category performance metrics.

    Args:
        repo: Backtesting repository.

    Returns:
        List of category performance dicts.
    """
    try:
        rows: list[dict[str, Any]] = await repo.get_category_performance()
        return [_serialize_decimals(r) for r in rows]
    except Exception as exc:
        logger.error("category_perf_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


@router.get("/optimal-params")
async def get_optimal_params(
    category: str | None = None,
    repo: Any = Depends(get_backtest_repo),
) -> list[dict[str, Any]]:
    """Fetch optimal parameter snapshots.

    Args:
        category: Optional category filter.
        repo: Backtesting repository.

    Returns:
        List of optimal param dicts.
    """
    try:
        rows: list[dict[str, Any]] = await repo.get_optimal_params(
            category=category,
        )
        return [_serialize_decimals(r) for r in rows]
    except Exception as exc:
        logger.error("optimal_params_fetch_failed", error=str(exc))
        raise HTTPException(503, "Database unavailable") from exc


def _serialize_decimals(d: dict[str, Any]) -> dict[str, Any]:
    """Convert Decimal and date values to JSON-safe types.

    Args:
        d: Dict potentially containing Decimal/date values.

    Returns:
        Dict with float/str values.
    """
    from datetime import date
    from decimal import Decimal

    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out
