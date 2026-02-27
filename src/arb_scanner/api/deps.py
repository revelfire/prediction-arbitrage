"""FastAPI dependency injection for database repositories."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from arb_scanner.models.config import Settings
from arb_scanner.storage.analytics_repository import AnalyticsRepository
from arb_scanner.storage.repository import Repository


def _require_db(request: Request) -> Any:
    """Extract the database object from app state, raising 503 if unavailable.

    Args:
        request: The incoming HTTP request.

    Returns:
        The Database instance.

    Raises:
        HTTPException: 503 when running in --no-db mode.
    """
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(503, "Database not available (running in --no-db mode)")
    return db


async def get_repo(request: Request) -> Repository:
    """Provide a Repository instance from the app's database pool.

    Args:
        request: The incoming HTTP request.

    Returns:
        Repository backed by the shared connection pool.
    """
    db = _require_db(request)
    return Repository(db.pool)


async def get_analytics_repo(request: Request) -> AnalyticsRepository:
    """Provide an AnalyticsRepository from the app's database pool.

    Args:
        request: The incoming HTTP request.

    Returns:
        AnalyticsRepository backed by the shared connection pool.
    """
    db = _require_db(request)
    return AnalyticsRepository(db.pool)


async def get_flip_repo(request: Request) -> Any:
    """Provide a FlippeningRepository from the app's database pool.

    Args:
        request: The incoming HTTP request.

    Returns:
        FlippeningRepository backed by the shared connection pool.
    """
    from arb_scanner.storage.flippening_repository import (
        FlippeningRepository,
    )

    db = _require_db(request)
    return FlippeningRepository(db.pool)


async def get_ticket_repo(request: Request) -> Any:
    """Provide a TicketRepository from the app's database pool.

    Args:
        request: The incoming HTTP request.

    Returns:
        TicketRepository backed by the shared connection pool.
    """
    from arb_scanner.storage.ticket_repository import TicketRepository

    db = _require_db(request)
    return TicketRepository(db.pool)


async def get_config(request: Request) -> Settings:
    """Provide the application Settings.

    Args:
        request: The incoming HTTP request.

    Returns:
        The application Settings instance.
    """
    config: Settings = request.app.state.config
    return config
