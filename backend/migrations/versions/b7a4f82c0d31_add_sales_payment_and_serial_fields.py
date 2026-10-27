"""add POS GST, payment, and serial entry fields

Revision ID: b7a4f82c0d31
Revises: a8c4126bd5e1
Create Date: 2026-10-20 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7a4f82c0d31"
down_revision: Union[str, None] = "a8c4126bd5e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("source_type", sa.String(16), nullable=False, server_default="UPLOAD"))
    op.add_column("invoices", sa.Column("vendor_address", sa.Text(), nullable=True))
    op.add_column("invoices", sa.Column("receiver_gstin", sa.String(32), nullable=True))
    op.add_column("invoices", sa.Column("receiver_address", sa.Text(), nullable=True))
    op.add_column("invoices", sa.Column("amount_paid", sa.Numeric(14, 2), nullable=False, server_default="0"))
    op.add_column("invoices", sa.Column("payment_status", sa.String(16), nullable=False, server_default="UNKNOWN"))
    op.create_index("ix_invoices_payment_status", "invoices", ["payment_status"])
    op.add_column("invoice_lines", sa.Column("tax_rate", sa.Numeric(5, 2), nullable=False, server_default="0"))
    op.add_column("invoice_lines", sa.Column("tax_amount", sa.Numeric(14, 2), nullable=False, server_default="0"))
    op.add_column("invoice_lines", sa.Column("serial_numbers", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("invoice_lines", sa.Column("warranty_months", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "vendor_address")
    op.drop_column("invoices", "source_type")
    op.drop_column("invoice_lines", "warranty_months")
    op.drop_column("invoice_lines", "serial_numbers")
    op.drop_column("invoice_lines", "tax_amount")
    op.drop_column("invoice_lines", "tax_rate")
    op.drop_index("ix_invoices_payment_status", table_name="invoices")
    op.drop_column("invoices", "payment_status")
    op.drop_column("invoices", "amount_paid")
    op.drop_column("invoices", "receiver_address")
    op.drop_column("invoices", "receiver_gstin")
