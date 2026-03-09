"""Standalone pipeline initialization for CLI auto-execution."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import structlog

from arb_scanner.models.config import Settings

logger: structlog.stdlib.BoundLogger = structlog.get_logger(
    module="execution.pipeline_init",
)


async def init_flip_pipeline_standalone(
    config: Settings,
) -> Any:
    """Create and return a FlipAutoExecutionPipeline for CLI use.

    Requires DATABASE_URL and POLY_PRIVATE_KEY environment variables.
    Fails fast with clear error messages if dependencies are missing.

    Args:
        config: Application settings with auto_execution config.

    Returns:
        Configured FlipAutoExecutionPipeline instance.

    Raises:
        RuntimeError: If required env vars or DB connection fails.
    """
    db_url = config.storage.database_url
    if not db_url:
        raise RuntimeError("DATABASE_URL required for --auto-execute")
    poly_key = os.environ.get("POLY_PRIVATE_KEY", "")
    if not poly_key:
        raise RuntimeError("POLY_PRIVATE_KEY required for --auto-execute")

    return await _build_pipeline(config, db_url)


async def _build_pipeline(
    config: Settings,
    db_url: str,
) -> Any:
    """Construct the pipeline components and return the assembled pipeline.

    Args:
        config: Application settings.
        db_url: Validated database connection URL.

    Returns:
        Configured FlipAutoExecutionPipeline instance.
    """
    from arb_scanner.storage.db import Database

    db = Database(db_url)
    await db.connect()

    pool = db.pool
    ac = config.auto_execution.effective_config("flip")
    infra = _create_infra(config, ac, pool)
    return _assemble_pipeline(config, ac, infra)


def _create_infra(
    config: Settings,
    ac: Any,
    pool: Any,
) -> dict[str, Any]:
    """Create infrastructure components for the pipeline.

    Args:
        config: Application settings.
        ac: Effective auto-execution config.
        pool: asyncpg connection pool.

    Returns:
        Dict of named infrastructure components.
    """
    from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
    from arb_scanner.execution.flip_critic import FlipTradeCritic
    from arb_scanner.execution.flip_exit_executor import FlipExitExecutor
    from arb_scanner.execution.flip_position_repo import FlipPositionRepo
    from arb_scanner.execution.polymarket_executor import PolymarketExecutor
    from arb_scanner.storage.auto_exec_repository import AutoExecRepository
    from arb_scanner.storage.execution_repository import ExecutionRepository

    poly = PolymarketExecutor(config.execution.polymarket)
    position_repo = FlipPositionRepo(pool)
    exec_repo = ExecutionRepository(pool)

    return {
        "breakers": CircuitBreakerManager(ac),
        "critic": FlipTradeCritic(ac.critic, config.claude),
        "poly": poly,
        "position_repo": position_repo,
        "auto_repo": AutoExecRepository(pool),
        "exec_repo": exec_repo,
        "exit_executor": FlipExitExecutor(
            poly=poly,
            exec_repo=exec_repo,
            position_repo=position_repo,
            stop_loss_aggression_pct=ac.stop_loss_aggression_pct,
        ),
        "capital": _build_capital_stub(config),
    }


def _assemble_pipeline(
    config: Settings,
    ac: Any,
    infra: dict[str, Any],
) -> Any:
    """Assemble the FlipAutoExecutionPipeline from components.

    Args:
        config: Application settings.
        ac: Effective auto-execution config.
        infra: Dict of named infrastructure components.

    Returns:
        Configured FlipAutoExecutionPipeline instance.
    """
    from arb_scanner.execution.flip_pipeline import FlipAutoExecutionPipeline

    pipeline = FlipAutoExecutionPipeline(
        config=config,
        auto_config=ac,
        critic=infra["critic"],
        breakers=infra["breakers"],
        capital=infra["capital"],
        poly=infra["poly"],
        position_repo=infra["position_repo"],
        auto_repo=infra["auto_repo"],
        exec_repo=infra["exec_repo"],
        exit_executor=infra["exit_executor"],
    )
    logger.info("flip_pipeline_standalone_init")
    return pipeline


def _build_capital_stub(config: Settings) -> Any:
    """Build a minimal capital manager for CLI pipeline use.

    Args:
        config: Application settings.

    Returns:
        Capital manager or stub with required attributes.
    """
    try:
        from arb_scanner.execution.capital_manager import (
            CapitalManager,
        )

        async def _noop_balance() -> Decimal:
            return Decimal("0")

        return CapitalManager(
            config.execution,
            poly_get_balance=_noop_balance,
            kalshi_get_balance=_noop_balance,
        )
    except Exception:
        logger.warning("capital_manager_fallback_stub")
        return _CapitalStub()


class _CapitalStub:
    """Minimal stub when CapitalManager can't be instantiated."""

    def __init__(self) -> None:
        """Initialize with safe defaults."""
        self.current_exposure: Decimal = Decimal("0")
        self.total_balance: Decimal = Decimal("1000")
        self.daily_pnl: Decimal = Decimal("0")

    async def refresh_balances(self) -> None:
        """No-op refresh."""
