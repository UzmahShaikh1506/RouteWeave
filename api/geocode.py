"""
Geocoding service using OpenStreetMap Nominatim.

Converts raw street addresses to (latitude, longitude) coordinates.
Rate-limited to 1 request/second per Nominatim usage policy.
Includes retry logic with exponential backoff for transient errors.

Performance optimization:
  - Optional database-backed geocoding cache to avoid redundant Nominatim calls.
"""

import asyncio
import hashlib
import logging
import time

import httpx
from sqlalchemy import select, update

logger = logging.getLogger(__name__)

# Nominatim requires a valid User-Agent identifying the application.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "RouteWeave/1.0 (delivery-route-optimizer)"
HEADERS = {"User-Agent": USER_AGENT}

# Rate limiting: minimum interval between requests (seconds)
MIN_REQUEST_INTERVAL = 1.1  # slightly over 1s to stay safely within policy
_last_request_time: float = 0.0
_rate_lock = asyncio.Lock()

# Retry configuration
MAX_RETRIES = 3
BASE_BACKOFF = 2.0  # seconds


def _normalize_address(address: str) -> str:
    """Normalize an address string for consistent cache keys."""
    return address.strip().lower()


def _address_hash(address: str) -> str:
    """Return SHA-256 hex digest of the normalized address."""
    return hashlib.sha256(_normalize_address(address).encode("utf-8")).hexdigest()


async def _enforce_rate_limit():
    """
    Ensure at least MIN_REQUEST_INTERVAL seconds have passed
    since the last Nominatim request. Thread-safe via asyncio lock.
    """
    global _last_request_time
    async with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            await asyncio.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.monotonic()


async def _lookup_cache(
    address: str,
    db_session,
):
    """Check the geocode cache for a previously resolved address."""
    from models import GeocodeCache  # noqa: E402

    addr_hash = _address_hash(address)
    result = await db_session.execute(
        select(GeocodeCache).where(GeocodeCache.address_hash == addr_hash)
    )
    cached = result.scalar_one_or_none()
    if cached:
        cached.hit_count += 1
        await db_session.commit()
        logger.info("Cache hit for '%s' → (%.6f, %.6f) [hits=%d]", address, cached.lat, cached.lng, cached.hit_count)
        return (cached.lat, cached.lng)
    return None


async def _save_cache(
    address: str,
    lat: float,
    lng: float,
    db_session,
):
    """Persist a geocoding result to the cache."""
    from models import GeocodeCache  # noqa: E402

    addr_hash = _address_hash(address)
    cache_entry = GeocodeCache(
        id=__import__("uuid").uuid4(),
        address_hash=addr_hash,
        raw_address=address.strip(),
        lat=lat,
        lng=lng,
    )
    db_session.add(cache_entry)
    await db_session.commit()
    logger.info("Cached '%s' → (%.6f, %.6f)", address, lat, lng)


async def geocode_address(
    address: str,
    client: httpx.AsyncClient | None = None,
    db_session=None,
) -> tuple[float, float] | None:
    """
    Geocode a single address using Nominatim, with optional DB cache.

    Args:
        address: The raw street address to geocode.
        client: Optional httpx.AsyncClient for connection reuse.
        db_session: Optional SQLAlchemy async session for cache lookup/persistence.

    Returns:
        A (latitude, longitude) tuple, or None if geocoding failed.
    """
    # --- Cache lookup ---
    if db_session:
        cached = await _lookup_cache(address, db_session)
        if cached:
            return cached

    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "addressdetails": 0,
    }

    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        should_close = True

    try:
        for attempt in range(MAX_RETRIES):
            try:
                await _enforce_rate_limit()

                response = await client.get(
                    NOMINATIM_URL,
                    params=params,
                    headers=HEADERS,
                )
                response.raise_for_status()

                results = response.json()
                if results and len(results) > 0:
                    lat = float(results[0]["lat"])
                    lng = float(results[0]["lon"])
                    logger.info(
                        "Geocoded '%s' → (%.6f, %.6f)", address, lat, lng
                    )
                    # --- Cache persistence ---
                    if db_session:
                        await _save_cache(address, lat, lng, db_session)
                    return (lat, lng)
                else:
                    logger.warning("No results for address: '%s'", address)
                    return None

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    # Rate limited — back off more aggressively
                    wait = BASE_BACKOFF * (attempt + 1) * 2
                    logger.warning(
                        "Rate limited by Nominatim, waiting %.1fs (attempt %d/%d)",
                        wait, attempt + 1, MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                elif e.response.status_code >= 500:
                    wait = BASE_BACKOFF * (attempt + 1)
                    logger.warning(
                        "Nominatim server error %d, retrying in %.1fs (attempt %d/%d)",
                        e.response.status_code, wait, attempt + 1, MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "Nominatim HTTP error %d for '%s': %s",
                        e.response.status_code, address, str(e),
                    )
                    return None

            except (httpx.RequestError, httpx.TimeoutException) as e:
                wait = BASE_BACKOFF * (attempt + 1)
                logger.warning(
                    "Network error geocoding '%s': %s — retrying in %.1fs (attempt %d/%d)",
                    address, str(e), wait, attempt + 1, MAX_RETRIES,
                )
                await asyncio.sleep(wait)

        logger.error("Failed to geocode '%s' after %d attempts", address, MAX_RETRIES)
        return None

    finally:
        if should_close:
            await client.aclose()


async def geocode_addresses(
    addresses: list[str],
    db_session=None,
) -> list[tuple[float, float] | None]:
    """
    Geocode a batch of addresses sequentially (to respect rate limits).
    Optional DB session enables caching across the batch.

    Args:
        addresses: List of raw street address strings.
        db_session: Optional SQLAlchemy async session for caching.

    Returns:
        List of (lat, lng) tuples or None for each address.
    """
    results = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for address in addresses:
            coords = await geocode_address(address, client=client, db_session=db_session)
            results.append(coords)
    return results
