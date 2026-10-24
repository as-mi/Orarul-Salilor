"""tutoriate: permisiuni, tutori pe materii, tutoriate saptamanale si anulari

Fara `batch_alter_table` pe SALA si USER: ORA si SESIZARE le refera.

Revision ID: b8e1f4c2d953
Revises: a2d64e8b1f37
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8e1f4c2d953"
down_revision = "a2d64e8b1f37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE "SALA" ADD COLUMN "DOAR_TUTORIATE" BOOLEAN NOT NULL DEFAULT 0')
    op.execute('ALTER TABLE "USER" ADD COLUMN "TUTORIATE_ALESE" TEXT')
    op.create_table(
        "PERMISIUNE",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column(
            "ID_USER", sa.Integer(), sa.ForeignKey("USER.ID", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("CHEIE", sa.String(30), nullable=False),
        sa.Column("ACORDAT_DE", sa.String(120), nullable=False),
        sa.Column("ACORDAT_LA", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("ID_USER", "CHEIE", name="uq_permisiune"),
    )
    op.create_index("ix_PERMISIUNE_ID_USER", "PERMISIUNE", ["ID_USER"])
    op.create_table(
        "TUTOR",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column("ID_USER", sa.Integer(), sa.ForeignKey("USER.ID", ondelete="CASCADE")),
        sa.Column("EMAIL", sa.String(180)),
        sa.Column("MATERIE", sa.String(160), nullable=False),
        sa.Column("SPECIALIZARI", sa.String(200)),
        sa.Column("ANI", sa.String(40)),
        sa.Column("VALABIL_DE_LA", sa.Date(), nullable=False),
        sa.Column("VALABIL_PANA_LA", sa.Date(), nullable=False),
        sa.Column("CREAT_DE", sa.String(120), nullable=False),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
        sa.CheckConstraint("VALABIL_DE_LA <= VALABIL_PANA_LA", name="ck_tutor_perioada"),
        sa.CheckConstraint("ID_USER IS NOT NULL OR EMAIL IS NOT NULL", name="ck_tutor_cine"),
    )
    op.create_index("ix_TUTOR_ID_USER", "TUTOR", ["ID_USER"])
    op.create_index("ix_TUTOR_EMAIL", "TUTOR", ["EMAIL"])
    op.create_table(
        "TUTORIAT",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column("AN_UNIV", sa.String(9), nullable=False),
        sa.Column("SEMESTRU", sa.Integer(), nullable=False),
        sa.Column("MATERIE", sa.String(160), nullable=False),
        sa.Column("ZI_SAPTAMANA", sa.String(10), nullable=False),
        sa.Column("ORA_INCEPUT", sa.Time(), nullable=False),
        sa.Column("ORA_SFARSIT", sa.Time(), nullable=False),
        sa.Column("SALA", sa.Integer(), sa.ForeignKey("SALA.ID_SALA", ondelete="SET NULL")),
        sa.Column("LOC", sa.String(160)),
        sa.Column("DATA_INCEPUT", sa.Date(), nullable=False),
        sa.Column("DATA_SFARSIT", sa.Date(), nullable=False),
        sa.Column("FRECVENTA", sa.String(4)),
        sa.Column("SPECIALIZARI", sa.String(200)),
        sa.Column("ANI", sa.String(40)),
        sa.Column("SERII", sa.String(300)),
        sa.Column("LINK_WHATSAPP", sa.String(300)),
        sa.Column("LINK_TEAMS", sa.String(300)),
        sa.Column("DESCRIERE", sa.Text()),
        sa.Column("STARE", sa.String(10), nullable=False, server_default="ciorna"),
        sa.Column("CREAT_DE", sa.String(120), nullable=False),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
        sa.CheckConstraint("STARE IN ('ciorna','publicat','incheiat')", name="ck_tutoriat_stare"),
        sa.CheckConstraint("DATA_INCEPUT <= DATA_SFARSIT", name="ck_tutoriat_perioada"),
        sa.CheckConstraint("ORA_INCEPUT < ORA_SFARSIT", name="ck_tutoriat_ore"),
    )
    op.create_index("ix_tutoriat_slot", "TUTORIAT", ["AN_UNIV", "SEMESTRU", "ZI_SAPTAMANA"])
    op.create_table(
        "TUTORIAT_TUTOR",
        sa.Column(
            "ID_TUTORIAT",
            sa.Integer(),
            sa.ForeignKey("TUTORIAT.ID", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "ID_USER", sa.Integer(), sa.ForeignKey("USER.ID", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("PRINCIPAL", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.create_index("ix_TUTORIAT_TUTOR_ID_USER", "TUTORIAT_TUTOR", ["ID_USER"])
    op.create_table(
        "TUTORIAT_ANULARE",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column(
            "ID_TUTORIAT",
            sa.Integer(),
            sa.ForeignKey("TUTORIAT.ID", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("DATA", sa.Date(), nullable=False),
        sa.Column("MOTIV", sa.String(200)),
        sa.Column("DE_CATRE", sa.String(120), nullable=False),
        sa.UniqueConstraint("ID_TUTORIAT", "DATA", name="uq_anulare"),
    )
    op.create_index("ix_TUTORIAT_ANULARE_ID_TUTORIAT", "TUTORIAT_ANULARE", ["ID_TUTORIAT"])


def downgrade() -> None:
    for tabela in ("TUTORIAT_ANULARE", "TUTORIAT_TUTOR", "TUTORIAT", "TUTOR", "PERMISIUNE"):
        op.drop_table(tabela)
    op.execute('ALTER TABLE "USER" DROP COLUMN "TUTORIATE_ALESE"')
    op.execute('ALTER TABLE "SALA" DROP COLUMN "DOAR_TUTORIATE"')
