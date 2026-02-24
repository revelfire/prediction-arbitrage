"""Integration test fixtures for database-dependent tests."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, AsyncGenerator

import pytest
import pytest_asyncio

if TYPE_CHECKING:
    import asyncpg

_DATABASE_URL = os.environ.get("DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not _DATABASE_URL,
    reason="DATABASE_URL not set; skipping PostgreSQL integration tests",
)


@pytest_asyncio.fixture()
async def db_pool() -> AsyncGenerator[asyncpg.Pool[asyncpg.Record], None]:
    """Create a test database connection pool and run migrations.

    Yields an asyncpg pool after applying all pending migrations.
    Rolls back data by truncating all application tables after the test.
    """
    import asyncpg as _asyncpg

    from arb_scanner.storage.migrations_runner import run_migrations

    pool: _asyncpg.Pool[_asyncpg.Record] = await _asyncpg.create_pool(_DATABASE_URL)
    assert pool is not None

    await run_migrations(pool)

    yield pool

    # Clean up all application data after each test
    await pool.execute(
        "TRUNCATE execution_tickets, arb_opportunities, match_results,"
        " markets, scan_logs, market_price_snapshots CASCADE"
    )
    await pool.close()
