"""FastAPI application factory for the arb scanner dashboard."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
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
    """Wire up split auto-execution pipelines on app state.

    Creates independent arb and flip pipelines with separate breakers
    but a shared capital manager.

    Args:
        app: FastAPI application.
        config: Application settings.
    """
    from arb_scanner.execution.arb_critic import ArbTradeCritic
    from arb_scanner.execution.arb_pipeline import ArbAutoExecutionPipeline
    from arb_scanner.execution.circuit_breaker import CircuitBreakerManager
    from arb_scanner.execution.flip_critic import FlipTradeCritic
    from arb_scanner.execution.flip_exit_executor import FlipExitExecutor
    from arb_scanner.execution.flip_pipeline import FlipAutoExecutionPipeline
    from arb_scanner.execution.flip_position_repo import FlipPositionRepo
    from arb_scanner.storage.auto_exec_repository import AutoExecRepository
    from arb_scanner.storage.execution_repository import ExecutionRepository

    ac = config.auto_execution
    auto_repo = AutoExecRepository(app.state.db.pool)
    position_repo = FlipPositionRepo(app.state.db.pool)
    exec_repo = ExecutionRepository(app.state.db.pool)

    orch = getattr(app.state, "execution_orchestrator", None)
    capital = getattr(app.state, "capital_manager", None)
    poly = getattr(app.state, "poly_executor", None)
    kalshi = getattr(app.state, "kalshi_executor", None)

    # Independent circuit breakers per pipeline
    arb_breakers = CircuitBreakerManager(ac)
    flip_breakers = CircuitBreakerManager(ac)

    # Arb pipeline: two-leg execution via orchestrator
    arb_critic = ArbTradeCritic(ac.critic, config.claude)
    arb_pipeline = ArbAutoExecutionPipeline(
        config=config,
        auto_config=ac,
        orchestrator=orch,
        critic=arb_critic,
        breakers=arb_breakers,
        capital=capital,
        poly=poly,
        kalshi=kalshi,
        auto_repo=auto_repo,
    )

    # Flip pipeline: single-leg execution via PolymarketExecutor
    flip_critic = FlipTradeCritic(ac.critic, config.claude)
    exit_executor = FlipExitExecutor(
        poly=poly,
        exec_repo=exec_repo,
        position_repo=position_repo,
        stop_loss_aggression_pct=ac.stop_loss_aggression_pct,
    )
    flip_pipeline = FlipAutoExecutionPipeline(
        config=config,
        auto_config=ac,
        critic=flip_critic,
        breakers=flip_breakers,
        capital=capital,
        poly=poly,
        position_repo=position_repo,
        auto_repo=auto_repo,
        exec_repo=exec_repo,
        exit_executor=exit_executor,
    )

    # Store on app.state for routes
    app.state.arb_pipeline = arb_pipeline
    app.state.flip_pipeline = flip_pipeline
    app.state.arb_breakers = arb_breakers
    app.state.flip_breakers = flip_breakers
    app.state.auto_exec_repo = auto_repo
    app.state.flip_position_repo = position_repo
    app.state.flip_exit_executor = exit_executor

    # Sidecar refs for CLI and flippening orchestrator access
    object.__setattr__(config, "_arb_pipeline", arb_pipeline)
    object.__setattr__(config, "_flip_pipeline", flip_pipeline)
    logger.info("split_pipelines_initialised", arb="ready", flip="ready")


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
    from arb_scanner.utils.logging import setup_logging

    setup_logging(
        level=config.logging.level,
        json_format=config.logging.format == "json",
    )

    app = FastAPI(
        title="Arb Scanner Dashboard",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.state.config = config
    app.state.no_db = no_db
    app.state.flip_watch = flip_watch

    # Bearer token auth (must be added before static files mount)
    from arb_scanner.api.auth import BearerTokenMiddleware

    app.add_middleware(BearerTokenMiddleware, token=config.dashboard.auth_token)

    # Static files
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/")
    async def root() -> HTMLResponse:
        """Serve the dashboard HTML with optional auth token meta tag."""
        html = (_STATIC_DIR / "index.html").read_text()
        token = config.dashboard.auth_token
        if token:
            meta_tag = f'<meta name="api-token" content="{token}">'
            html = html.replace("</head>", f"    {meta_tag}\n</head>", 1)
        return HTMLResponse(html)

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
