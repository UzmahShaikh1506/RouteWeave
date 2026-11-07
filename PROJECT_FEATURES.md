# RouteWeave — Complete Feature Reference

## Purpose

**RouteWeave** is a last-mile delivery route optimizer designed for logistics teams, delivery businesses, and fleet operators. It takes a spreadsheet of delivery addresses, geocodes them via OpenStreetMap Nominatim, runs a Nearest-Neighbor + 2-Opt optimization algorithm, and produces an optimized route with before/after distance comparison — visualized on an interactive map with GPX export for GPS devices.

**Why it exists**: Most delivery teams plan routes manually or use expensive enterprise software. RouteWeave demonstrates that a small, focused system with real algorithmic optimization can produce measurable distance savings (typically 20–40%) with an async, scalable architecture.

---

## Architecture

```
Browser (localhost:3000)
    |
    v
[Nginx Client] ──→ serves static UI + proxies /api/*
    |
    v
[FastAPI Backend :8000] ──→ async DB (PostgreSQL) + calls optimizer
    |
    v
[Optimizer Microservice :8001] ──→ NN + 2-Opt engine
```

| Service | Tech | Port | Role |
|---------|------|------|------|
| Client | Nginx + vanilla JS + Leaflet | 3000 | UI, file upload, map, admin dashboard |
| API | FastAPI + SQLAlchemy 2.0 (asyncpg) | 8000 | HTTP API, job orchestration, caching |
| Optimizer | FastAPI + custom algorithms | 8001 | Route math (haversine, NN, 2-opt) |
| Database | PostgreSQL 16 | 5432 | Jobs, stops, routes, geocode cache |

---

## Backend Features

### Core API
| Feature | Endpoint | What it does |
|---------|----------|-------------|
| **Health Check** | `GET /health` | Returns API + DB connectivity status |
| **Create Job** | `POST /jobs` | Upload CSV → returns `202 Accepted` with job_id. Processed async in background. |
| **Job SSE Stream** | `GET /jobs/{id}/events` | Server-Sent Events for real-time status (geocoding → optimizing → completed/failed) |
| **Get Job** | `GET /jobs/{id}` | Job status, error message, stop count |
| **List Jobs** | `GET /jobs?limit=&offset=` | Paginated recent jobs |
| **Get Route** | `GET /jobs/{id}/route` | Full optimized route: depot, stops in order, before/after distances |
| **Export GPX** | `GET /jobs/{id}/route.gpx` | Downloads `.gpx` file compatible with Garmin, Strava, Google Earth |
| **Delete Job** | `DELETE /jobs/{id}` | Admin-only: removes job + stops + route from DB |
| **Admin Stats** | `GET /admin/stats` | System metrics: job counts, route stats, cache hits |
| **Admin Jobs** | `GET /admin/jobs?status=` | Filtered full job listing with route data |
| **Public Analytics** | `GET /analytics/overview` | Aggregated stats: success rate, avg improvement, distance saved, 24h activity |

### Security
| Feature | Details |
|---------|---------|
| **API Key Auth** | Optional `X-API-Key` header on `POST /jobs`. Disabled when env var is unset. |
| **Admin Key Auth** | `ADMIN_KEY` protects `/admin/*` and `DELETE`. Returns 403 when disabled, 401 for wrong key. |
| **Rate Limiting** | Per-IP sliding window: 5 `POST /jobs` per minute. Returns 429 when exceeded. |
| **CORS** | Strict origin whitelist via `CORS_ORIGINS` env var. No wildcard + credentials combo. |
| **File Validation** | Content-type check (CSV only), 5 MB max size, 50 stops max, BOM handling. |
| **Parameterized SQL** | 100% SQLAlchemy ORM. Zero raw string concatenation. |
| **Secrets** | `.env` file only. Never committed. `.env.example` provided. |

### Performance
| Feature | Details |
|---------|---------|
| **Async Background Tasks** | `POST /jobs` returns immediately. Geocoding + optimization runs in `BackgroundTasks`. |
| **Geocoding Cache** | `geocode_cache` table stores resolved addresses (SHA-256 keyed). Hit counter tracks efficiency. |
| **Nominatim Rate Limiting** | 1.1s between requests, exponential backoff, retry logic (3 attempts). |
| **Connection Pooling** | SQLAlchemy async engine with `pool_size=5`, `max_overflow=10`, `pool_pre_ping`. |
| **Alembic Migrations** | Schema versioning with asyncpg support. Initial migration + geocode_cache migration. |

