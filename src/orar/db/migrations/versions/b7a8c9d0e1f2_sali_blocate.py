"""sali blocate: flagul BLOCATA pe SALA, pentru tabul "Fereastra"

Revision ID: b7a8c9d0e1f2
Revises: e4f5a6b7c8d9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7a8c9d0e1f2"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "SALA", sa.Column("BLOCATA", sa.Boolean(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("SALA", "BLOCATA")
