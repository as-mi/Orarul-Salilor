"""orarul ales al contului si resetarea grupelor la un an universitar nou

`USER.ORAR_AUTOMAT`: "Orarul meu" urmeaza orarul implicit al sitului. `USER.ORAR_ALES`: orarul
pe care a ramas cine nu-l urmeaza. `SETARE`: anul pentru care s-au resetat deja grupele.

Fara `batch_alter_table` pe USER: SESIZARE il refera.

Revision ID: a2d64e8b1f37
Revises: f91b3d5c7a28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a2d64e8b1f37"
down_revision = "f91b3d5c7a28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE "USER" ADD COLUMN "ORAR_AUTOMAT" BOOLEAN NOT NULL DEFAULT 1')
    op.execute(
        'ALTER TABLE "USER" ADD COLUMN "ORAR_ALES" INTEGER '
        'REFERENCES "PERIOADA" ("ID_PERIOADA") ON DELETE SET NULL'
    )
    op.create_table(
        "SETARE",
        sa.Column("CHEIE", sa.String(60), primary_key=True),
        sa.Column("VALOARE", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("SETARE")
    op.execute('ALTER TABLE "USER" DROP COLUMN "ORAR_ALES"')
    op.execute('ALTER TABLE "USER" DROP COLUMN "ORAR_AUTOMAT"')
