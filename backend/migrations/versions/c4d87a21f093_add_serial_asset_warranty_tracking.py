"""track serialized assets and their warranty lifecycle

Revision ID: c4d87a21f093
Revises: b7a4f82c0d31
Create Date: 2026-10-20 18:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4d87a21f093"
down_revision: Union[str, None] = "b7a4f82c0d31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "serial_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("serial_key", sa.String(160), nullable=False),
        sa.Column("serial_number", sa.String(160), nullable=False),
        sa.Column("product_id", sa.String(80), nullable=False),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("purchase_invoice_id", sa.String(80), nullable=False),
        sa.Column("sale_invoice_id", sa.String(80), nullable=True),
        sa.Column("warranty_months", sa.Integer(), nullable=True),
        sa.Column("warranty_start", sa.Date(), nullable=True),
        sa.Column("warranty_until", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="IN_STOCK"),
        sa.UniqueConstraint("serial_key", name="uq_serial_assets_serial_key"),
    )
    for column in ("serial_key", "product_id", "purchase_invoice_id", "sale_invoice_id", "status"):
        op.create_index(f"ix_serial_assets_{column}", "serial_assets", [column])


def downgrade() -> None:
    for column in ("status", "sale_invoice_id", "purchase_invoice_id", "product_id", "serial_key"):
        op.drop_index(f"ix_serial_assets_{column}", table_name="serial_assets")
    op.drop_table("serial_assets")
