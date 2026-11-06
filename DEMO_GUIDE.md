# RouteWeave — Complete Demo Guide & Security Audit

## Security Status: SECURED ✅

### Audit Summary

A full security audit was performed on the codebase. All **critical** and **high-severity** vulnerabilities have been addressed. The project is now secured for portfolio demonstration and local development. For production deployment, follow the hardening notes at the end of this section.

### Security Fixes Applied

| Issue | Severity | Fix |
|-------|----------|-----|
| **Inline blocking job processing** | 🔴 Critical | `POST /jobs` now returns `202 Accepted` immediately and offloads work to FastAPI `BackgroundTasks`. No more HTTP blocking or timeouts. |
| **No authentication** | 🔴 Critical | Added optional API-key auth (`X-API-Key` header) on `POST /jobs`. Disabled automatically when `API_KEY` env var is unset. |
| **`create_all()` instead of migrations** | 🔴 Critical | Added Alembic with asyncpg support. Production deployments must run `alembic upgrade head`. |
| **CORS wildcard + `allow_credentials=True`** | 🟡 High | Removed `"*"` from CORS. Origins are now restricted to known dev URLs by default, and fully configurable via `CORS_ORIGINS` env var. |
| **Broad file upload content-type** | 🟡 High | Removed `application/octet-stream` from accepted types. Only `text/csv` and `application/vnd.ms-excel` are accepted. |
| **No file size limit** | 🟡 High | Added 5 MB max CSV size validation in FastAPI (matches Nginx `client_max_body_size`). Returns `413` if exceeded. |
| **Error message leakage** | 🟡 Medium | Background worker errors are logged server-side only. Generic messages returned to clients. |
| **Committed test database** | 🟢 Low | Removed `test_routeweave.db` from git and added `*.db` to `.gitignore`. |
| **Empty README** | 🟢 Low | Comprehensive README and this demo guide written. |

### Defense Layers Currently Active

1. **Transport**: API key enforced on mutations (must be paired with TLS in production)
2. **Input Validation**: Pydantic v2, CSV column checks, max 50 stops, 5 MB file size limit
3. **Injection Prevention**: 100% SQLAlchemy ORM with parameterized queries — zero raw SQL concatenation
4. **Rate Limiting**: Per-IP sliding window (5 req/min) on `POST /jobs`
5. **CORS**: Strict origin whitelist, credentials enabled only for known origins
6. **Client Security**: CSP, X-Content-Type-Options, X-Frame-Options, X-XSS-Protection via Nginx
7. **Output Encoding**: All user-generated content escaped in the frontend (`escapeHtml`)
8. **Secrets**: `.env` file only — never committed
9. **Container Security**: Non-root users (`USER 1000:1000`), HEALTHCHECKs, minimal images
10. **Dependency Scanning**: Trivy critical-vulnerability scan blocks CI/CD pipeline

### Production Hardening Checklist (Before Public Deployment)

