"""add invoice duplicate and confidence metadata

Revision ID: b291a7c14e32
Revises: ad05d7d4015c
Create Date: 2026-10-16 20:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b291a7c14e32"
down_revision: Union[str, None] = "ad05d7d4015c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("content_sha256", sa.String(length=64), nullable=True))
    op.add_column("invoices", sa.Column("field_confidence", sa.JSON(), nullable=True))
    op.create_index("ix_invoices_content_sha256", "invoices", ["content_sha256"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_invoices_content_sha256", table_name="invoices")
    op.drop_column("invoices", "field_confidence")
    op.drop_column("invoices", "content_sha256")
