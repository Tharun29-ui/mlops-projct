"""add optional per-product sale margin rules

Revision ID: a8c4126bd5e1
Revises: e4bd2f09b260
Create Date: 2026-10-20 17:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8c4126bd5e1"
down_revision: Union[str, None] = "e4bd2f09b260"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("minimum_margin_percent", sa.Numeric(5, 2), nullable=True))
    op.add_column("products", sa.Column("target_margin_percent", sa.Numeric(5, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "target_margin_percent")
    op.drop_column("products", "minimum_margin_percent")
