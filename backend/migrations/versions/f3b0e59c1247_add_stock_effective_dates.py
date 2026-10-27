"""Record effective dates for opening stock and inventory movements."""
from alembic import op
import sqlalchemy as sa
revision = 'f3b0e59c1247'
down_revision = 'f2b6e713c008'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('stock_movements', sa.Column('effective_date', sa.Date(), nullable=True))
    op.add_column('stock_movements', sa.Column('movement_type', sa.String(32), server_default='ADJUSTMENT', nullable=False))


def downgrade():
    op.drop_column('stock_movements', 'movement_type')
    op.drop_column('stock_movements', 'effective_date')