### Algorithm
| Feature | Details |
|---------|---------|
| **Haversine Distance** | Great-circle distance calculation for accurate real-world routing. |
| **Nearest Neighbor** | Greedy initial route construction from depot. |
| **2-Opt Improvement** | Iterative edge exchange to refine route. Never worsens the solution. |
| **Measurable Savings** | Returns naive_distance_km, optimized_distance_km, and pct_improvement. |

---

## Frontend Features

### Main App (`http://localhost:3000`)
| Feature | Details |
|---------|---------|
| **Drag & Drop Upload** | Manifest-style upload zone with hover/drag states. Accepts `.csv` only. |
| **Depot Input** | Warehouse field for the starting location. Falls back to the first CSV address if empty. |
| **Pipeline Tracker** | Shipment-style progress rail: Manifest → Geocode → Optimize → Dispatch, driven by live job status. |
| **Live Status Bar** | Color-coded processing / success / error messages. |
| **Distance Report** | Four KPI tiles: Before km, After km, Saved %, Stops. |
| **Route Manifest** | Timeline of depot + stops in optimized order, linked by a dashed route line. |
| **Route Legend** | Grey dashed = original CSV order, blue solid = optimized route. |
| **Skipped Addresses** | Addresses Nominatim can't resolve are listed above the manifest instead of disappearing silently. |
| **API Key Prompt** | When the server has `API_KEY` set, the UI prompts once and remembers the key. |
| **Shipment Log** | Recent jobs with `JOB-XXXXXXXX` IDs and status badges. Click (or Enter) on a completed job to reload it. |
| **GPX Download** | Button in the Route Manifest header downloads a `.gpx` file. |
| **Navigation** | Top bar links between Route Planner and Operations (admin). |

### Interactive Map (Leaflet.js)
| Feature | Details |
|---------|---------|
| **Basemap** | OpenStreetMap tiles, desaturated with a CSS filter so routes stand out. |
| **Dispatch Toolbar** | Header above the map shows the loaded job, stop count and optimized distance. |
| **Depot Marker** | Amber rounded-square marker with warehouse icon. |
| **Stop Markers** | Navy numbered markers in optimized order. |
| **Route Polylines** | Grey dashed = naive route (original CSV order), blue solid = optimized route. |
| **Route Animation** | Truck marker travels the optimized route with a pulsing teal ring. |
| **Popups** | Click any marker → address info, styled as a shipping label. |
| **Auto-fit Bounds** | Map zooms to show the entire route with padding. |

### Admin Dashboard (`http://localhost:3000/admin`)
| Feature | Details |
|---------|---------|
| **Secure Login** | Admin key input with localStorage remember. |
| **KPI Tiles** | 6 tiles: Total Jobs, Delivered, Success Rate, Avg Improvement, Distance Saved, Cached Addresses. |
| **System Health** | API, Database, In Transit, Queue, Failed Jobs, Geocode Cache — green/amber status dots. |
| **Jobs Table** | ID, status badge, created date, depot, stops, route summary with % saved, delete action. On phones each row becomes a stacked card. |
| **Status Filtering** | Dropdown to filter jobs by pending/geocoding/optimizing/completed/failed. |
| **Auto Refresh** | Polls every 10 seconds, shows last-updated time. |
| **Job Deletion** | Delete with confirmation dialog. |

