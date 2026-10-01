"""structura anului universitar: activitate didactica, vacante, sesiuni, licenta

Revision ID: e8c27a4f9d16
Revises: d5b18f6a0e93
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e8c27a4f9d16"
down_revision = "d5b18f6a0e93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "STRUCTURA_AN",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column("AN_UNIV", sa.String(9), nullable=False),
        sa.Column("FEL", sa.String(10), nullable=False),
        sa.Column("SEMESTRU", sa.Integer()),
        sa.Column("NUME", sa.String(80)),
        sa.Column("DATA_INCEPUT", sa.Date(), nullable=False),
        sa.Column("DATA_SFARSIT", sa.Date(), nullable=False),
        sa.Column("SAPTAMANA", sa.Integer()),
        sa.CheckConstraint(
            "FEL IN ('didactica','vacanta','sesiune','restante','licenta')",
            name="ck_structura_fel",
        ),
        sa.CheckConstraint("DATA_INCEPUT <= DATA_SFARSIT", name="ck_structura_perioada"),
    )
    op.create_index("ix_STRUCTURA_AN_AN_UNIV", "STRUCTURA_AN", ["AN_UNIV"])


def downgrade() -> None:
    op.drop_index("ix_STRUCTURA_AN_AN_UNIV", table_name="STRUCTURA_AN")
    op.drop_table("STRUCTURA_AN")
