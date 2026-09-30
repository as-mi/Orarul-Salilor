"""anii de studiu pe ale caror orare apare un eveniment

Revision ID: b2f05d8e3c71
Revises: a19e4c7d2b60
"""

from __future__ import annotations

from alembic import op

revision = "b2f05d8e3c71"
down_revision = "a19e4c7d2b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE "EVENIMENT" ADD COLUMN "ANI" VARCHAR(40)')


def downgrade() -> None:
    op.execute('ALTER TABLE "EVENIMENT" DROP COLUMN "ANI"')
