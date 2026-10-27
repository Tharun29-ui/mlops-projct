"""add searchable product identity fields

Revision ID: c5af1b6b9d10
Revises: f1c4a816c5d2
Create Date: 2026-10-20 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5af1b6b9d10"
down_revision: Union[str, None] = "f1c4a816c5d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("product_type", sa.String(length=120), nullable=True))
    op.add_column("products", sa.Column("brand", sa.String(length=120), nullable=True))
    op.add_column("products", sa.Column("model", sa.String(length=160), nullable=True))
    op.add_column("products", sa.Column("hsn_sac", sa.String(length=32), nullable=True))
    op.create_index("ix_products_product_type", "products", ["product_type"], unique=False)
    op.create_index("ix_products_brand", "products", ["brand"], unique=False)
    op.create_index("ix_products_hsn_sac", "products", ["hsn_sac"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_products_hsn_sac", table_name="products")
    op.drop_index("ix_products_brand", table_name="products")
    op.drop_index("ix_products_product_type", table_name="products")
    op.drop_column("products", "hsn_sac")
    op.drop_column("products", "model")
    op.drop_column("products", "brand")
    op.drop_column("products", "product_type")