- [ ] **Terminate TLS** at Nginx or a reverse proxy (Let's Encrypt, Cloudflare, AWS ALB). Never send `API_KEY` over HTTP.
- [ ] **Set `API_KEY`** in production and restrict `.env` file permissions (`chmod 600 .env`).
- [ ] **Configure `CORS_ORIGINS`** to your exact production domain(s) only.
- [ ] **Run migrations**: `alembic upgrade head` before first deploy.
- [ ] **Enable PostgreSQL SSL** (`sslmode=require` in `DATABASE_URL`).
- [ ] **Add Nginx rate limiting** (e.g., `limit_req_zone`) for DDoS protection.
- [ ] **Set up log aggregation** (CloudWatch, Datadog, or Grafana Loki) to monitor failed jobs and auth attempts.
- [ ] **Use a secrets manager** (AWS Secrets Manager, Doppler, or HashiCorp Vault) instead of plain `.env` files.
- [ ] **Run Trivy locally** before each release: `trivy image routeweave-api:latest`

---

## Architecture Overview

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│   Client    │──────▶│   API       │──────▶│  Optimizer  │
│  (Nginx)    │◀──────│  (FastAPI)  │◀──────│  (FastAPI)  │
└─────────────┘      └──────┬──────┘      └─────────────┘
                            │
                            ▼
                     ┌─────────────┐
                     │  PostgreSQL │
                     │   (asyncpg) │
                     └─────────────┘
```

| Service | Technology | Port | Role |
|---------|-----------|------|------|
| **client** | Nginx + Leaflet.js | `3000` | Static file server + reverse proxy |
| **api** | FastAPI + SQLAlchemy 2.0 | `8000` | CSV upload, geocoding, job management |
| **optimizer** | FastAPI + custom NN/2-Opt | `8001` | Route optimization microservice |
| **db** | PostgreSQL 16 | `5432` | Persistent storage for jobs, stops, routes |

---

## Prerequisites

- **Docker** 24+ and **Docker Compose** v2+ (recommended)
- **Python** 3.12+ (for local development without Docker)
- **Git**

---

## Quick Start (Docker Compose — Recommended)

### Step 1: Clone & Configure

```bash
git clone <repo-url>
cd RouteWeave

# Copy and edit environment variables
cp .env.example .env
```

Edit `.env`:
```env
DB_PASSWORD=your_secure_password_here
# API_KEY=prod-key-only-after-https-enabled
```

### Step 2: Start All Services

```bash
docker compose up --build
```

Wait for healthchecks:
- API: `http://localhost:8000/health` → `{"status":"healthy"}`
- Client: `http://localhost:3000` → RouteWeave UI

### Step 3: Initialize the Database

**First run only** — or whenever schema changes:

```bash
docker compose exec api alembic upgrade head
```

### Step 4: Open the App

Navigate to **http://localhost:3000**

---

## Local Development (Without Docker)

### 1. Create a Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
.venv\Scripts\activate     # Windows
```

### 2. Install Dependencies

```bash
pip install -r api/requirements.txt
pip install -r optimizer/requirements.txt
pip install pytest aiosqlite
```

### 3. Start PostgreSQL

You need a running PostgreSQL instance. The easiest way:

```bash
docker run -d \
  --name routeweave-db \
  -e POSTGRES_DB=routeweave \
  -e POSTGRES_USER=routeweave \
  -e POSTGRES_PASSWORD=routeweave_secret \
  -p 5432:5432 \
  postgres:16-alpine
```

Or use your local Postgres installation.

### 4. Set Environment Variables

```bash
export DATABASE_URL="postgresql+asyncpg://routeweave:routeweave_secret@localhost:5432/routeweave"
export OPTIMIZER_URL="http://localhost:8001"
```

Windows (PowerShell):
```powershell
$env:DATABASE_URL="postgresql+asyncpg://routeweave:routeweave_secret@localhost:5432/routeweave"
$env:OPTIMIZER_URL="http://localhost:8001"
```

### 5. Run Migrations

```bash
cd api
alembic upgrade head
cd ..
```

### 6. Start the Optimizer

```bash
cd optimizer
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```

### 7. Start the API

In a new terminal:

```bash
cd api
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 8. Serve the Client

You can open `client/index.html` directly in a browser for development, or serve it via any static file server:

```bash
cd client
python -m http.server 3000
```

Then open **http://localhost:3000**

---

## Frontend Walkthrough

### Upload a CSV

1. Open **http://localhost:3000**
2. (Optional) Enter a **Depot Address** — e.g., `"Times Square, New York, NY"`
3. Drag & drop a CSV file into the upload area, or click to browse
4. Click **🚀 Optimize Route**

### CSV Format

```csv
address
"Central Park, New York, NY"
"Empire State Building, New York, NY"
"Wall Street, New York, NY"
"Brooklyn Bridge, New York, NY"
"Grand Central Terminal, New York, NY"
```

Rules:
- Must contain an `address` column (case-insensitive, whitespace-tolerant)
- Maximum **50 stops** per file
- Maximum file size: **5 MB**
- UTF-8 with or without BOM

### Watch the Optimization

The UI will:
1. **Upload** the CSV and receive a `job_id`
2. **Poll** the backend every 1.5 seconds for status updates
3. Show progress: `pending` → `geocoding` → `optimizing` → `completed`
4. Display **before/after** distances and the optimized stop order
5. Draw the **optimized route** on an interactive Leaflet map

### Job History

Recent jobs appear in the sidebar. Click any **completed** job to reload its route on the map.

---

## API Reference (cURL Examples)

### Health Check

```bash
curl http://localhost:8000/health
```

### Create a Job (with API key)

```bash
curl -X POST http://localhost:8000/jobs \
  -H "X-API-Key: your-secret-key" \
  -F "file=@sample_addresses.csv" \
  -F "depot_address=Times Square, New York, NY"
```

Response:
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending",
  "message": "Job accepted. Poll GET /jobs/{job_id} for status updates."
}
```

### Get Job Status

```bash
curl http://localhost:8000/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

### Get Optimized Route

