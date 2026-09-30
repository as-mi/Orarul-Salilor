"""contul unui profesor: al cui orar e "orarul meu", si cererea de a primi rolul

Fara `batch_alter_table`: ar reconstrui USER, iar SESIZARE il refera.

Revision ID: c40a7e91f5d2
Revises: b2f05d8e3c71
"""

from __future__ import annotations

from alembic import op

revision = "c40a7e91f5d2"
down_revision = "b2f05d8e3c71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE "USER" ADD COLUMN "PROFESOR" VARCHAR(120)')
    op.execute('ALTER TABLE "USER" ADD COLUMN "CERERE_PROFESOR" VARCHAR(120)')


def downgrade() -> None:
    op.execute('ALTER TABLE "USER" DROP COLUMN "CERERE_PROFESOR"')
    op.execute('ALTER TABLE "USER" DROP COLUMN "PROFESOR"')
