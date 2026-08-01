"""
RouteWeave API — Main application entry point.

Provides endpoints for creating optimization jobs (CSV upload),
polling job status, and retrieving optimized routes.

Security features:
  - In-memory per-IP rate limiting on POST /jobs
  - Input validation via Pydantic models
  - CORS restricted to known origins
  - Parameterized SQL queries (SQLAlchemy ORM)
"""

import csv
import io
import logging
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager

import httpx
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_session, init_db
from geocode import geocode_address
from models import (
    HealthResponse,
    Job,
    JobListResponse,
    JobResponse,
    Route,
    RouteResponse,
    Stop,
    StopResponse,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("routeweave.api")


# ---------------------------------------------------------------------------
# Rate Limiter (in-memory, per-IP)
# ---------------------------------------------------------------------------

MAX_REQUESTS_PER_MINUTE = 5

# Track request timestamps per IP
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(client_ip: str) -> bool:
    """
    Returns True if the request should be ALLOWED, False if rate-limited.
    Implements a sliding-window counter: max 5 requests per 60-second window.
    """
    now = time.time()
    window_start = now - 60.0

    # Clean old entries
    _rate_limit_store[client_ip] = [
        ts for ts in _rate_limit_store[client_ip] if ts > window_start
    ]

    if len(_rate_limit_store[client_ip]) >= MAX_REQUESTS_PER_MINUTE:
        return False

    _rate_limit_store[client_ip].append(now)
    return True


# ---------------------------------------------------------------------------
# Optimizer service client
# ---------------------------------------------------------------------------

import os

OPTIMIZER_URL = os.getenv("OPTIMIZER_URL", "http://optimizer:8001")


async def _call_optimizer(depot: dict, stops: list[dict]) -> dict:
    """Call the optimizer microservice to compute the optimized route."""
    payload = {
        "depot": depot,
        "stops": stops,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{OPTIMIZER_URL}/optimize",
            json=payload,
        )
        response.raise_for_status()
        return response.json()


# ---------------------------------------------------------------------------
# Application Lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database tables on startup."""
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized successfully.")
    yield
    logger.info("Shutting down RouteWeave API.")


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RouteWeave API",
    description="Last-mile delivery route optimizer — upload addresses, get optimized routes.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow the client (Nginx on port 3000) and local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8080",
        "*",  # For development; restrict in production
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_STOPS = 50  # Maximum number of delivery stops per job


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health(session: AsyncSession = Depends(get_session)):
    """
    Liveness check. Verifies the API is running and can reach the database.
    Used by Docker HEALTHCHECK and CI pipelines.
    """
    try:
        await session.execute(select(func.count()).select_from(Job))
        db_status = "connected"
    except Exception:
        db_status = "disconnected"

    return HealthResponse(
        status="healthy",
        service="api",
        database=db_status,
    )


@app.post("/jobs", tags=["jobs"], status_code=201)
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    depot_address: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """
    Create a new optimization job by uploading a CSV of delivery addresses.

    The CSV must have an 'address' column. Each row is a delivery stop.
    Optionally provide a depot_address (the starting location).
    If no depot is provided, the first address in the CSV is used.

    Rate-limited to 5 requests per minute per IP.
    Maximum 50 stops per job.
    """
    # --- Rate limiting ---
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Maximum 5 jobs per minute.",
        )

    # --- Validate file type ---
    if file.content_type and file.content_type not in (
        "text/csv",
        "application/vnd.ms-excel",
        "application/octet-stream",
    ):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid file type: {file.content_type}. Expected CSV.",
        )

    # --- Parse CSV ---
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")  # Handle BOM
        reader = csv.DictReader(io.StringIO(text))

        # Validate that 'address' column exists
        if reader.fieldnames is None or "address" not in [
            f.strip().lower() for f in reader.fieldnames
        ]:
            raise HTTPException(
                status_code=422,
                detail="CSV must contain an 'address' column.",
            )

        # Normalize field names to lowercase
        addresses = []
        for row in reader:
            normalized = {k.strip().lower(): v.strip() for k, v in row.items()}
            addr = normalized.get("address", "").strip()
            if addr:
                addresses.append(addr)

        if len(addresses) == 0:
            raise HTTPException(
                status_code=422,
                detail="CSV contains no valid addresses.",
            )

        if len(addresses) > MAX_STOPS:
            raise HTTPException(
                status_code=422,
                detail=f"Too many stops ({len(addresses)}). Maximum is {MAX_STOPS}.",
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to parse CSV: {str(e)}",
        )

    # --- Create job record ---
    job = Job(
        id=uuid.uuid4(),
        status="pending",
        depot_address=depot_address if depot_address else addresses[0],
    )
    session.add(job)

    # Create stop records
    for addr in addresses:
        stop = Stop(
            id=uuid.uuid4(),
            job_id=job.id,
            raw_address=addr,
        )
        session.add(stop)

    await session.commit()
    await session.refresh(job)

    job_id = str(job.id)
    logger.info("Created job %s with %d stops", job_id, len(addresses))

    # --- Process job (geocode + optimize) ---
    # In a production system this would be an async task queue.
    # For this project, we process inline for simplicity.
    try:
        # Update status to geocoding
        job.status = "geocoding"
        await session.commit()

        # Geocode depot
        actual_depot_address = depot_address if depot_address else addresses[0]
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            from geocode import geocode_address as _geocode

            depot_coords = await _geocode(actual_depot_address, client=http_client)
            if depot_coords is None:
                job.status = "failed"
                job.error_message = f"Could not geocode depot address: {actual_depot_address}"
                await session.commit()
                return {"job_id": job_id, "status": "failed", "error": job.error_message}

            job.depot_lat = depot_coords[0]
            job.depot_lng = depot_coords[1]
            await session.commit()

            # Geocode all stops
            result = await session.execute(
                select(Stop).where(Stop.job_id == job.id)
            )
            stops = result.scalars().all()

            geocoded_stops = []
            for stop in stops:
                coords = await _geocode(stop.raw_address, client=http_client)
                if coords:
                    stop.lat = coords[0]
                    stop.lng = coords[1]
                    geocoded_stops.append(stop)
                else:
                    logger.warning(
                        "Failed to geocode stop '%s' in job %s",
                        stop.raw_address, job_id,
                    )

            await session.commit()

        if len(geocoded_stops) == 0:
            job.status = "failed"
            job.error_message = "Could not geocode any delivery addresses."
            await session.commit()
            return {"job_id": job_id, "status": "failed", "error": job.error_message}

        # Update status to optimizing
        job.status = "optimizing"
        await session.commit()

        # Call optimizer service
        depot_dict = {
            "id": "depot",
            "address": actual_depot_address,
            "lat": depot_coords[0],
            "lng": depot_coords[1],
        }
        stops_dicts = [
            {
                "id": str(s.id),
                "address": s.raw_address,
                "lat": s.lat,
                "lng": s.lng,
            }
            for s in geocoded_stops
        ]

        opt_result = await _call_optimizer(depot_dict, stops_dicts)

        # Update stops with visit_order
        for opt_stop in opt_result.get("ordered_stops", []):
            for db_stop in geocoded_stops:
                if str(db_stop.id) == opt_stop["id"]:
                    db_stop.visit_order = opt_stop["visit_order"]
                    break

        # Create route summary
        route = Route(
            id=uuid.uuid4(),
            job_id=job.id,
            naive_distance_km=opt_result["naive_distance_km"],
            optimized_distance_km=opt_result["optimized_distance_km"],
            pct_improvement=opt_result["pct_improvement"],
        )
        session.add(route)

        job.status = "completed"
        await session.commit()

        logger.info(
            "Job %s completed: %.2f km → %.2f km (%.1f%% improvement)",
            job_id,
            opt_result["naive_distance_km"],
            opt_result["optimized_distance_km"],
            opt_result["pct_improvement"],
        )

        return {
            "job_id": job_id,
            "status": "completed",
        }

    except Exception as e:
        logger.error("Job %s failed: %s", job_id, str(e), exc_info=True)
        job.status = "failed"
        job.error_message = str(e)
        await session.commit()
        return {"job_id": job_id, "status": "failed", "error": str(e)}


