"""store suggested rates and justified sales price overrides

Revision ID: d913b0f2a764
Revises: c4d87a21f093
Create Date: 2026-10-20 19:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d913b0f2a764"
down_revision: Union[str, None] = "c4d87a21f093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoice_lines", sa.Column("suggested_unit_rate", sa.Numeric(14, 2), nullable=True))
    op.add_column("invoice_lines", sa.Column("price_override_reason", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("invoice_lines", "price_override_reason")
    op.drop_column("invoice_lines", "suggested_unit_rate")
