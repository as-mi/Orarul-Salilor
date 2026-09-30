"""culoarea evenimentului

Revision ID: a19e4c7d2b60
Revises: f68d3b01c4e5
"""

from __future__ import annotations

from alembic import op

revision = "a19e4c7d2b60"
down_revision = "f68d3b01c4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE "EVENIMENT" ADD COLUMN "CULOARE" VARCHAR(12)')


def downgrade() -> None:
    op.execute('ALTER TABLE "EVENIMENT" DROP COLUMN "CULOARE"')
