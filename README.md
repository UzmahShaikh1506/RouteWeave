# RouteWeave

> **Last-mile delivery route optimizer** — upload a CSV of addresses, geocode via OpenStreetMap Nominatim, run Nearest-Neighbor + 2-Opt optimization, and visualize before/after routes on an interactive Leaflet map.

---

## Architecture

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

| Service | Role | Port |
|---------|------|------|
| **client** | Nginx static file server (Leaflet map) | `3000` |
| **api** | FastAPI async backend — CSV ingestion, geocoding, job management | `8000` |
| **optimizer** | FastAPI microservice — NN + 2-Opt route optimization | `8001` (internal) |
| **db** | PostgreSQL 16 — persistent job + route storage | `5432` |

---

## Tech Stack

- **Backend**: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 (asyncpg)
- **Optimizer**: Custom NN + 2-Opt with haversine distance
- **Geocoding**: OpenStreetMap Nominatim (with retry/backoff/rate-limit compliance)
- **Database**: PostgreSQL 16, managed via Alembic migrations
- **Frontend**: Leaflet.js, vanilla JS, Nginx
- **DevOps**: Docker Compose, GitHub Actions CI/CD, Trivy security scanning, GHCR

---

## Quick Start

### Prerequisites

- Docker + Docker Compose
- (Optional) Python 3.12+ for local development

### 1. Clone & Configure

```bash
git clone <repo-url>
cd RouteWeave
cp .env.example .env
# Edit .env if you want to set DB_PASSWORD or API_KEY
```

### 2. Start Services

```bash
docker compose up --build
```

Wait for the healthchecks to pass:
- API: `http://localhost:8000/health`
- Client: `http://localhost:3000`

### 3. Run Migrations (first time only)

```bash
docker compose exec api alembic upgrade head
```

### 4. Upload & Optimize

1. Open `http://localhost:3000`
2. Upload `sample_addresses.csv` (or your own CSV with an `address` column)
3. Poll `GET /jobs/{job_id}` until status is `completed`
4. View the optimized route on the map with before/after distance comparison

---

## API Overview

### Authentication

When `API_KEY` is set in the environment, mutation endpoints require the `X-API-Key` header:

```bash
curl -X POST "http://localhost:8000/jobs" \
  -H "X-API-Key: your-secret-key" \
  -F "file=@addresses.csv"
```

Read endpoints (`GET /jobs`, `GET /jobs/{id}`, `GET /health`) remain open.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness + DB connectivity check |
| `POST` | `/jobs` | Upload CSV, create optimization job (async, returns 202) |
| `GET` | `/jobs` | List recent jobs (paginated) |
| `GET` | `/jobs/{job_id}` | Get job status |
| `GET` | `/jobs/{job_id}/route` | Get optimized route (once `completed`) |

### CSV Format

```csv
address
"1600 Amphitheatre Parkway, Mountain View, CA"
"1 Infinite Loop, Cupertino, CA"
```

- Must contain an `address` column (case-insensitive, whitespace-tolerant)
- Maximum 50 stops per job
- UTF-8 with or without BOM

---

## Database Migrations

We use **Alembic** for schema versioning.

```bash
cd api

# Auto-generate a migration from updated models
alembic revision --autogenerate -m "add users table"

# Apply pending migrations
alembic upgrade head

# Downgrade one revision
alembic downgrade -1
```

---

## Testing

```bash
# Install dependencies
pip install -r api/requirements.txt
pip install -r optimizer/requirements.txt
pip install pytest aiosqlite

# Run all tests
pytest tests/ -v
```

Test coverage (~40 tests):
- API validation, rate limiting, CSV edge cases
- Haversine accuracy, 2-opt correctness
- Performance budgets
- Authentication behavior

---

## CI/CD Pipeline

```
Push to main
    │
    ▼
┌─────────────┐
│   Tests     │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Trivy Scan  │  (blocks on CRITICAL vulnerabilities)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Build & Push│  → GHCR (api + optimizer images)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Deploy    │  → Render / Fly.io via deploy hook
└─────────────┘
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes* | `postgresql+asyncpg://...` | Async DB connection string |
| `DB_PASSWORD` | Yes* | — | PostgreSQL password (used in docker-compose) |
| `OPTIMIZER_URL` | No | `http://optimizer:8001` | Internal optimizer service URL |
| `API_KEY` | No | — | If set, required on `POST /jobs` via `X-API-Key` |
| `RENDER_DEPLOY_HOOK` | No | — | CI/CD deploy trigger URL |

\* Required for Docker Compose; SQLite fallback works for local testing.

---

## Security Checklist

- [x] Parameterized SQL (SQLAlchemy ORM)
- [x] Input validation via Pydantic v2
- [x] Per-IP rate limiting on job creation
- [x] API-key auth on mutation endpoints
- [x] CORS restricted to known origins (no wildcard + credentials)
- [x] Docker non-root users + HEALTHCHECKs
- [x] Trivy container scanning in CI/CD
- [x] Secrets via `.env` (never committed)

---

## License

MIT
