# RouteWeave — Project Analysis

## 1. Project Overview

**RouteWeave** is a last-mile delivery route optimizer. The workflow:

1. **Upload** a CSV of delivery addresses via the FastAPI backend
2. **Geocode** each address via OpenStreetMap Nominatim
3. **Optimize** the route using Nearest-Neighbor + 2-Opt in a dedicated microservice
4. **Visualize** before/after routes on an interactive Leaflet map

### Tech Stack

| Layer | Technology |
|-------|------------|
| API | FastAPI (async) + Pydantic v2 |
| Database | PostgreSQL 16 + SQLAlchemy 2.0 (asyncpg) |
| Optimizer | FastAPI microservice (NN + 2-opt) |
| Client | Nginx + Leaflet.js |
| DevOps | Docker Compose, GitHub Actions CI/CD, Trivy security scanning |

---

## 2. Backend Readiness Assessment

### ✅ Strengths

| Area | Details |
|------|---------|
| **Architecture** | Clean 2-service split (API + optimizer microservice), async end-to-end |
| **Validation** | Pydantic v2 models, parameterized SQL (SQLAlchemy ORM), CSV edge-case handling |
| **Rate Limiting** | In-memory per-IP sliding window on `POST /jobs` |
| **Docker Hygiene** | Non-root users (`USER 1000:1000`), `HEALTHCHECK`s, health-gated startup ordering (`depends_on` with `condition: service_healthy`) |
| **CI/CD** | 4-stage pipeline: tests → Trivy security scan → GHCR build/push → deploy hook |
| **Test Suite** | ~35 tests covering API validation, rate limiting, CSV edge cases, haversine accuracy, 2-opt correctness, performance budgets |
| **Secrets** | Handled via `.env` (not committed); `.env.example` provided |
| **External APIs** | Nominatim integration with retry logic, exponential backoff, and rate-limit compliance (1 req/s) |

### ⚠️ Gaps (Production Blockers)

| # | Gap | Impact | Priority |
|---|-----|--------|----------|
| 1 | **Inline job processing** — geocoding + optimization run inside the HTTP request handler. A 50-stop job blocks the caller for tens of seconds. | Poor UX, timeouts under load, hard to scale | 🔴 High |
| 2 | **In-memory rate limiter** — resets on restart, not shared across API replicas. | Inconsistent protection under horizontal scaling | 🟡 Medium |
| 3 | **CORS wildcard with `allow_credentials=True`** — `*` + credentials is invalid in browsers and wide open. | Security misconfiguration | 🟡 Medium |
| 4 | **No authentication** — anyone can create/list jobs. | No access control, abuse risk | 🔴 High |
| 5 | `create_all()` instead of migrations (no Alembic). | Schema drift, unsafe deployments, no rollback | 🔴 High |
| 6 | **README.md is empty** (1 line). | Poor onboarding, bad first impression for recruiters | 🟡 Medium |
| 7 | `test_routeweave.db` committed to git. | Repository hygiene issue, potential stale test data | 🟢 Low |

---

## 3. Resume / Portfolio Assessment

### Is it worth showcasing?

**Yes.** This is a strong backend/DevOps portfolio piece, especially for student/fresher-level candidates.

### Key Talking Points

1. **Microservice architecture** — you designed and wired two independent services (API + optimizer) over HTTP with Docker Compose
2. **Async database layer** — SQLAlchemy 2.0 + asyncpg, not the more common synchronous stack
3. **Real algorithmic optimization** — implemented NN + 2-opt with measurable `% improvement` metrics
4. **External API integration** — geocoding with production-grade retry/backoff/rate-limit handling
5. **Containerization & CI/CD** — multi-service Docker setup, GitHub Actions, Trivy security scanning, GHCR publishing
6. **Testing discipline** — ~35 tests with edge-case coverage (BOM encoding, empty CSVs, performance budgets)

### Verdict

> **Working, well-tested demo backend** — end-to-end runnable via `docker compose up` — but not deployment-ready without a task queue, auth layer, and migrations.

---

## 4. Recommended Next Steps

To push this from **"good demo"** to **"interview centerpiece"**, address the top 3 gaps:

- [ ] **Async task queue** — offload geocoding + optimization from the request handler (FastAPI `BackgroundTasks`, Celery, or RQ)
- [ ] **Authentication** — API-key or JWT-based auth on mutation endpoints (`POST /jobs`)
- [ ] **Database migrations** — introduce Alembic with asyncpg support

Secondary improvements:
- [ ] Fix CORS configuration (remove `*` when credentials are enabled)
- [ ] Write a real README with architecture diagram and a short demo video/GIF
- [ ] Remove `test_routeweave.db` from git history

---

*Analysis generated on 2026-08-03.*
