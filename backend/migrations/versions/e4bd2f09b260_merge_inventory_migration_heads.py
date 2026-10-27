"""merge the invoice-line and product-identity migration branches

Revision ID: e4bd2f09b260
Revises: d723e5c12a41, e18d3a5c9b42
Create Date: 2026-10-20 14:10:00.000000
"""
from typing import Sequence, Union


revision: str = "e4bd2f09b260"
down_revision: Union[str, tuple[str, str], None] = (
    "d723e5c12a41",
    "e18d3a5c9b42",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
