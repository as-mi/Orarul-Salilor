"""orarul profesorilor, pastrat

A doua sursa nu mai e doar citita si aruncata: numele din titlurile paginilor devin lista
din care se alege un profesor (`PROFESOR.DIN_ORAR`), iar activitatile fiecaruia raman in
`ORAR_PROFESOR`, ca sa se poata potrivi cu orarul grupelor si in coada de verificare.

Fara `batch_alter_table`: ar reconstrui PROFESOR, iar ORA o refera.

Revision ID: e57c2a90b3d4
Revises: d41b7e2c9f60
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e57c2a90b3d4"
down_revision = "d41b7e2c9f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE "PROFESOR" ADD COLUMN "DIN_ORAR" BOOLEAN NOT NULL DEFAULT 0')
    op.create_table(
        "ORAR_PROFESOR",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column("AN_UNIV", sa.String(9), nullable=False),
        sa.Column("SEMESTRU", sa.Integer(), nullable=False),
        sa.Column("PROFESOR", sa.String(120), nullable=False),
        sa.Column("ZI", sa.String(10), nullable=False),
        sa.Column("ORA_INCEPUT", sa.Integer(), nullable=False),
        sa.Column("ORA_SFARSIT", sa.Integer(), nullable=False),
        sa.Column("SALA", sa.String(90), nullable=False, server_default=""),
        sa.Column("MATERIE", sa.String(200), nullable=False, server_default=""),
        sa.Column("FRECVENTA", sa.String(4)),
        sa.Column("SAPTAMANI", sa.String(40)),
    )
    op.create_index(
        "ix_orar_profesor_slot", "ORAR_PROFESOR", ["AN_UNIV", "SEMESTRU", "ZI", "ORA_INCEPUT"]
    )


def downgrade() -> None:
    op.drop_index("ix_orar_profesor_slot", table_name="ORAR_PROFESOR")
    op.drop_table("ORAR_PROFESOR")
    op.execute('ALTER TABLE "PROFESOR" DROP COLUMN "DIN_ORAR"')
