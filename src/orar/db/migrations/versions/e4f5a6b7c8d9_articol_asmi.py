"""articolul asmi din subsol: ultimul articol publicat pe asmi.ro

Revision ID: e4f5a6b7c8d9
Revises: f7a2c4e9b1d3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e4f5a6b7c8d9"
down_revision = "f7a2c4e9b1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ARTICOL_ASMI",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column("SLUG", sa.String(200), nullable=False, unique=True),
        sa.Column("TITLU", sa.String(300), nullable=False),
        sa.Column("ACTUALIZAT_LA", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ARTICOL_ASMI")
