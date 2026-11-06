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

    For PostgreSQL:
      - If the schema is managed by Alembic (alembic_version table exists),
        nothing is done here; run 'alembic upgrade head' instead.
      - Otherwise (e.g. the docker-compose setup, where db/init.sql may have
        created only some tables), create any missing tables and add any
        columns introduced after the initial schema. Both steps are
        idempotent, so existing data volumes keep working after upgrades.
    """
    if _is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return

    import logging
    from sqlalchemy import inspect as sa_inspect, text

    logger = logging.getLogger("routeweave.db")

    def _alembic_managed(connection):
        return sa_inspect(connection).has_table("alembic_version")

    async with engine.begin() as conn:
        if await conn.run_sync(_alembic_managed):
            return

        logger.info(
            "Schema not managed by Alembic — ensuring tables/columns exist. "
            "For production, prefer 'alembic upgrade head'."
        )
        # create_all() uses checkfirst=True: only missing tables are created
        await conn.run_sync(Base.metadata.create_all)
        # Columns added after the initial schema (keep in sync with migrations)
        await conn.execute(
            text("ALTER TABLE stops ADD COLUMN IF NOT EXISTS input_order INTEGER")
        )


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
