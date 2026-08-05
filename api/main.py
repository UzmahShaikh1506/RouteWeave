"""
RouteWeave API — Main application entry point.

Provides endpoints for creating optimization jobs (CSV upload),
polling job status, retrieving optimized routes, real-time SSE updates,
admin analytics, and GPX export.

Security features:
  - In-memory per-IP rate limiting on POST /jobs
  - Input validation via Pydantic models
  - CORS restricted to known origins (configurable via env)
  - Parameterized SQL queries (SQLAlchemy ORM)
  - Optional API-key authentication on mutation endpoints
  - Optional admin-key authentication on management endpoints
"""

import asyncio
import csv
import io
import json
import logging
import os
import time
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone

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
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_session, init_db
from geocode import geocode_address
from models import (
    GeocodeCache,
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
ADMIN_KEY = os.getenv("ADMIN_KEY")

# SECURITY: When API_KEY is enabled, you MUST terminate TLS at a reverse proxy.

# CORS origins — override in production via comma-separated env var
_cors_raw = os.getenv("CORS_ORIGINS", "")
if _cors_raw.strip():
    ALLOWED_ORIGINS = [o.strip() for o in _cors_raw.split(",") if o.strip()]
else:
    ALLOWED_ORIGINS = [
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8080",
    ]

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
    """
    if not API_KEY:
        return  # auth disabled in dev

    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
        )


async def require_admin_key(x_admin_key: str = Header(None)) -> None:
    """
    Enforce admin-key authentication when ADMIN_KEY is configured.
    """
    if not ADMIN_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is disabled. Set ADMIN_KEY to enable.",
        )

    if x_admin_key != ADMIN_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin key.",
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
    Uses database-backed geocoding cache for performance.
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

            # Geocode depot (with cache)
            actual_depot_address = job.depot_address or (stops[0].raw_address if stops else "")
            async with httpx.AsyncClient(timeout=10.0) as http_client:
                depot_coords = await geocode_address(
                    actual_depot_address, client=http_client, db_session=session
                )
                if depot_coords is None:
                    job.status = "failed"
                    job.error_message = f"Could not geocode depot address: {actual_depot_address}"
                    await session.commit()
                    logger.error("Job %s failed: depot geocoding error", job_id)
                    return

                job.depot_lat = depot_coords[0]
                job.depot_lng = depot_coords[1]
                await session.commit()

                # Geocode stops (with cache)
                geocoded_stops = []
                for stop in stops:
                    coords = await geocode_address(stop.raw_address, client=http_client, db_session=session)
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
    version="1.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_STOPS = 50
MAX_CSV_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB (must match Nginx client_max_body_size)


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
    The job is accepted immediately and processed asynchronously.
    Poll SSE /jobs/{job_id}/events or GET /jobs/{job_id} for status updates.
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
    ):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid file type: {file.content_type}. Expected CSV.",
        )

    # --- Parse CSV ---
    try:
        content = await file.read()
        if len(content) > MAX_CSV_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large ({len(content)} bytes). Maximum is {MAX_CSV_SIZE_BYTES // 1024 // 1024} MB.",
            )
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))

        if reader.fieldnames is None or "address" not in [
            f.strip().lower() for f in reader.fieldnames
        ]:
            raise HTTPException(
                status_code=422,
                detail="CSV must contain an 'address' column.",
            )

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
        "message": "Job accepted. Poll GET /jobs/{job_id} or connect to SSE /jobs/{job_id}/events for status updates.",
    }


@app.get("/jobs/{job_id}/events", tags=["jobs"])
async def job_events(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Server-Sent Events (SSE) endpoint for real-time job status updates.
    The client receives a JSON event every 1.5 seconds until the job
    reaches a terminal state (completed or failed).
    """
    try:
        uid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid job ID format.")

    # Verify job exists
    result = await session.execute(select(Job).where(Job.id == uid))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    async def _event_generator():
        """Yield SSE events until job is terminal."""
        while True:
            async with async_session() as inner_session:
                result = await inner_session.execute(
                    select(Job).where(Job.id == uid)
                )
                current = result.scalar_one_or_none()
                if current is None:
                    payload = {"status": "not_found", "error": "Job was deleted."}
                    yield f"data: {json.dumps(payload)}\n\n"
                    break

                payload = {
                    "status": current.status,
                    "error_message": current.error_message,
                    "updated_at": current.created_at.isoformat() if current.created_at else None,
                }
                yield f"data: {json.dumps(payload)}\n\n"

                if current.status in ("completed", "failed"):
                    break

            await asyncio.sleep(1.5)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering for SSE
        },
    )


