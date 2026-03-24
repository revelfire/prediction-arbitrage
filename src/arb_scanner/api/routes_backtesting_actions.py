"""API routes for backtesting action triggers (analyze, report, sweep)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from arb_scanner.api.deps import get_backtest_repo, get_config, get_flip_repo
from arb_scanner.models.config import Settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="api.backtesting_actions",
)
router = APIRouter(prefix="/api/backtesting", tags=["backtesting"])


class SweepRequest(BaseModel):
    """Request body for parameter sweep."""

    category: str
    param: str
    min: float
    max: float
    step: float


@router.post("/analyze")
async def analyze_portfolio(
    category: str | None = None,
    repo: Any = Depends(get_backtest_repo),
) -> dict[str, Any]:
    """Reconstruct positions, compute portfolio and category performance.

    Args:
        category: Optional category filter.
        repo: Backtesting repository.

    Returns:
        Portfolio summary and category performance.
    """
    from arb_scanner.backtesting.performance_tracker import (
        classify_market_category,
        compute_category_performance,
    )
    from arb_scanner.backtesting.portfolio_calculator import calculate_portfolio
    from arb_scanner.backtesting.position_engine import reconstruct_positions
    from arb_scanner.flippening.category_keywords import DEFAULT_SPORT_KEYWORDS
    from arb_scanner.models.backtesting import ImportedTrade, TradeAction

    try:
        rows = await repo.get_trades()
        trades = [ImportedTrade(**{k: v for k, v in r.items() if k != "id"}) for r in rows]
        positions = reconstruct_positions(trades)
        if category:
            positions = [
                p
                for p in positions
                if classify_market_category(p.market_name, DEFAULT_SPORT_KEYWORDS) == category
            ]
        summary = calculate_portfolio(positions)
        cat_perfs = compute_category_performance(positions, [], DEFAULT_SPORT_KEYWORDS)
        for perf in cat_perfs:
            await repo.upsert_category_performance(perf)
        for pos in positions:
            await repo.upsert_position(pos)
        buy_sells = [t for t in trades if t.action in (TradeAction.Buy, TradeAction.Sell)]
        return {
            "portfolio": summary.model_dump(mode="json"),
            "category_performance": [p.model_dump(mode="json") for p in cat_perfs],
            "trade_count": len(buy_sells),
        }
    except Exception as exc:
        logger.error("analyze_failed", error=str(exc))
        raise HTTPException(500, f"Analysis failed: {exc}") from exc


@router.post("/report")
async def run_backtest_report(
    category: str | None = None,
    since: str | None = None,
    until: str | None = None,
    repo: Any = Depends(get_backtest_repo),
    flip_repo: Any = Depends(get_flip_repo),
) -> dict[str, Any]:
    """Run full backtest report with signal comparison.

    Args:
        category: Optional category filter.
        since: ISO date start filter.
        until: ISO date end filter.
        repo: Backtesting repository.
        flip_repo: Flippening repository for signal data.

    Returns:
        Portfolio, signal alignment, and category performance.
    """
    from arb_scanner.backtesting.performance_tracker import (
        classify_market_category,
        compute_category_performance,
    )
    from arb_scanner.backtesting.portfolio_calculator import calculate_portfolio
    from arb_scanner.backtesting.position_engine import reconstruct_positions
    from arb_scanner.backtesting.signal_comparator import (
        aggregate_by_alignment,
        compare_trades_to_signals,
    )
    from arb_scanner.flippening.category_keywords import DEFAULT_SPORT_KEYWORDS
    from arb_scanner.models.backtesting import ImportedTrade, TradeAction

    try:
        since_dt = datetime.fromisoformat(since) if since else None
        until_dt = datetime.fromisoformat(until) if until else None
        rows = await repo.get_trades(since=since_dt, until=until_dt)
        trades = [ImportedTrade(**{k: v for k, v in r.items() if k != "id"}) for r in rows]
        positions = reconstruct_positions(trades)
        if category:
            positions = [
                p
                for p in positions
                if classify_market_category(p.market_name, DEFAULT_SPORT_KEYWORDS) == category
            ]
        summary = calculate_portfolio(positions)
        signals = await flip_repo.get_history(limit=500, category=category)
        buy_sells = [t for t in trades if t.action in (TradeAction.Buy, TradeAction.Sell)]
        comparisons = compare_trades_to_signals(buy_sells, signals)
        alignment_agg = aggregate_by_alignment(comparisons)
        cat_perfs = compute_category_performance(
            positions,
            comparisons,
            DEFAULT_SPORT_KEYWORDS,
        )
        for perf in cat_perfs:
            await repo.upsert_category_performance(perf)
        return {
            "portfolio": summary.model_dump(mode="json"),
            "signal_alignment": alignment_agg,
            "category_performance": [p.model_dump(mode="json") for p in cat_perfs],
        }
    except Exception as exc:
        logger.error("report_failed", error=str(exc))
        raise HTTPException(500, f"Report failed: {exc}") from exc


@router.post("/sweep")
async def run_sweep_endpoint(
    body: SweepRequest,
    config: Settings = Depends(get_config),
    repo: Any = Depends(get_backtest_repo),
) -> dict[str, Any]:
    """Run a parameter sweep and persist the best result.

    Args:
        body: Sweep parameters (category, param, min, max, step).
        config: Application settings.
        repo: Backtesting repository for persisting optimal params.

    Returns:
        Sweep results with per-value evaluations and best param.
    """
    from arb_scanner.cli._replay_helpers import run_sweep
    from arb_scanner.models.backtesting import OptimalParamSnapshot

    try:
        now = datetime.now(tz=UTC)
        since = now - timedelta(days=30)
        result = await run_sweep(
            config,
            body.category,
            since,
            now,
            body.param,
            body.min,
            body.max,
            body.step,
        )
        # Persist best param — results is list of [value, eval_dict]
        entries: list[Any] = result.get("results", [])
        if entries:
            best = max(entries, key=lambda r: r[1].get("win_rate", 0))
            snapshot = OptimalParamSnapshot(
                category=body.category,
                param_name=body.param,
                optimal_value=best[0],
                win_rate_at_optimal=best[1].get("win_rate", 0),
                sweep_date=now,
            )
            await repo.upsert_optimal_param(snapshot)
        return result
    except Exception as exc:
        logger.error("sweep_failed", error=str(exc))
        raise HTTPException(500, f"Sweep failed: {exc}") from exc
