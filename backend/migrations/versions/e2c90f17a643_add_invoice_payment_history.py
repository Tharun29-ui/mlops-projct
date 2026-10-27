"""retain dated entries for invoice payments

Revision ID: e2c90f17a643
Revises: d913b0f2a764
Create Date: 2026-10-20 19:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e2c90f17a643"
down_revision: Union[str, None] = "d913b0f2a764"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("payment_history", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade() -> None:
    op.drop_column("invoices", "payment_history")
