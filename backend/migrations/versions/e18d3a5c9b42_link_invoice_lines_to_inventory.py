"""link invoice lines to inventory products

Revision ID: e18d3a5c9b42
Revises: b291a7c14e32
Create Date: 2026-10-19 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e18d3a5c9b42"
down_revision: Union[str, None] = "b291a7c14e32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("invoice_lines") as batch:
        batch.add_column(sa.Column("product_id", sa.Integer(), nullable=True))
        batch.create_index("ix_invoice_lines_product_id", ["product_id"], unique=False)
        batch.create_foreign_key("fk_invoice_lines_product_id_products", "products",
                                 ["product_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("invoice_lines") as batch:
        batch.drop_constraint("fk_invoice_lines_product_id_products", type_="foreignkey")
        batch.drop_index("ix_invoice_lines_product_id")
        batch.drop_column("product_id")
