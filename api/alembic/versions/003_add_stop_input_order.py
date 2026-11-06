"""Add stops.input_order (original CSV position)

Revision ID: 003
Revises: 002
Create Date: 2026-11-07 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stops", sa.Column("input_order", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("stops", "input_order")