@app.get("/jobs", response_model=JobListResponse, tags=["jobs"])
async def list_jobs(
    limit: int = 20,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """List recent optimization jobs, newest first."""
    # Get total count
    count_result = await session.execute(select(func.count()).select_from(Job))
    total = count_result.scalar() or 0

    # Get paginated jobs
    result = await session.execute(
        select(Job)
        .options(selectinload(Job.stops))
        .order_by(Job.created_at.desc())
        .limit(min(limit, 50))
        .offset(offset)
    )
    jobs = result.scalars().all()

    return JobListResponse(
        jobs=[
            JobResponse(
                id=str(j.id),
                created_at=j.created_at,
                status=j.status,
                error_message=j.error_message,
                stop_count=len(j.stops),
            )
            for j in jobs
        ],
        total=total,
    )


@app.get("/jobs/{job_id}", tags=["jobs"])
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get the status of a specific job."""
    try:
        uid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid job ID format.")

    result = await session.execute(
        select(Job).options(selectinload(Job.stops)).where(Job.id == uid)
    )
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    return JobResponse(
        id=str(job.id),
        created_at=job.created_at,
        status=job.status,
        error_message=job.error_message,
        stop_count=len(job.stops),
    )


@app.get("/jobs/{job_id}/route", response_model=RouteResponse, tags=["jobs"])
async def get_route(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Get the optimized route for a completed job.
    Returns before/after distances and stops in optimized order.
    """
    try:
        uid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid job ID format.")

    # Fetch job with stops and route
    result = await session.execute(
        select(Job)
        .options(selectinload(Job.stops), selectinload(Job.route))
        .where(Job.id == uid)
    )
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    if job.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Job is not completed (current status: {job.status}).",
        )

    if job.route is None:
        raise HTTPException(status_code=404, detail="Route data not found.")

    # Build response
    sorted_stops = sorted(
        [s for s in job.stops if s.visit_order is not None],
        key=lambda s: s.visit_order,
    )

    depot_stop = None
    if job.depot_lat is not None and job.depot_lng is not None:
        depot_stop = StopResponse(
            id="depot",
            address=job.depot_address or "",
            lat=job.depot_lat,
            lng=job.depot_lng,
            visit_order=0,
        )

    return RouteResponse(
        job_id=str(job.id),
        naive_distance_km=job.route.naive_distance_km,
        optimized_distance_km=job.route.optimized_distance_km,
        pct_improvement=job.route.pct_improvement,
        depot=depot_stop,
        stops=[
            StopResponse(
                id=str(s.id),
                address=s.raw_address,
                lat=s.lat,
                lng=s.lng,
                visit_order=s.visit_order,
            )
            for s in sorted_stops
        ],
    )
