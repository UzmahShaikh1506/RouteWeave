"""
Unit tests for the RouteWeave API endpoints.

Uses FastAPI's TestClient with a SQLite backend
(no Postgres required for unit tests).

Tests cover:
  - Health endpoint
  - Job creation (valid/invalid CSV)
  - Job status retrieval
  - Rate limiting
  - Input validation
  - Job listing
  - OpenAPI docs accessibility
  - API-key authentication
  - Background task acceptance (202 response)
"""

import io
import os
import sys
import uuid
import asyncio

import pytest

# Ensure the api module is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

# Override DATABASE_URL BEFORE importing the app modules
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_routeweave.db"
os.environ["OPTIMIZER_URL"] = "http://localhost:9999"
# Ensure auth is disabled for baseline tests
os.environ.pop("API_KEY", None)

# Remove stale test DB
_test_db_path = os.path.join(os.getcwd(), "test_routeweave.db")
if os.path.exists(_test_db_path):
    try:
        os.remove(_test_db_path)
    except OSError:
        pass


@pytest.fixture(scope="module")
def client():
    """
    Module-scoped test client with the database tables initialized.

    We explicitly create the tables via `init_db()` before handing
    over the TestClient, ensuring all endpoints can hit a real
    (SQLite-backed) database.
    """
    from fastapi.testclient import TestClient
    from database import init_db
    from main import app

    # Run init_db() to create tables before any test
    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    loop.close()

    with TestClient(app) as c:
        yield c

    # Cleanup
    if os.path.exists(_test_db_path):
        try:
            os.remove(_test_db_path)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_200(self, client):
        """Health endpoint should return 200 with status info."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "api"
        assert data["database"] == "connected"


# ─────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────

class TestAuthentication:
    """Tests for API-key authentication on mutation endpoints."""

    def test_post_jobs_without_api_key_when_configured_returns_401(self, client, monkeypatch):
        """When API_KEY is set, missing X-API-Key header should return 401."""
        monkeypatch.setattr("main.API_KEY", "secret-test-key")

        csv_content = 'address\n"Test St, NYC"\n'
        csv_file = io.BytesIO(csv_content.encode('utf-8'))
        resp = client.post(
            "/jobs",
            files={"file": ("test.csv", csv_file, "text/csv")},
        )
        assert resp.status_code == 401
        assert "api key" in resp.json()["detail"].lower()

    def test_post_jobs_with_wrong_api_key_returns_401(self, client, monkeypatch):
        """When API_KEY is set, an invalid X-API-Key header should return 401."""
        monkeypatch.setattr("main.API_KEY", "secret-test-key")

        csv_content = 'address\n"Test St, NYC"\n'
        csv_file = io.BytesIO(csv_content.encode('utf-8'))
        resp = client.post(
            "/jobs",
            files={"file": ("test.csv", csv_file, "text/csv")},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_post_jobs_with_correct_api_key_returns_202(self, client, monkeypatch):
        """When API_KEY is set, a valid X-API-Key header should succeed."""
        monkeypatch.setattr("main.API_KEY", "secret-test-key")

        csv_content = 'address\n"Test St, NYC"\n'
        csv_file = io.BytesIO(csv_content.encode('utf-8'))
        resp = client.post(
            "/jobs",
            files={"file": ("test.csv", csv_file, "text/csv")},
            headers={"X-API-Key": "secret-test-key"},
        )
        assert resp.status_code == 202


# ─────────────────────────────────────────────────────────────
# Input Validation
# ─────────────────────────────────────────────────────────────

class TestInputValidation:
    """Tests for input validation on POST /jobs."""

    def test_missing_file_returns_422(self, client):
        """POST /jobs without a file should return 422."""
        response = client.post("/jobs")
        assert response.status_code == 422

    def test_csv_without_address_column_rejected(self, client, invalid_csv_content):
        """CSV without 'address' column should be rejected."""
        csv_file = io.BytesIO(invalid_csv_content.encode('utf-8'))
        response = client.post(
            "/jobs",
            files={"file": ("test.csv", csv_file, "text/csv")},
        )
        assert response.status_code == 422
        assert "address" in response.json()["detail"].lower()

    def test_empty_csv_rejected(self, client):
        """CSV with only headers should be rejected."""
        csv_content = "address\n"
        csv_file = io.BytesIO(csv_content.encode('utf-8'))
        response = client.post(
            "/jobs",
            files={"file": ("empty.csv", csv_file, "text/csv")},
        )
        assert response.status_code == 422

    def test_too_many_stops_rejected(self, client):
        """CSV with >50 stops should be rejected."""
        lines = ["address"] + [f'"Stop {i}, New York, NY"' for i in range(51)]
        csv_content = "\n".join(lines) + "\n"
        csv_file = io.BytesIO(csv_content.encode('utf-8'))
        response = client.post(
            "/jobs",
            files={"file": ("big.csv", csv_file, "text/csv")},
        )
        assert response.status_code == 422
        assert "50" in response.json()["detail"]


# ─────────────────────────────────────────────────────────────
# API Endpoints
# ─────────────────────────────────────────────────────────────

class TestAPIEndpoints:
    """Tests for core API endpoint behavior."""

    def test_get_nonexistent_job_returns_404(self, client):
        """GET /jobs/{id} with a random UUID should return 404."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/jobs/{fake_id}")
        assert response.status_code == 404

    def test_invalid_job_id_format(self, client):
        """GET /jobs/{id} with invalid format should return 422."""
        response = client.get("/jobs/not-a-uuid")
        assert response.status_code == 422

    def test_get_route_nonexistent_job(self, client):
        """GET /jobs/{id}/route for nonexistent job should return 404."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/jobs/{fake_id}/route")
        assert response.status_code == 404

    def test_docs_endpoint_accessible(self, client):
        """OpenAPI docs should be available."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_redoc_endpoint_accessible(self, client):
        """ReDoc docs should be available."""
        response = client.get("/redoc")
        assert response.status_code == 200

    def test_list_jobs_returns_200(self, client):
        """GET /jobs should return a valid empty job list."""
        response = client.get("/jobs")
        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data
        assert "total" in data
        assert isinstance(data["jobs"], list)

    def test_list_jobs_respects_limit(self, client):
        """GET /jobs?limit=5 should work."""
        response = client.get("/jobs?limit=5&offset=0")
        assert response.status_code == 200

    def test_create_job_returns_202_and_pending_status(self, client):
        """POST /jobs should accept the job and return 202 with pending status."""
        csv = "address\n\"Test Address 123, New York, NY\"\n"
        csv_file = io.BytesIO(csv.encode('utf-8'))
        resp = client.post(
            "/jobs",
            files={"file": ("test.csv", csv_file, "text/csv")},
            data={"depot_address": "Times Square, New York, NY"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "pending"
        assert "job_id" in data
        assert "message" in data

        # Background tasks run synchronously in TestClient, so by now the job
        # has likely failed (no real geocoder/optimizer in tests). We just
        # verify the status endpoint is reachable.
        job_id = data["job_id"]
        status_resp = client.get(f"/jobs/{job_id}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["id"] == job_id
        assert status_data["status"] in ("pending", "geocoding", "optimizing", "completed", "failed")

    def test_get_route_for_incomplete_job_returns_400(self, client):
        """Attempting to get route for a non-completed job should fail."""
        # Clear rate limiter to avoid 429 from prior tests
        from main import _rate_limit_store
        _rate_limit_store.clear()

        # Create a job; background task will likely fail in tests.
        csv = "address\n\"Test Address 123, New York, NY\"\n"
        csv_file = io.BytesIO(csv.encode('utf-8'))
        resp = client.post(
            "/jobs",
            files={"file": ("test.csv", csv_file, "text/csv")},
            data={"depot_address": "Times Square, New York, NY"},
        )
        assert resp.status_code == 202
        job_id = resp.json().get("job_id")

        if job_id:
            # By now the background task has run and likely failed;
            # either way status != "completed", so /route should return 400
            route_resp = client.get(f"/jobs/{job_id}/route")
            assert route_resp.status_code == 400


# ─────────────────────────────────────────────────────────────
# Rate Limiting
# ─────────────────────────────────────────────────────────────

class TestRateLimiting:
    """Tests for the rate limiter."""

    def test_rate_limit_allows_initial_requests(self):
        """Rate limiter should allow the first N requests."""
        from main import _check_rate_limit, _rate_limit_store

        test_ip = f"test_{uuid.uuid4().hex[:8]}"
        _rate_limit_store[test_ip] = []

        for i in range(5):
            assert _check_rate_limit(test_ip) is True, f"Request {i+1} should be allowed"

    def test_rate_limit_blocks_excess(self):
        """Rate limiter should block requests over the limit."""
        from main import _check_rate_limit, _rate_limit_store

        test_ip = f"test_{uuid.uuid4().hex[:8]}"
        _rate_limit_store[test_ip] = []

        # Exhaust the limit
        for _ in range(5):
            _check_rate_limit(test_ip)

        # 6th should be blocked
        assert _check_rate_limit(test_ip) is False

    def test_rate_limit_separate_ips(self):
        """Different IPs should have independent limits."""
        from main import _check_rate_limit, _rate_limit_store

        ip_a = f"test_a_{uuid.uuid4().hex[:8]}"
        ip_b = f"test_b_{uuid.uuid4().hex[:8]}"
        _rate_limit_store[ip_a] = []
        _rate_limit_store[ip_b] = []

        # Exhaust IP A
        for _ in range(5):
            _check_rate_limit(ip_a)

        # IP B should still work
        assert _check_rate_limit(ip_b) is True

        # IP A should be blocked
        assert _check_rate_limit(ip_a) is False


# ─────────────────────────────────────────────────────────────
# CSV Parsing Edge Cases
# ─────────────────────────────────────────────────────────────

class TestCSVParsing:
    """Tests for CSV parsing edge cases."""

    def _clear_rate_limit(self):
        """Clear the rate limiter to prevent interference from prior tests."""
        from main import _rate_limit_store
        _rate_limit_store.clear()

    def test_csv_with_bom_encoding(self, client):
        """CSV with UTF-8 BOM should parse correctly."""
        self._clear_rate_limit()
        # Write raw BOM bytes + CSV content (utf-8-sig encoding adds BOM automatically)
        csv_content = 'address\n"Central Park, New York, NY"\n'
        csv_file = io.BytesIO(csv_content.encode('utf-8-sig'))
        response = client.post(
            "/jobs",
            files={"file": ("bom.csv", csv_file, "text/csv")},
        )
        # Should accept the CSV (validation happens before background task)
        assert response.status_code == 202

    def test_csv_with_extra_whitespace(self, client):
        """CSV with whitespace in column names should still work."""
        self._clear_rate_limit()
        csv_content = ' address \n"Test St, NYC"\n'
        csv_file = io.BytesIO(csv_content.encode('utf-8'))
        response = client.post(
            "/jobs",
            files={"file": ("spaces.csv", csv_file, "text/csv")},
        )
        assert response.status_code == 202
