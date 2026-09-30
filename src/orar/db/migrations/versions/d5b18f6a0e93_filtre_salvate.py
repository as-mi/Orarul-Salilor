"""filtrele salvate ale orarului de statistici

Revision ID: d5b18f6a0e93
Revises: c40a7e91f5d2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d5b18f6a0e93"
down_revision = "c40a7e91f5d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "FILTRU_SALVAT",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column("NUME", sa.String(80), nullable=False),
        sa.Column("PARAMETRI", sa.Text(), nullable=False),
        sa.Column("CREAT_DE", sa.String(120), nullable=False),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("FILTRU_SALVAT")
