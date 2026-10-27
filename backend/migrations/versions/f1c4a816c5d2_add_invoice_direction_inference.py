"""store extracted receiver and direction explanation

Revision ID: f1c4a816c5d2
Revises: b291a7c14e32
Create Date: 2026-10-20 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1c4a816c5d2"
down_revision: Union[str, None] = "b291a7c14e32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("receiver_name", sa.String(length=300), nullable=True))
    op.add_column("invoices", sa.Column("direction_reason", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "direction_reason")
    op.drop_column("invoices", "receiver_name")
