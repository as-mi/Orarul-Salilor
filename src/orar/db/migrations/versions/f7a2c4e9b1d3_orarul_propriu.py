"""orarul propriu: evenimente personale si activitati importate din alte grupe/serii

Adauga cele doua tabele pe care le poate edita singur un cont, pe orarul lui: evenimentele
personale (saptamanale sau cu data fixa) si activitatile importate (copii ale activitatilor
altor formatiuni).

Revision ID: f7a2c4e9b1d3
Revises: d9f3e7a1c4b2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f7a2c4e9b1d3"
down_revision = "d9f3e7a1c4b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "EVENIMENT_PERSONAL",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column(
            "ID_USER", sa.Integer(), sa.ForeignKey("USER.ID", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("TITLU", sa.String(160), nullable=False),
        sa.Column("LOC", sa.String(160)),
        sa.Column("ZI_SAPTAMANA", sa.String(10)),
        sa.Column("DATA", sa.Date()),
        sa.Column("ORA_INCEPUT", sa.Time(), nullable=False),
        sa.Column("ORA_SFARSIT", sa.Time(), nullable=False),
        sa.Column("FRECVENTA", sa.String(4)),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(ZI_SAPTAMANA IS NULL) != (DATA IS NULL)", name="ck_eveniment_personal_zi"
        ),
        sa.CheckConstraint("ORA_INCEPUT < ORA_SFARSIT", name="ck_eveniment_personal_ore"),
        sa.CheckConstraint(
            "FRECVENTA IS NULL OR FRECVENTA IN ('SI','SP')", name="ck_eveniment_personal_frecv"
        ),
    )
    op.create_index("ix_eveniment_personal_user", "EVENIMENT_PERSONAL", ["ID_USER"])

    op.create_table(
        "ACTIVITATE_IMPORTATA",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column(
            "ID_USER", sa.Integer(), sa.ForeignKey("USER.ID", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("SURSA", sa.String(120), nullable=False),
        sa.Column("MATERIE", sa.String(160), nullable=False),
        sa.Column("TIP", sa.String(40)),
        sa.Column("PROFESOR", sa.String(120)),
        sa.Column("SALA", sa.String(120)),
        sa.Column("ZI_SAPTAMANA", sa.String(10), nullable=False),
        sa.Column("ORA_INCEPUT", sa.Time(), nullable=False),
        sa.Column("ORA_SFARSIT", sa.Time(), nullable=False),
        sa.Column("FRECVENTA", sa.String(4)),
        sa.Column("SAPTAMANI", sa.String(120)),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
        sa.CheckConstraint("ORA_INCEPUT < ORA_SFARSIT", name="ck_activitate_importata_ore"),
    )
    op.create_index("ix_activitate_importata_user", "ACTIVITATE_IMPORTATA", ["ID_USER"])


def downgrade() -> None:
    op.drop_table("ACTIVITATE_IMPORTATA")
    op.drop_table("EVENIMENT_PERSONAL")
