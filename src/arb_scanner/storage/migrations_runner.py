"""Database migration runner for applying SQL migrations in order."""

from __future__ import annotations

from pathlib import Path

import asyncpg
import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(module="migrations")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS _migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


async def _get_applied(pool: asyncpg.Pool[asyncpg.Record]) -> set[str]:
    """Fetch the set of already-applied migration filenames."""
    rows = await pool.fetch("SELECT filename FROM _migrations")
    return {row["filename"] for row in rows}


async def _apply_migration(
    pool: asyncpg.Pool[asyncpg.Record],
    filepath: Path,
) -> None:
    """Apply a single migration file and record it."""
    sql = filepath.read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO _migrations (filename) VALUES ($1)",
                filepath.name,
            )


async def run_migrations(pool: asyncpg.Pool[asyncpg.Record]) -> list[str]:
    """Run all pending SQL migrations from the migrations directory.

    Creates the _migrations tracking table if it does not exist,
    then applies any unapplied .sql files in sorted order.

    Args:
        pool: An asyncpg connection pool.

    Returns:
        List of newly applied migration filenames.
    """
    await pool.execute(_ENSURE_TABLE_SQL)
    applied = await _get_applied(pool)

    sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    newly_applied: list[str] = []

    for filepath in sql_files:
        if filepath.name in applied:
            continue
        logger.info("applying_migration", filename=filepath.name)
        await _apply_migration(pool, filepath)
        newly_applied.append(filepath.name)
        logger.info("migration_applied", filename=filepath.name)

    return newly_applied
