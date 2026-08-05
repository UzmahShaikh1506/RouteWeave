"""Add geocode_cache table

Revision ID: 002
Revises: 001
Create Date: 2026-08-05 18:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "geocode_cache",
        sa.Column("id", sa.Uuid(), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("address_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("raw_address", sa.String(length=500), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("idx_geocode_cache_hash", "geocode_cache", ["address_hash"])
    op.create_index("idx_geocode_cache_created_at", "geocode_cache", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_geocode_cache_created_at", table_name="geocode_cache")
    op.drop_index("idx_geocode_cache_hash", table_name="geocode_cache")
    op.drop_table("geocode_cache")