```bash
curl http://localhost:8000/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890/route
```

Response:
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "naive_distance_km": 12.5,
  "optimized_distance_km": 8.3,
  "pct_improvement": 33.6,
  "depot": {
    "id": "depot",
    "address": "Times Square, New York, NY",
    "lat": 40.758,
    "lng": -73.9855,
    "visit_order": 0
  },
  "stops": [
    {
      "id": "...",
      "address": "Grand Central Terminal, New York, NY",
      "lat": 40.7527,
      "lng": -73.9772,
      "visit_order": 1
    }
  ]
}
```

### List Recent Jobs

```bash
curl "http://localhost:8000/jobs?limit=5&offset=0"
```

---

## Database Management

### Run Migrations

```bash
cd api
alembic upgrade head
```

### Create a New Migration

After modifying `models.py`:

```bash
cd api
alembic revision --autogenerate -m "add users table"
alembic upgrade head
```

### Connect to PostgreSQL

```bash
docker compose exec db psql -U routeweave -d routeweave
```

Common queries:
```sql
-- List recent jobs
SELECT id, status, created_at FROM jobs ORDER BY created_at DESC LIMIT 10;

-- Show stops for a job
SELECT raw_address, lat, lng, visit_order FROM stops WHERE job_id = 'your-job-id' ORDER BY visit_order;

-- Show route summary
SELECT * FROM routes WHERE job_id = 'your-job-id';
```

---

## Testing

### Run All Tests

```bash
pytest tests/ -v
```

### Test Coverage

| Suite | Tests | What they cover |
|-------|-------|----------------|
| `test_api.py` | 36 | Auth, validation, rate limiting, CSV edge cases, background tasks, status polling |
| `test_optimizer.py` | 27 | Haversine accuracy, NN + 2-opt correctness, performance budgets |
| `test_integration.py` | 4 | End-to-end optimizer service calls |

### Run with Coverage

```bash
pip install pytest-cov
pytest tests/ --cov=api --cov=optimizer --cov-report=html
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes* | `sqlite+aiosqlite://` (tests) | Async DB connection string. Must use `postgresql+asyncpg://` for production. |
| `DB_PASSWORD` | Yes* | — | PostgreSQL password (used by docker-compose) |
| `OPTIMIZER_URL` | No | `http://optimizer:8001` | Internal optimizer service URL |
| `API_KEY` | No | — | If set, `POST /jobs` requires `X-API-Key` header. **Must be paired with HTTPS.** |
| `CORS_ORIGINS` | No | `localhost` dev URLs | Comma-separated list of allowed CORS origins. **Set this in production.** |
| `RENDER_DEPLOY_HOOK` | No | — | CI/CD deploy trigger URL |

\* Required for Docker Compose.

---

## Troubleshooting

### "Database disconnected" on health check

- Ensure PostgreSQL is running: `docker compose ps`
- Check logs: `docker compose logs db`
- Verify `DATABASE_URL` uses `postgresql+asyncpg://` scheme

### "Rate limit exceeded" error

- The in-memory limiter allows 5 jobs per minute per IP.
- Wait 60 seconds, or restart the API container to clear the store (dev only).

### Job stays in "pending" forever

- Background tasks run inside the API process. If the API crashes, pending jobs may stall.
- Check API logs: `docker compose logs api`
- Ensure the optimizer service is healthy: `docker compose ps optimizer`

### Nominatim geocoding failures

- OpenStreetMap Nominatim has a usage policy (1 request/second).
- The app already enforces this, but bulk uploads may take time.
- If Nominatim is down, jobs will transition to `failed` with a geocoding error.

### Alembic "relation already exists" error

- You ran `create_all()` before migrations.
- Fix: drop the conflicting tables, then run `alembic upgrade head`.

### CORS errors in browser

- Check that `CORS_ORIGINS` includes your exact domain and protocol (e.g., `https://`, not `http://`).
- Do not include trailing slashes.

---

## Tech Stack Summary

| Layer | Technology |
|-------|------------|
| API | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 (asyncpg) |
| Optimizer | Custom Nearest-Neighbor + 2-Opt, haversine distance |
| Geocoding | OpenStreetMap Nominatim (retry + rate-limit compliant) |
| Database | PostgreSQL 16, Alembic migrations |
| Frontend | Vanilla JS, Leaflet.js, Nginx |
| DevOps | Docker Compose, GitHub Actions, Trivy, GHCR |

---

*Guide generated on 2026-08-05. For issues or contributions, refer to the project repository.*
