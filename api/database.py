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
    Create all tables defined in the ORM models.
    Called once on application startup.
    """
    async with engine.begin() as conn:
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
