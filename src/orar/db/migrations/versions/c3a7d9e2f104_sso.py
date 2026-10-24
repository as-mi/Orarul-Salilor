"""sso: contul legat de SSO-ul ASMI (sub) si sesiunile SSO tinute pe server

Fara `batch_alter_table` pe USER: SESIZARE, PERMISIUNE si TUTOR il refera.

Revision ID: c3a7d9e2f104
Revises: b8e1f4c2d953
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3a7d9e2f104"
down_revision = "b8e1f4c2d953"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE "USER" ADD COLUMN "SSO_SUB" VARCHAR(64)')
    op.create_index("uq_USER_SSO_SUB", "USER", ["SSO_SUB"], unique=True)
    op.create_table(
        "SSO_SESIUNE",
        sa.Column("ID", sa.String(64), primary_key=True),
        sa.Column(
            "ID_USER", sa.Integer(), sa.ForeignKey("USER.ID", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("SID", sa.String(64)),
        sa.Column("REFRESH_TOKEN", sa.Text()),
        sa.Column("ID_TOKEN", sa.Text()),
        sa.Column("ROLURI", sa.Text(), nullable=False),
        sa.Column("PROFIL", sa.Text()),
        sa.Column("CREATA_LA", sa.DateTime(), nullable=False),
        sa.Column("VERIFICAT_LA", sa.DateTime(), nullable=False),
        sa.Column("EXPIRA_LA", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_SSO_SESIUNE_ID_USER", "SSO_SESIUNE", ["ID_USER"])
    op.create_index("ix_SSO_SESIUNE_SID", "SSO_SESIUNE", ["SID"])


def downgrade() -> None:
    op.drop_index("ix_SSO_SESIUNE_SID", "SSO_SESIUNE")
    op.drop_index("ix_SSO_SESIUNE_ID_USER", "SSO_SESIUNE")
    op.drop_table("SSO_SESIUNE")
    op.drop_index("uq_USER_SSO_SUB", "USER")
    op.execute('ALTER TABLE "USER" DROP COLUMN "SSO_SUB"')