### Visual Design — Enterprise Freight (light only)
| Feature | Details |
|---------|---------|
| **Palette** | Navy command bar (#0B2545), cool slate workspace (#F1F4F8), white surfaces, signal blue actions and route (#1D5BD8), amber depot (#F59E0B), teal vehicle, green for savings. |
| **Supply-Chain Motifs** | Lane-stripe rail under the command bar (blue / teal / amber), dispatch-map dot grid background, manifest-style card labels (`01 · LOAD MANIFEST`), pallet-label strips on KPI tiles. |
| **Typography** | Inter for UI text, JetBrains Mono for IDs, figures and labels (tracking-number feel). |
| **Shared Stylesheet** | `styles.css` holds tokens and components for both pages; `admin.css` adds dashboard-only rules. |
| **SVG Icons** | No emojis. All icons are inline SVG. |
| **Accessibility** | Visible focus rings, keyboard-operable upload and history, `aria-live` status, reduced-motion support. |
| **Responsive** | Wide monitor → laptop → tablet → phone breakpoints. Map stays in view (sticky) on desktop; on tablets/phones it moves above the sidebar. No horizontal scroll down to 320px. |

---------|---------|
| **Premium Color Palette** | Deep navy backgrounds (#0B1120) + coral/rose accent (#F43F5E) + sky blue data (#38BDF8) + amber highlights. |
| **Ambient Glow** | Subtle radial gradient orbs in background for atmosphere. |
| **Card Depth** | Layered shadows, subtle top highlights, hover lift effects. |
| **Shimmer Buttons** | Primary buttons have a light sweep animation on hover. |
| **Gradient Accents** | Logo, markers, stop numbers use warm gradients. |
| **Smooth Motion** | All transitions use `cubic-bezier(0.4, 0, 0.2, 1)`. Cards fade in on load. |
| **SVG Icons** | Zero emojis. All icons are inline SVG (upload, home, truck, download, sun, etc). |
| **Custom Scrollbars** | Thin styled scrollbar for stop list and history. |
| **Responsive** | Sidebar stacks above map on mobile. Admin grid adapts to 2 columns. |

---

## DevOps & Development Features

| Feature | Details |
|---------|---------|
| **Docker Compose** | 4 services with health-gated startup ordering. |
| **Non-root Containers** | All services run as `USER 1000:1000`. |
| **HEALTHCHECK** | Docker healthchecks on API and optimizer. |
| **GitHub Actions CI/CD** | 4-stage pipeline: tests → Trivy security scan → GHCR build/push → deploy hook. |
| **Trivy Scanning** | Blocks on CRITICAL vulnerabilities in container images. |
| **Environment Config** | `.env.example` with DB_PASSWORD, API_KEY, ADMIN_KEY, CORS_ORIGINS. |
| **Test Suite** | 67 tests: API, auth, rate limiting, CSV parsing, SSE, admin, analytics, GPX, optimizer math, integration, full geocode→optimize→route pipeline. Fully offline (geocoder stubbed). |
| **Pytest** | SQLite backend for fast unit tests. No Docker required for testing. |

---

## How to Run (Testing)

### Quick Start — Docker Compose (Recommended)

```bash
cd RouteWeave
cp .env.example .env
# Edit .env if you want DB_PASSWORD, API_KEY, or ADMIN_KEY

docker compose up --build

# In another terminal (first run only):
docker compose exec api alembic upgrade head
```

Wait for services to be healthy, then open:
- **http://localhost:3000** — Main app
- **http://localhost:3000/admin** — Admin dashboard
- **http://localhost:8000/docs** — Swagger API docs

### Upload a Route
1. Open `http://localhost:3000`
2. Enter a depot address (e.g., `Times Square, New York, NY`)
3. Drag `sample_addresses.csv` into the upload zone
4. Click **Optimize Route**
5. Watch the SSE status updates, then see the animated route on the map
6. Download the GPX file from the results sidebar

### Run Tests (No Docker Required)

```bash
# Install Python dependencies
pip install -r api/requirements.txt
pip install -r optimizer/requirements.txt
pip install pytest aiosqlite

# Run all tests
pytest tests/ -v
```

### Local Development (No Docker)

```bash
# 1. Start PostgreSQL
docker run -d --name routeweave-db -e POSTGRES_DB=routeweave -e POSTGRES_USER=routeweave -e POSTGRES_PASSWORD=secret -p 5432:5432 postgres:16-alpine

# 2. Set env vars
export DATABASE_URL="postgresql+asyncpg://routeweave:secret@localhost:5432/routeweave"
export OPTIMIZER_URL="http://localhost:8001"

# 3. Run migrations
cd api && alembic upgrade head && cd ..

# 4. Start optimizer
cd optimizer && uvicorn server:app --host 0.0.0.0 --port 8001 --reload

# 5. Start API (new terminal)
cd api && uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 6. Serve client (new terminal)
cd client && python -m http.server 3000
```

---

## Files You Should Know

| File | Purpose |
|------|---------|
| `api/main.py` | FastAPI app — all HTTP endpoints |
| `api/models.py` | SQLAlchemy ORM + Pydantic schemas |
| `api/geocode.py` | Nominatim client with cache integration |
| `api/database.py` | Async engine + session factory |
| `optimizer/optimize.py` | NN + 2-Opt algorithm |
| `client/index.html` | Main app markup |
| `client/styles.css` | Design tokens + shared components (both pages) |
| `client/map.js` | Upload, SSE, polling, map, animation, GPX |
| `client/admin.html` | Admin dashboard markup |
| `client/admin.css` | Admin-only layout (login, KPI tiles, responsive table) |
| `client/admin.js` | Admin stats, job table, filtering, deletion |
| `docker-compose.yml` | Service orchestration |
| `.github/workflows/ci.yml` | CI/CD pipeline |
| `PROJECT_ANALYSIS.md` | Backend readiness assessment |
| `DEMO_GUIDE.md` | Security audit + setup guide |

---

*Document version: v1.2 — 2026-08-05*
