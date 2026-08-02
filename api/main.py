"""
RouteWeave API — Main application entry point.

Provides endpoints for creating optimization jobs (CSV upload),
polling job status, and retrieving optimized routes.

Security features:
  - In-memory per-IP rate limiting on POST /jobs
  - Input validation via Pydantic models
  - CORS restricted to known origins
  - Parameterized SQL queries (SQLAlchemy ORM)
  - Optional API-key authentication on mutation endpoints
"""

import csv
import io
import logging
import os
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
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
# Configuration
# ---------------------------------------------------------------------------

OPTIMIZER_URL = os.getenv("OPTIMIZER_URL", "http://optimizer:8001")
API_KEY = os.getenv("API_KEY")

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
# Authentication
# ---------------------------------------------------------------------------

async def require_api_key(x_api_key: str = Header(None)) -> None:
    """
    Enforce API-key authentication when API_KEY is configured.

    In development, omit API_KEY from the environment to disable auth.
    In production, set API_KEY and require clients to pass it via
    the X-API-Key header.
    """
    if not API_KEY:
        return  # auth disabled in dev

    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
        )


# ---------------------------------------------------------------------------
# Optimizer service client
# ---------------------------------------------------------------------------

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
# Background Job Processor
# ---------------------------------------------------------------------------

from database import async_session  # noqa: E402


async def _process_job(job_id: uuid.UUID) -> None:
    """
    Background worker: geocode addresses and run route optimization.

    This function receives a job_id, creates its own database session,
    and transitions the job through: pending → geocoding → optimizing → completed | failed.
    """
    async with async_session() as session:
        try:
            # Fetch job
            result = await session.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()
            if job is None:
                logger.error("Background worker: job %s not found", job_id)
                return

            # Update status to geocoding
            job.status = "geocoding"
            await session.commit()

            # Fetch stops
            result = await session.execute(select(Stop).where(Stop.job_id == job.id))
            stops = result.scalars().all()

            # Geocode depot
            actual_depot_address = job.depot_address or (stops[0].raw_address if stops else "")
            async with httpx.AsyncClient(timeout=10.0) as http_client:
                depot_coords = await geocode_address(actual_depot_address, client=http_client)
                if depot_coords is None:
                    job.status = "failed"
                    job.error_message = f"Could not geocode depot address: {actual_depot_address}"
                    await session.commit()
                    logger.error("Job %s failed: depot geocoding error", job_id)
                    return

                job.depot_lat = depot_coords[0]
                job.depot_lng = depot_coords[1]
                await session.commit()

                # Geocode stops
                geocoded_stops = []
                for stop in stops:
                    coords = await geocode_address(stop.raw_address, client=http_client)
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
                logger.error("Job %s failed: no stops geocoded", job_id)
                return

            # Update status to optimizing
            job.status = "optimizing"
            await session.commit()

            # Call optimizer service
            depot_dict = {
                "id": "depot",
                "address": actual_depot_address,
                "lat": job.depot_lat,
                "lng": job.depot_lng,
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

        except Exception as e:
            logger.error("Job %s failed: %s", job_id, str(e), exc_info=True)
            try:
                result = await session.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.error_message = str(e)
                    await session.commit()
            except Exception as inner:
                logger.error("Failed to mark job %s as failed: %s", job_id, inner)


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
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow the client (Nginx on port 3000) and local dev
# NOTE: "*" is NOT used when allow_credentials=True (browser security restriction)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8080",
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


@app.post(
    "/jobs",
    tags=["jobs"],
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    depot_address: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """
    Create a new optimization job by uploading a CSV of delivery addresses.

    The CSV must have an 'address' column. Each row is a delivery stop.
    Optionally provide a depot_address (the starting location).
    If no depot is provided, the first address in the CSV is used.

    The job is accepted immediately and processed asynchronously in the
    background. Poll `GET /jobs/{job_id}` to track status.

    Rate-limited to 5 requests per minute per IP.
    Maximum 50 stops per job.
    Requires X-API-Key header when API_KEY is configured.
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

    # --- Offload processing to background worker ---
    background_tasks.add_task(_process_job, job.id)

    return {
        "job_id": job_id,
        "status": "pending",
        "message": "Job accepted. Poll GET /jobs/{job_id} for status updates.",
    }


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
