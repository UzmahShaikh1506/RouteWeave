"""
Optimizer microservice — exposes the route optimization engine
as an HTTP API for consumption by the main API service.

Runs as a separate container so it can be scaled or swapped
independently of the API layer.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from optimize import optimize_route

app = FastAPI(
    title="RouteWeave Optimizer",
    description="Internal route optimization microservice",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------

class StopIn(BaseModel):
    """A delivery stop with coordinates."""
    id: str = ""
    address: str = ""
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class OptimizeRequest(BaseModel):
    """Input payload for the /optimize endpoint."""
    depot: StopIn
    stops: list[StopIn] = Field(..., min_length=0, max_length=100)


class StopOut(BaseModel):
    """A stop in the optimized route with visit order."""
    id: str = ""
    address: str = ""
    lat: float
    lng: float
    visit_order: int


class OptimizeResponse(BaseModel):
    """Result of a route optimization run."""
    ordered_stops: list[StopOut]
    naive_distance_km: float
    optimized_distance_km: float
    pct_improvement: float


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
async def health():
    """Liveness check for Docker healthcheck and monitoring."""
    return {"status": "healthy", "service": "optimizer"}


@app.post(
    "/optimize",
    response_model=OptimizeResponse,
    tags=["optimization"],
    summary="Optimize a delivery route",
)
async def optimize(request: OptimizeRequest):
    """
    Accepts a depot location and a list of delivery stops.
    Returns the stops in optimized visit order with distance metrics.
    """
    try:
        depot = request.depot.model_dump()
        stops = [s.model_dump() for s in request.stops]

        result = optimize_route(depot, stops)

        return OptimizeResponse(**result)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Optimization failed: {str(e)}",
        )
