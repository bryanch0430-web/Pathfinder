import logging
from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)

# Normalize PostgreSQL scheme for asyncpg driver if needed
database_url = settings.DATABASE_URL
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)

# Configure engine settings
engine_kwargs = {"echo": False}
if "sqlite" in database_url:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # The hosted database drops connections that sit idle, and the first request after a pause then failed
    # with "connection is closed" (a 500 the browser reports as a CORS error). Checking a pooled connection
    # before use replaces a dead one. ponytail: one ~200 ms ping per request; pool_recycle is free but only
    # covers idle drops, so switch to it if the ping ever matters more than the rare stale connection.
    engine_kwargs["pool_pre_ping"] = True
    # Supabase's transaction pooler (port 6543) can't keep asyncpg's prepared statements between queries;
    # without this every second query fails with "prepared statement does not exist". The session pooler
    # (port 5432) doesn't need it.
    if ":6543/" in database_url:
        engine_kwargs["connect_args"] = {"statement_cache_size": 0}

engine = create_async_engine(database_url, **engine_kwargs)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency generator yielding an async SQLAlchemy session."""
    async with async_session_maker() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_database_connection() -> dict:
    """Helper function to test database connectivity for health checks."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "connected", "driver": engine.name}
    except Exception as e:
        logger.warning(f"Database health check ping failed: {str(e)}")
        return {"status": "disconnected", "driver": engine.name, "details": str(e)}


async def init_db():
    """Initialize database tables according to registered ORM models."""
    import app.models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if "sqlite" in engine.name:
            plan_columns = {column[1] for column in (await conn.execute(text("PRAGMA table_info(budget_plans)"))).all()}
            if plan_columns and "is_locked" not in plan_columns:
                await conn.execute(text("ALTER TABLE budget_plans ADD COLUMN is_locked BOOLEAN NOT NULL DEFAULT 0"))
                await conn.execute(text("ALTER TABLE budget_plans ADD COLUMN locked_at DATETIME"))
                await conn.execute(text("ALTER TABLE budget_plans ADD COLUMN locked_total NUMERIC(12, 2)"))
            columns = (await conn.execute(text("PRAGMA table_info(trips)"))).all()
            if columns and not any(column[1] == "places_to_visit" for column in columns):
                await conn.execute(text("ALTER TABLE trips ADD COLUMN places_to_visit JSON NOT NULL DEFAULT '[]'"))
            if columns and not any(column[1] == "planning_preferences" for column in columns):
                await conn.execute(text("ALTER TABLE trips ADD COLUMN planning_preferences JSON NOT NULL DEFAULT '{}'"))
            budget_columns = {column[1] for column in (await conn.execute(text("PRAGMA table_info(budget_items)"))).all()}
            if budget_columns and "estimate_amount" not in budget_columns:
                await conn.execute(text("ALTER TABLE budget_items ADD COLUMN estimate_amount NUMERIC(12, 2)"))
                await conn.execute(text("ALTER TABLE budget_items ADD COLUMN estimate_note VARCHAR(255)"))
        if "postgresql" in engine.name:
            try:
                await conn.execute(text("ALTER TABLE budget_plans ADD COLUMN IF NOT EXISTS is_locked BOOLEAN NOT NULL DEFAULT FALSE"))
                await conn.execute(text("ALTER TABLE budget_plans ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ"))
                await conn.execute(text("ALTER TABLE budget_plans ADD COLUMN IF NOT EXISTS locked_total NUMERIC(12, 2)"))
                await conn.execute(text("ALTER TABLE trips ADD COLUMN IF NOT EXISTS places_to_visit JSON NOT NULL DEFAULT '[]'"))
                await conn.execute(text("ALTER TABLE trips ADD COLUMN IF NOT EXISTS planning_preferences JSON NOT NULL DEFAULT '{}'"))
                await conn.execute(text("ALTER TABLE trips ADD COLUMN IF NOT EXISTS guest_id VARCHAR(64);"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trips_guest_id ON trips(guest_id);"))
                await conn.execute(text("ALTER TABLE budget_items ADD COLUMN IF NOT EXISTS estimate_amount NUMERIC(12, 2)"))
                await conn.execute(text("ALTER TABLE budget_items ADD COLUMN IF NOT EXISTS estimate_note VARCHAR(255)"))
            except Exception as e:
                logger.debug(f"PostgreSQL migration check note: {e}")
    logger.info("Database schema initialized.")



async def close_db():
    """Dispose connection pool on application shutdown."""
    await engine.dispose()

