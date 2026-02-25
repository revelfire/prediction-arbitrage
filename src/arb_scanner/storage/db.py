"""Database connection management with asyncpg."""

from __future__ import annotations

from types import TracebackType
from typing import Self

import asyncpg
from pgvector.asyncpg import register_vector  # type: ignore[import-untyped]


class Database:
    """Manages an asyncpg connection pool with async context manager support."""

    def __init__(self, database_url: str) -> None:
        """Initialize the database wrapper.

        Args:
            database_url: PostgreSQL connection URL.
        """
        self._database_url = database_url
        self._pool: asyncpg.Pool[asyncpg.Record] | None = None

    @property
    def pool(self) -> asyncpg.Pool[asyncpg.Record]:
        """Return the asyncpg connection pool.

        Raises:
            RuntimeError: If the pool has not been created via connect().
        """
        if self._pool is None:
            raise RuntimeError("Database pool not initialized. Call connect() first.")
        return self._pool

    async def connect(self) -> None:
        """Create the asyncpg connection pool with pgvector type support."""
        self._pool = await asyncpg.create_pool(
            self._database_url,
            init=_init_connection,
        )

    async def disconnect(self) -> None:
        """Close the asyncpg connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def health_check(self) -> bool:
        """Execute SELECT 1 to verify database connectivity.

        Returns:
            True if the query succeeds, False otherwise.
        """
        try:
            await self.pool.execute("SELECT 1")
        except Exception:
            return False
        return True

    async def __aenter__(self) -> Self:
        """Enter async context manager and connect."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context manager and disconnect."""
        await self.disconnect()


async def _init_connection(conn: asyncpg.Connection[asyncpg.Record]) -> None:
    """Register pgvector types on each new pool connection.

    Gracefully skips registration if the pgvector extension is not yet
    installed (e.g. on a fresh database before migrations run).

    Args:
        conn: The newly created asyncpg connection.
    """
    try:
        await register_vector(conn)
    except (ValueError, asyncpg.UndefinedObjectError):
        # pgvector extension not yet installed — skip type registration.
        # Happens on fresh databases before migrations run.
        pass
