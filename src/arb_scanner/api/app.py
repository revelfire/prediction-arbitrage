"""FastAPI application factory for the arb scanner dashboard."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from arb_scanner.models.config import Settings
from arb_scanner.storage.db import Database

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="api.app")

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage database pool and optional flip-watch lifecycle."""
    import asyncio

    config: Settings = app.state.config
    no_db: bool = getattr(app.state, "no_db", False)
    flip_watch: bool = getattr(app.state, "flip_watch", False)
    if no_db:
        app.state.db = None
        logger.info(
            "api.started", host=config.dashboard.host, port=config.dashboard.port, db="disabled"
        )
    else:
        db = Database(config.storage.database_url)
        await db.connect()
        app.state.db = db
        logger.info("api.started", host=config.dashboard.host, port=config.dashboard.port)

    if not no_db and config.execution.enabled and app.state.db is not None:
        _init_execution(app, config)

    if not no_db and config.auto_execution.enabled and app.state.db is not None:
        _init_auto_execution(app, config)

    flip_task: asyncio.Task[None] | None = None
    if flip_watch and not no_db:
        from arb_scanner.flippening.orchestrator import run_flip_watch

        flip_task = asyncio.create_task(run_flip_watch(config))
        logger.info("flip_watch_embedded", mode="background_task")

    expire_task: asyncio.Task[None] | None = None
    if not no_db and app.state.db is not None:
        expire_task = asyncio.create_task(_run_ticket_expiry(app))
        logger.info("ticket_expiry_started")

    yield

    if expire_task is not None:
        expire_task.cancel()
        try:
            await expire_task
        except asyncio.CancelledError:
            pass
        logger.info("ticket_expiry_stopped")
    if flip_task is not None:
        flip_task.cancel()
        try:
            await flip_task
        except asyncio.CancelledError:
            pass
        logger.info("flip_watch_stopped")
    if not no_db and app.state.db is not None:
        await app.state.db.disconnect()
    logger.info("api.stopped")


def _init_execution(app: FastAPI, config: Settings) -> None:
    """Wire up execution engine components on app state.

    Args:
        app: FastAPI application.
        config: Application settings.
    """
    from arb_scanner.execution.capital_manager import CapitalManager
    from arb_scanner.execution.kalshi_executor import KalshiExecutor
    from arb_scanner.execution.orchestrator import ExecutionOrchestrator
    from arb_scanner.execution.polymarket_executor import PolymarketExecutor
    from arb_scanner.storage.execution_repository import ExecutionRepository
    from arb_scanner.storage.ticket_repository import TicketRepository

    poly = PolymarketExecutor(config.execution.polymarket)
    kalshi = KalshiExecutor(config.execution.kalshi)
    capital = CapitalManager(config.execution, poly.get_balance, kalshi.get_balance)
    exec_repo = ExecutionRepository(app.state.db.pool)
    ticket_repo = TicketRepository(app.state.db.pool)
    orch = ExecutionOrchestrator(
        config=config.execution,
        capital=capital,
        poly=poly,
        kalshi=kalshi,
        exec_repo=exec_repo,
        ticket_repo=ticket_repo,
    )
    app.state.execution_orchestrator = orch
    app.state.execution_repo = exec_repo
    app.state.capital_manager = capital
    app.state.poly_executor = poly
    app.state.kalshi_executor = kalshi
    logger.info("execution_engine_initialised")


def _init_auto_execution(app: FastAPI, config: Settings) -> None:
    """Wire up auto-execution pipeline components on app state.

    Args:
        app: FastAPI application.
        config: Application settings.
    """
    from arb_scanner.execution.auto_pipeline import AutoExecutionPipeline
    from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
    from arb_scanner.execution.trade_critic import TradeCritic
    from arb_scanner.storage.auto_exec_repository import AutoExecRepository

    ac = config.auto_execution
    critic = TradeCritic(ac.critic, config.claude)
    breakers = CircuitBreakerManager(ac)
    auto_repo = AutoExecRepository(app.state.db.pool)

    orch = getattr(app.state, "execution_orchestrator", None)
    capital = getattr(app.state, "capital_manager", None)
    poly = getattr(app.state, "poly_executor", None)
    kalshi = getattr(app.state, "kalshi_executor", None)

    pipeline = AutoExecutionPipeline(
        config=config,
        auto_config=ac,
        orchestrator=orch,
        critic=critic,
        breakers=breakers,
        auto_repo=auto_repo,
        capital=capital,
        poly=poly,
        kalshi=kalshi,
    )
    app.state.auto_pipeline = pipeline
    app.state.circuit_breakers = breakers
    app.state.auto_exec_repo = auto_repo
    logger.info("auto_execution_pipeline_initialised")


async def _run_ticket_expiry(app: FastAPI) -> None:
    """Periodically expire stale pending tickets.

    Args:
        app: FastAPI application with config and db on state.
    """
    import asyncio

    from arb_scanner.storage.ticket_repository import TicketRepository

    config: Settings = app.state.config
    lc = config.ticket_lifecycle
    interval = lc.expire_interval_minutes * 60
    max_age = lc.max_pending_hours

    while True:
        try:
            await asyncio.sleep(interval)
            repo = TicketRepository(app.state.db.pool)
            expired = await repo.auto_expire(max_age_hours=max_age)
            if expired:
                logger.info("ticket_expiry_cycle", expired=len(expired))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ticket_expiry_error")


def create_app(
    config: Settings,
    *,
    no_db: bool = False,
    flip_watch: bool = False,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Application settings.
        no_db: When True, skip database connection (UI-only mode).
        flip_watch: When True, run flip-watch engine in-process.

    Returns:
        Configured FastAPI instance.
    """
    app = FastAPI(
        title="Arb Scanner Dashboard",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.state.config = config
    app.state.no_db = no_db
    app.state.flip_watch = flip_watch

    # Static files
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/")
    async def root() -> FileResponse:
        """Serve the dashboard HTML."""
        return FileResponse(str(_STATIC_DIR / "index.html"))

    # API route modules
    from arb_scanner.api.routes_alerts import router as alerts_router
    from arb_scanner.api.routes_auto_execution import router as auto_execution_router
    from arb_scanner.api.routes_execution import router as execution_router
    from arb_scanner.api.routes_flippening import router as flippening_router
    from arb_scanner.api.routes_health import router as health_router
    from arb_scanner.api.routes_matches import router as matches_router
    from arb_scanner.api.routes_opportunities import router as opportunities_router
    from arb_scanner.api.routes_price_stream import router as price_stream_router
    from arb_scanner.api.routes_scan import router as scan_router
    from arb_scanner.api.routes_tickets import router as tickets_router
    from arb_scanner.api.routes_ws_telemetry import router as ws_telemetry_router

    app.include_router(opportunities_router)
    app.include_router(health_router)
    app.include_router(alerts_router)
    app.include_router(matches_router)
    app.include_router(tickets_router)
    app.include_router(scan_router)
    app.include_router(flippening_router)
    app.include_router(price_stream_router)
    app.include_router(ws_telemetry_router)
    app.include_router(execution_router)
    app.include_router(auto_execution_router)

    return app


def create_app_from_env() -> FastAPI:
    """Factory for uvicorn --reload mode (reads config from env).

    Returns:
        Configured FastAPI instance.
    """
    import os

    from arb_scanner.config.loader import load_config

    no_db = os.environ.get("ARB_NO_DB", "0") == "1"
    flip_watch = os.environ.get("ARB_FLIP_WATCH", "0") == "1"
    config = load_config()
    return create_app(config, no_db=no_db, flip_watch=flip_watch)
