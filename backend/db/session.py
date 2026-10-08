"""Async engine / session factory for the PostgreSQL backend."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def _with_asyncpg_driver(database_url: str) -> str:
    """Accept the usual ``postgresql://`` / ``postgres://`` spellings and pin the asyncpg driver
    (the only async driver installed), so a plain URL from a hosting panel still works."""
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+asyncpg://" + database_url[len(prefix) :]
    return database_url


def create_engine_and_sessionmaker(
    database_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build the async engine and a session factory.

    ``expire_on_commit=False`` because repositories convert rows to pydantic records inside the
    session and never touch ORM instances afterwards. ``pool_pre_ping`` survives a restarted
    database without surfacing a stale-connection error to a request.
    """
    engine = create_async_engine(_with_asyncpg_driver(database_url), pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
