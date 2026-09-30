"""evenimente: activitati cu data, in afara orarului saptamanal

Evenimentele ASMI si alte activitati pentru care se rezerva o sala (si in weekend), plus
perioadele fara sala (recrutari, Balul Bobocilor) din calendarul ASMI.

Revision ID: f68d3b01c4e5
Revises: e57c2a90b3d4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f68d3b01c4e5"
down_revision = "e57c2a90b3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "EVENIMENT",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column("FEL", sa.String(8), nullable=False),
        sa.Column("TITLU", sa.String(160), nullable=False),
        sa.Column("DESCRIERE", sa.Text()),
        sa.Column("LINK", sa.String(300)),
        sa.Column("DATA_INCEPUT", sa.Date(), nullable=False),
        sa.Column("DATA_SFARSIT", sa.Date(), nullable=False),
        sa.Column("ORA_INCEPUT", sa.Time()),
        sa.Column("ORA_SFARSIT", sa.Time()),
        sa.Column("SALA", sa.Integer(), sa.ForeignKey("SALA.ID_SALA", ondelete="SET NULL")),
        sa.Column("LOC", sa.String(160)),
        sa.Column("VIZIBILITATE", sa.String(14), nullable=False),
        sa.Column("SPECIALIZARI", sa.String(200)),
        sa.Column("CREAT_DE", sa.String(120), nullable=False),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
        sa.CheckConstraint("FEL IN ('asmi','alta')", name="ck_eveniment_fel"),
        sa.CheckConstraint(
            "VIZIBILITATE IN ('toate','specializari','sala')", name="ck_eveniment_vizibilitate"
        ),
        sa.CheckConstraint("DATA_INCEPUT <= DATA_SFARSIT", name="ck_eveniment_perioada"),
    )
    op.create_index("ix_eveniment_data", "EVENIMENT", ["DATA_INCEPUT", "DATA_SFARSIT"])


def downgrade() -> None:
    op.drop_index("ix_eveniment_data", table_name="EVENIMENT")
    op.drop_table("EVENIMENT")
