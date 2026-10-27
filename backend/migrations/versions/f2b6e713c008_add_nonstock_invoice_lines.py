"""Separate service and expense lines from physical inventory."""
from alembic import op
import sqlalchemy as sa

revision = "f2b6e713c008"
down_revision = "f0c6b4a2d1e9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("invoice_lines", sa.Column("is_stock_item", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column("invoice_lines", "is_stock_item")
