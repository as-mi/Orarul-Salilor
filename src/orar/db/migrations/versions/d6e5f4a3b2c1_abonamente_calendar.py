"""abonamente la calendarul ICS

Un abonament tine tokenul secret din URL-ul feed-ului, subiectul (grupa sau profesor) si,
pentru vizitatorii fara cont, snapshot-ul preferintelor. Subiectul e pe slug/nume, nu pe
id-uri: nodurile GRUPA se pot recrea la o publicare noua.

Revision ID: d6e5f4a3b2c1
Revises: c4f1a9e73b20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d6e5f4a3b2c1"
down_revision = "c4f1a9e73b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ABONAMENT_CALENDAR",
        sa.Column("ID_ABONAMENT", sa.Integer(), nullable=False),
        sa.Column("TOKEN", sa.String(length=64), nullable=False),
        sa.Column("FEL", sa.String(length=12), nullable=False),
        sa.Column("MOD", sa.String(length=12), nullable=False),
        sa.Column("ID_USER", sa.Integer(), nullable=True),
        sa.Column("GRUPA_SLUG", sa.String(length=180), nullable=True),
        sa.Column("AN_UNIV", sa.String(length=9), nullable=True),
        sa.Column("PROFESOR_NUME", sa.String(length=120), nullable=True),
        sa.Column("SEMESTRU", sa.Integer(), nullable=False),
        sa.Column("OPTIUNI", sa.Text(), nullable=True),
        sa.Column("CLIENT", sa.String(length=64), nullable=True),
        sa.Column("AMPRENTA", sa.String(length=64), nullable=False),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
        sa.Column("MODIFICAT_LA", sa.DateTime(), nullable=False),
        sa.Column("ACCESAT_LA", sa.DateTime(), nullable=True),
        sa.Column("REVOCAT_LA", sa.DateTime(), nullable=True),
        sa.CheckConstraint("FEL IN ('grupa','profesor')", name="ck_abonament_fel"),
        sa.CheckConstraint("MOD IN ('cont','instantaneu')", name="ck_abonament_mod"),
        sa.CheckConstraint("SEMESTRU IN (1,2)", name="ck_abonament_semestru"),
        sa.ForeignKeyConstraint(["ID_USER"], ["USER.ID"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ID_ABONAMENT"),
        sa.UniqueConstraint("TOKEN", name="uq_abonament_token"),
        sa.UniqueConstraint("AMPRENTA", name="uq_abonament_amprenta"),
    )
    op.create_index("ix_abonament_user_fel", "ABONAMENT_CALENDAR", ["ID_USER", "FEL"])
    op.create_index("ix_abonament_client_fel", "ABONAMENT_CALENDAR", ["CLIENT", "FEL"])
    op.create_index("ix_abonament_accesat", "ABONAMENT_CALENDAR", ["ACCESAT_LA"])


def downgrade() -> None:
    op.drop_index("ix_abonament_accesat", table_name="ABONAMENT_CALENDAR")
    op.drop_index("ix_abonament_client_fel", table_name="ABONAMENT_CALENDAR")
    op.drop_index("ix_abonament_user_fel", table_name="ABONAMENT_CALENDAR")
    op.drop_table("ABONAMENT_CALENDAR")
