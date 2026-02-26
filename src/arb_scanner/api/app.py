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
    """Manage database pool lifecycle."""
    config: Settings = app.state.config
    no_db: bool = getattr(app.state, "no_db", False)
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
    yield
    if not no_db and app.state.db is not None:
        await app.state.db.disconnect()
    logger.info("api.stopped")


def create_app(config: Settings, *, no_db: bool = False) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Application settings.
        no_db: When True, skip database connection (UI-only mode).

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

    app.include_router(opportunities_router)
    app.include_router(health_router)
    app.include_router(alerts_router)
    app.include_router(matches_router)
    app.include_router(tickets_router)
    app.include_router(scan_router)
    app.include_router(flippening_router)
    app.include_router(price_stream_router)

    return app
