"""FastAPI dependency injection for database repositories."""

from __future__ import annotations

from fastapi import Request

from arb_scanner.models.config import Settings
from arb_scanner.storage.analytics_repository import AnalyticsRepository
from arb_scanner.storage.repository import Repository


async def get_repo(request: Request) -> Repository:
    """Provide a Repository instance from the app's database pool.

    Args:
        request: The incoming HTTP request.

    Returns:
        Repository backed by the shared connection pool.
    """
    db = request.app.state.db
    return Repository(db.pool)


async def get_analytics_repo(request: Request) -> AnalyticsRepository:
    """Provide an AnalyticsRepository from the app's database pool.

    Args:
        request: The incoming HTTP request.

    Returns:
        AnalyticsRepository backed by the shared connection pool.
    """
    db = request.app.state.db
    return AnalyticsRepository(db.pool)


async def get_config(request: Request) -> Settings:
    """Provide the application Settings.

    Args:
        request: The incoming HTTP request.

    Returns:
        The application Settings instance.
    """
    config: Settings = request.app.state.config
    return config
