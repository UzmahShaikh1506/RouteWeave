# RouteWeave — Security Checklist

Last reviewed: 2026-11-07. Scope: API (`api/`), optimizer (`optimizer/`),
web client + nginx (`client/`), Docker Compose, CI.

Legend: ✅ in place and covered by a test or a live check · ⚠️ needs action
before a public/production deployment.

## 1. Authentication & access control

| # | Check | Status | Where |
|---|-------|--------|-------|
| 1.1 | Admin endpoints are **disabled** unless `ADMIN_KEY` is set (403) | ✅ | `api/main.py` `require_admin_key` |
| 1.2 | When `API_KEY` is set it protects **all** job data — create, status, route, GPX, SSE, job list, analytics (job addresses are customer PII) | ✅ | `TestSecurity.test_read_endpoints_require_api_key_when_configured` |
| 1.3 | Keys compared in constant time (`hmac.compare_digest`) | ✅ | `_keys_match` |
| 1.4 | Brute-force lockout: 10 failed key attempts per IP per 5 min → 429 | ✅ | `_verify_key`, `test_repeated_bad_admin_keys_are_locked_out` |
| 1.5 | nginx throttles `/api/admin/` to 2 req/s per IP (burst 10) | ✅ | `client/nginx.conf` |
| 1.6 | Admin key stored in `sessionStorage` (cleared when the tab closes), legacy `localStorage` copy removed, auto sign-out on 401 | ✅ | `client/admin.js` |
| 1.7 | Set strong random `API_KEY` / `ADMIN_KEY` in production | ⚠️ | `.env` — `python -c "import secrets; print(secrets.token_urlsafe(32))"` |

## 2. Input validation

| # | Check | Status | Where |
|---|-------|--------|-------|
| 2.1 | Upload size capped at 5 MB in nginx **and** API (bounded read, never loads more than limit + 1 byte) | ✅ | `create_job` |
| 2.2 | Max 50 stops; each address and the depot ≤ 300 chars | ✅ | `test_overlong_address_rejected`, `test_overlong_depot_rejected` |
| 2.3 | CSV must be UTF-8 and contain an `address` column | ✅ | `test_csv_parse_errors_do_not_leak_internals` |
| 2.4 | Job IDs parsed as UUIDs before any query | ✅ | all `/jobs/{job_id}` routes |
| 2.5 | Pagination bounded (`limit` 1–50/100, `offset` ≥ 0) | ✅ | `test_negative_pagination_rejected` |
| 2.6 | Admin status filter allow-listed | ✅ | `test_admin_status_filter_validated` |
| 2.7 | Optimizer payload bounded (≤ 100 stops, string lengths, lat/lng ranges) | ✅ | `optimizer/server.py` |
| 2.8 | SQL only via SQLAlchemy ORM (parameterized) | ✅ | no raw SQL with user input |

## 3. Output encoding / XSS

| # | Check | Status | Where |
|---|-------|--------|-------|
| 3.1 | `escapeHtml` escapes `& < > " '` — safe in element **and** attribute context (previously quotes were not escaped → attribute injection via a crafted address) | ✅ | `client/map.js`, `client/admin.js`; verified in browser with an `<img onerror>` payload |
| 3.2 | GPX built with `ElementTree` (auto-escaped XML) | ✅ | `export_gpx` |
| 3.3 | Content-Security-Policy: no inline scripts, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`, `connect-src 'self'` | ✅ | `client/nginx.conf` |
| 3.4 | Third-party script (Leaflet) pinned to a version with SRI hashes | ✅ | `client/index.html` |

## 4. Error handling & information disclosure

| # | Check | Status | Where |
|---|-------|--------|-------|
| 4.1 | Unexpected job failures store a generic message; the exception goes to the server log only | ✅ | `test_failed_job_hides_exception_text` |
| 4.2 | CSV parse errors and optimizer errors return generic messages | ✅ | `create_job`, `optimizer/server.py` |
| 4.3 | nginx version hidden (`server_tokens off`) | ✅ | live check: `Server: nginx` |
| 4.4 | API docs (`/docs`, `/redoc`, `/openapi.json`) can be turned off with `ENABLE_DOCS=false` | ⚠️ | set `ENABLE_DOCS=false` in production |

## 5. Transport, headers & browser policy

| # | Check | Status | Where |
|---|-------|--------|-------|
| 5.1 | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy` on pages and API | ✅ | nginx + API middleware, `test_security_headers_present` |
| 5.2 | `Permissions-Policy` denies camera, mic, geolocation, payment, USB | ✅ | nginx |
| 5.3 | `Cross-Origin-Opener-Policy: same-origin` | ✅ | nginx |
| 5.4 | Admin API responses `Cache-Control: no-store` | ✅ | `test_admin_responses_not_cached` |
| 5.5 | CORS: explicit origins, **no credentials**, only GET/POST/DELETE/OPTIONS and the 3 headers used | ✅ | `test_cors_does_not_allow_credentials` |
| 5.6 | Legacy `X-XSS-Protection` disabled (`0`) in favour of CSP | ✅ | nginx |
| 5.7 | TLS + HSTS in front of nginx (keys travel in headers) | ⚠️ | terminate TLS at your proxy/host, then enable the commented HSTS line in `nginx.conf` |

