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

    flip_task: asyncio.Task[None] | None = None
    if flip_watch and not no_db:
        from arb_scanner.flippening.orchestrator import run_flip_watch

        flip_task = asyncio.create_task(run_flip_watch(config))
        logger.info("flip_watch_embedded", mode="background_task")

    yield

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
