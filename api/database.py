"""
Async database engine and session management for the RouteWeave API.

Uses SQLAlchemy's async engine with asyncpg as the PostgreSQL driver.
Reads DATABASE_URL from environment for container-friendly configuration.

Supports both PostgreSQL (production) and SQLite (testing).
"""

import os

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from models import Base

# Read database URL from environment, defaulting to a local dev value.
# The docker-compose environment variable uses 'postgresql://' scheme,
# but asyncpg requires 'postgresql+asyncpg://'.
_raw_url = os.getenv(
    "DATABASE_URL",
    "postgresql://routeweave:routeweave@localhost:5432/routeweave",
)

# Ensure the async driver is specified in the URL
if _raw_url.startswith("postgresql://"):
    DATABASE_URL = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _raw_url.startswith("postgresql+asyncpg://"):
    DATABASE_URL = _raw_url
else:
    DATABASE_URL = _raw_url

# Determine engine options based on backend
_is_sqlite = DATABASE_URL.startswith("sqlite")

_engine_kwargs = {
    "echo": False,  # Set to True for SQL query logging during development
}

if not _is_sqlite:
    # PostgreSQL-specific pool options
    _engine_kwargs.update({
        "pool_size": 5,
        "max_overflow": 10,
        "pool_pre_ping": True,
    })

# Create async engine
engine = create_async_engine(DATABASE_URL, **_engine_kwargs)

# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db():
    """
    Initialize the database.

    For SQLite (testing/development), creates all tables via create_all().
    For PostgreSQL (production), prefers Alembic migrations, but falls back
    to create_all() if no tables exist yet (first-run local dev convenience).
    """
    if _is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return

    # PostgreSQL: check if the jobs table exists before auto-creating
    from sqlalchemy import inspect as sa_inspect

    def _tables_exist(connection):
        inspector = sa_inspect(connection)
        return inspector.has_table("jobs")

    async with engine.begin() as conn:
        tables_exist = await conn.run_sync(_tables_exist)
        if not tables_exist:
            # Fallback for first-run local development without Alembic
            import logging
            logger = logging.getLogger("routeweave.db")
            logger.warning(
                "No tables found — falling back to create_all(). "
                "For production, run 'alembic upgrade head' instead."
            )
            await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    """
    Dependency that yields an async database session.
    Used with FastAPI's Depends() for request-scoped sessions.
    """
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
