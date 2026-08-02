"""Initial schema — jobs, stops, routes

Revision ID: 001
Revises:
Create Date: 2026-08-03 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("depot_address", sa.String(length=500), nullable=True),
        sa.Column("depot_lat", sa.Float(), nullable=True),
        sa.Column("depot_lng", sa.Float(), nullable=True),
    )

    op.create_table(
        "stops",
        sa.Column("id", sa.Uuid(), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_address", sa.String(length=500), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("visit_order", sa.Integer(), nullable=True),
    )

    op.create_table(
        "routes",
        sa.Column("id", sa.Uuid(), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("naive_distance_km", sa.Float(), nullable=False),
        sa.Column("optimized_distance_km", sa.Float(), nullable=False),
        sa.Column("pct_improvement", sa.Float(), nullable=False),
    )

    # Indexes for common query patterns
    op.create_index("idx_stops_job_id", "stops", ["job_id"])
    op.create_index("idx_routes_job_id", "routes", ["job_id"])
    op.create_index("idx_jobs_created_at", "jobs", ["created_at"], postgresql_using="btree")


def downgrade() -> None:
    op.drop_index("idx_jobs_created_at", table_name="jobs")
    op.drop_index("idx_routes_job_id", table_name="routes")
    op.drop_index("idx_stops_job_id", table_name="stops")

    op.drop_table("routes")
    op.drop_table("stops")
    op.drop_table("jobs")
