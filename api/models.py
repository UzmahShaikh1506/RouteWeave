"""
SQLAlchemy ORM models and Pydantic schemas for the RouteWeave API.

Data model:
  - Job: one row per CSV upload / optimization run
  - Stop: one row per delivery address (with geocoded coordinates)
  - Route: summary statistics for a completed optimization run
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import Uuid
from sqlalchemy.orm import DeclarativeBase, relationship
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# SQLAlchemy ORM Models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class Job(Base):
    """
    Represents a single optimization job triggered by a CSV upload.

    Statuses: pending → geocoding → optimizing → completed | failed
    """
    __tablename__ = "jobs"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    status = Column(String(20), default="pending", nullable=False)
    error_message = Column(Text, nullable=True)
    depot_address = Column(String(500), nullable=True)
    depot_lat = Column(Float, nullable=True)
    depot_lng = Column(Float, nullable=True)

    # Relationships
    stops = relationship("Stop", back_populates="job", cascade="all, delete-orphan")
    route = relationship("Route", back_populates="job", uselist=False, cascade="all, delete-orphan")


class Stop(Base):
    """
    A delivery stop within a job. Stores the raw address, geocoded
    coordinates, and the final visit order after optimization.
    """
    __tablename__ = "stops"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id = Column(Uuid, ForeignKey("jobs.id"), nullable=False)
    raw_address = Column(String(500), nullable=False)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    visit_order = Column(Integer, nullable=True)

    # Relationships
    job = relationship("Job", back_populates="stops")


class Route(Base):
    """
    Summary statistics for a completed optimization run.
    Powers the 'before vs after' display in the client.
    """
    __tablename__ = "routes"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id = Column(Uuid, ForeignKey("jobs.id"), nullable=False, unique=True)
    naive_distance_km = Column(Float, nullable=False)
    optimized_distance_km = Column(Float, nullable=False)
    pct_improvement = Column(Float, nullable=False)

    # Relationships
    job = relationship("Job", back_populates="route")


# ---------------------------------------------------------------------------
# Pydantic Schemas (API request/response serialization)
# ---------------------------------------------------------------------------

class StopResponse(BaseModel):
    """A stop in the API response."""
    id: str
    address: str
    lat: float | None = None
    lng: float | None = None
    visit_order: int | None = None

    model_config = {"from_attributes": True}


class RouteResponse(BaseModel):
    """Route optimization results."""
    job_id: str
    naive_distance_km: float
    optimized_distance_km: float
    pct_improvement: float
    depot: StopResponse | None = None
    stops: list[StopResponse] = []


class JobResponse(BaseModel):
    """Job status response."""
    id: str
    created_at: datetime
    status: str
    error_message: str | None = None
    stop_count: int = 0

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    """Paginated list of jobs."""
    jobs: list[JobResponse]
    total: int


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    service: str
    database: str
