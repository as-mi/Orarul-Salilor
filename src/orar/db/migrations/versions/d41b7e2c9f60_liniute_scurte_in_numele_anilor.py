"""liniute scurte in numele anilor

Numele nodurilor de an ("Informatică — anul 2") se scriu de acum cu liniuta scurta, ca
peste tot pe sit; ingestul le genereaza deja asa.

Revision ID: d41b7e2c9f60
Revises: c3e8a5f17d22
"""

from __future__ import annotations

from alembic import op

revision = "d41b7e2c9f60"
down_revision = "c3e8a5f17d22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """UPDATE "GRUPA" SET "NUME" = replace("NUME", ' — ', ' - ') WHERE "TIP" = 'specializare'"""
    )


def downgrade() -> None:
    op.execute(
        """UPDATE "GRUPA" SET "NUME" = replace("NUME", ' - ', ' — ') WHERE "TIP" = 'specializare'"""
    )
