"""store shared app pricing defaults"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f0c6b4a2d1e9"
down_revision: Union[str, None] = "e2c90f17a643"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