@app.get("/jobs", response_model=JobListResponse, tags=["jobs"])
async def list_jobs(
    limit: int = 20,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """List recent optimization jobs, newest first."""
    count_result = await session.execute(select(func.count()).select_from(Job))
    total = count_result.scalar() or 0

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


@app.delete("/jobs/{job_id}", tags=["jobs"], status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: str,
    x_admin_key: str = Header(None),
    session: AsyncSession = Depends(get_session),
):
    """
    Delete a job and all associated stops/routes.
    Requires admin key when ADMIN_KEY is configured.
    """
    await require_admin_key(x_admin_key)

    try:
        uid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid job ID format.")

    result = await session.execute(select(Job).where(Job.id == uid))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    await session.delete(job)
    await session.commit()
    logger.info("Admin deleted job %s", job_id)
    return None


@app.get("/jobs/{job_id}/route", response_model=RouteResponse, tags=["jobs"])
async def get_route(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get the optimized route for a completed job."""
    try:
        uid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid job ID format.")

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


@app.get("/jobs/{job_id}/route.gpx", tags=["jobs"])
async def export_gpx(
    job_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Export a completed route as a GPX (GPS Exchange Format) file.
    Compatible with Garmin, Google Earth, Strava, and other GPS tools.
    """
    try:
        uid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid job ID format.")

    result = await session.execute(
        select(Job)
        .options(selectinload(Job.stops), selectinload(Job.route))
        .where(Job.id == uid)
    )
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Job is not completed.")
    if job.route is None:
        raise HTTPException(status_code=404, detail="Route data not found.")

    # Build GPX XML
    sorted_stops = sorted(
        [s for s in job.stops if s.visit_order is not None],
        key=lambda s: s.visit_order,
    )

    gpx = ET.Element("gpx", {
        "version": "1.1",
        "creator": "RouteWeave",
        "xmlns": "http://www.topografix.com/GPX/1/1",
    })

    trk = ET.SubElement(gpx, "trk")
    ET.SubElement(trk, "name").text = f"RouteWeave Optimized Route — {job_id}"
    ET.SubElement(trk, "desc").text = (
        f"Naive: {job.route.naive_distance_km:.2f} km | "
        f"Optimized: {job.route.optimized_distance_km:.2f} km | "
        f"Saved: {job.route.pct_improvement:.1f}%"
    )

    trkseg = ET.SubElement(trk, "trkseg")

    # Depot waypoint
    if job.depot_lat is not None and job.depot_lng is not None:
        trkpt = ET.SubElement(trkseg, "trkpt", {
            "lat": str(job.depot_lat),
            "lon": str(job.depot_lng),
        })
        ET.SubElement(trkpt, "name").text = "Depot"
        ET.SubElement(trkpt, "desc").text = job.depot_address or ""

    # Stop waypoints
    for stop in sorted_stops:
        if stop.lat is not None and stop.lng is not None:
            trkpt = ET.SubElement(trkseg, "trkpt", {
                "lat": str(stop.lat),
                "lon": str(stop.lng),
            })
            ET.SubElement(trkpt, "name").text = f"Stop {stop.visit_order}"
            ET.SubElement(trkpt, "desc").text = stop.raw_address

    xml_str = ET.tostring(gpx, encoding="unicode")
    return StreamingResponse(
        io.StringIO(xml_str),
        media_type="application/gpx+xml",
        headers={
            "Content-Disposition": f'attachment; filename="routeweave_{job_id}.gpx"'
        },
    )


# ---------------------------------------------------------------------------
# Admin / Analytics Endpoints
# ---------------------------------------------------------------------------

@app.get("/admin/stats", tags=["admin"])
async def admin_stats(
    x_admin_key: str = Header(None),
    session: AsyncSession = Depends(get_session),
):
    """
    Admin dashboard statistics. Returns high-level system metrics.
    Requires ADMIN_KEY header.
    """
    await require_admin_key(x_admin_key)

    # Total jobs
    total_jobs = await session.execute(select(func.count()).select_from(Job))
    total = total_jobs.scalar() or 0

    # Status breakdown
    status_counts = {}
    for st in ("pending", "geocoding", "optimizing", "completed", "failed"):
        cnt = await session.execute(select(func.count()).select_from(Job).where(Job.status == st))
        status_counts[st] = cnt.scalar() or 0

    # Completed route stats
    routes_result = await session.execute(
        select(
            func.count(Route.id),
            func.avg(Route.pct_improvement),
            func.sum(Route.naive_distance_km),
            func.sum(Route.optimized_distance_km),
        )
    )
    route_stats = routes_result.one_or_none()
    total_completed = int(route_stats[0]) if route_stats else 0
    avg_improvement = float(route_stats[1]) if route_stats and route_stats[1] else 0.0
    total_naive_km = float(route_stats[2]) if route_stats and route_stats[2] else 0.0
    total_optimized_km = float(route_stats[3]) if route_stats and route_stats[3] else 0.0
    total_distance_saved = total_naive_km - total_optimized_km

    # Cache stats
    cache_result = await session.execute(select(func.count()).select_from(GeocodeCache))
    cache_count = cache_result.scalar() or 0

    cache_hits = await session.execute(
        select(func.sum(GeocodeCache.hit_count))
    )
    total_cache_hits = cache_hits.scalar() or 0

    return {
        "jobs": {
            "total": total,
            "by_status": status_counts,
            "completed": total_completed,
        },
        "routes": {
            "avg_improvement_pct": round(avg_improvement, 2),
            "total_naive_km": round(total_naive_km, 2),
            "total_optimized_km": round(total_optimized_km, 2),
            "total_distance_saved_km": round(total_distance_saved, 2),
        },
        "geocoding_cache": {
            "cached_addresses": cache_count,
            "total_hits": int(total_cache_hits),
        },
        "system": {
            "api_version": "1.2.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


@app.get("/admin/jobs", tags=["admin"])
async def admin_list_jobs(
    x_admin_key: str = Header(None),
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """
    Admin job listing with filtering. Returns full job details including routes.
    Requires ADMIN_KEY header.
    """
    await require_admin_key(x_admin_key)

    query = select(Job).options(selectinload(Job.stops), selectinload(Job.route))
    if status:
        query = query.where(Job.status == status)

    query = query.order_by(Job.created_at.desc()).limit(min(limit, 100)).offset(offset)
    result = await session.execute(query)
    jobs = result.scalars().all()

    return {
        "jobs": [
            {
                "id": str(j.id),
                "status": j.status,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "depot_address": j.depot_address,
                "stop_count": len(j.stops),
                "route": {
                    "naive_distance_km": j.route.naive_distance_km,
                    "optimized_distance_km": j.route.optimized_distance_km,
                    "pct_improvement": j.route.pct_improvement,
                } if j.route else None,
            }
            for j in jobs
        ],
        "count": len(jobs),
    }


@app.get("/analytics/overview", tags=["analytics"])
async def analytics_overview(
    session: AsyncSession = Depends(get_session),
):
    """
    Public system-wide analytics. No auth required.
    Great for portfolio demos and dashboards.
    """
    total_jobs = await session.execute(select(func.count()).select_from(Job))
    total = total_jobs.scalar() or 0

    completed_jobs = await session.execute(
        select(func.count()).select_from(Job).where(Job.status == "completed")
    )
    completed = completed_jobs.scalar() or 0

    routes_result = await session.execute(
        select(
            func.avg(Route.pct_improvement),
            func.sum(Route.naive_distance_km),
            func.sum(Route.optimized_distance_km),
        )
        .join(Job, Route.job_id == Job.id)
        .where(Job.status == "completed")
    )
    route_stats = routes_result.one_or_none()
    avg_improvement = float(route_stats[0]) if route_stats and route_stats[0] else 0.0
    total_naive = float(route_stats[1]) if route_stats and route_stats[1] else 0.0
    total_optimized = float(route_stats[2]) if route_stats and route_stats[2] else 0.0

    # Recent activity (last 24h)
    from datetime import timedelta
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_jobs = await session.execute(
        select(func.count()).select_from(Job).where(Job.created_at >= day_ago)
    )
    recent = recent_jobs.scalar() or 0

    return {
        "total_jobs": total,
        "completed_jobs": completed,
        "success_rate_pct": round((completed / total * 100), 2) if total > 0 else 0.0,
        "avg_improvement_pct": round(avg_improvement, 2),
        "total_distance_saved_km": round(total_naive - total_optimized, 2),
        "jobs_last_24h": recent,
    }