## 6. Abuse & resource limits

| # | Check | Status | Where |
|---|-------|--------|-------|
| 6.1 | Job creation limited to 5 per minute per IP | ✅ | `_check_rate_limit` |
| 6.2 | Rate-limit IP comes from `X-Real-IP` **only** when `TRUST_PROXY_HEADERS=true` (set for the nginx deployment), so clients can't spoof it | ✅ | `test_proxy_headers_ignored_unless_trusted` |
| 6.3 | nginx overwrites `X-Forwarded-For` (no client-supplied chain) | ✅ | `client/nginx.conf` |
| 6.4 | Rate-limit memory bounded — idle IP buckets deleted | ✅ | `test_rate_limit_store_drops_idle_ips` |
| 6.5 | nginx throttles `/api/` to 20 req/s per IP (burst 40) | ✅ | `client/nginx.conf` |
| 6.6 | Nominatim calls rate-limited (1/s) and cached | ✅ | `api/geocode.py` |
| 6.7 | In-memory limits are per process; use Redis or the proxy if you run several API replicas | ⚠️ | architecture note |

## 7. Infrastructure

| # | Check | Status | Where |
|---|-------|--------|-------|
| 7.1 | PostgreSQL and API ports bound to `127.0.0.1` only; optimizer not published at all | ✅ | `docker-compose.yml` (live: `127.0.0.1:5432`, `127.0.0.1:8000`) |
| 7.2 | API and optimizer containers run as non-root (UID 1000) | ✅ | Dockerfiles |
| 7.3 | `no-new-privileges` on all containers; `cap_drop: ALL` on API and optimizer | ✅ | `docker-compose.yml` |
| 7.4 | `.env` git-ignored; `.env.example` has placeholders only | ✅ | `.gitignore` |
| 7.5 | Change the default `DB_PASSWORD` | ⚠️ | `.env` |

## 8. Dependencies & CI

| # | Check | Status | Where |
|---|-------|--------|-------|
| 8.1 | Known-vulnerable `starlette` 0.38.6 and `python-multipart` 0.0.12 upgraded (`fastapi` 0.162.0, `starlette` 1.10.0, `python-multipart` 0.0.33, `pydantic` 2.14.3) | ✅ | `pip-audit`: no known vulnerabilities |
| 8.2 | CI runs `pip-audit` on both services | ✅ | `.github/workflows/ci.yml` |
| 8.3 | CI runs Trivy image scans (fails on CRITICAL) | ✅ | `.github/workflows/ci.yml` |

## Before going public — short list

1. Generate and set `API_KEY`, `ADMIN_KEY` and `DB_PASSWORD` in `.env`.
2. Put TLS in front (Caddy, Cloudflare, a load balancer) and enable HSTS.
3. Set `ENABLE_DOCS=false` and `CORS_ORIGINS=https://your-domain`.
4. Re-run `pip-audit -r api/requirements.txt -r optimizer/requirements.txt` regularly (CI does this on every push).

## Reporting a vulnerability

Please open a private security advisory on the repository rather than a public issue.
