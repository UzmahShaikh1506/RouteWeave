-- RouteWeave Database Initialization Script
-- This serves as a fallback — SQLAlchemy's create_all() is the primary
-- mechanism for table creation. This file documents the intended schema
-- and can be mounted as a Postgres init script if needed.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    error_message TEXT,
    depot_address VARCHAR(500),
    depot_lat DOUBLE PRECISION,
    depot_lng DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS stops (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    raw_address VARCHAR(500) NOT NULL,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION,
    visit_order INTEGER
);

CREATE TABLE IF NOT EXISTS routes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    naive_distance_km DOUBLE PRECISION NOT NULL,
    optimized_distance_km DOUBLE PRECISION NOT NULL,
    pct_improvement DOUBLE PRECISION NOT NULL
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_stops_job_id ON stops(job_id);
CREATE INDEX IF NOT EXISTS idx_routes_job_id ON routes(job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
