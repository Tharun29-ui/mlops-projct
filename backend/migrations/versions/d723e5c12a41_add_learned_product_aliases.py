"""store reviewer-approved product aliases

Revision ID: d723e5c12a41
Revises: c5af1b6b9d10
Create Date: 2026-10-20 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d723e5c12a41"
down_revision: Union[str, None] = "c5af1b6b9d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column(
        "aliases", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade() -> None:
    op.drop_column("products", "aliases")
