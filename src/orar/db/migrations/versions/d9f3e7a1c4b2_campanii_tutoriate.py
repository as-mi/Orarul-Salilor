"""campanii de tutoriate: campanie, formular dinamic, posturi, aplicatii, echipe si sesiuni

Adauga pipeline-ul de recrutare si planificare in fata tutoriatelor existente. Sesiunea
confirmata se leaga de un `TUTORIAT` (deja existent), deci nu se modifica tabelele vechi.

Revision ID: d9f3e7a1c4b2
Revises: c3a7d9e2f104
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d9f3e7a1c4b2"
down_revision = "c3a7d9e2f104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "CAMPANIE_TUTORIATE",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column("AN_UNIV", sa.String(9), nullable=False),
        sa.Column("SEMESTRU", sa.Integer(), nullable=False),
        sa.Column("NUME", sa.String(160), nullable=False),
        sa.Column("DATA_DESCHIDERE", sa.Date(), nullable=False),
        sa.Column("DATA_INCHIDERE", sa.Date(), nullable=False),
        sa.Column("DATA_INCEPUT", sa.Date(), nullable=False),
        sa.Column("DATA_SFARSIT", sa.Date(), nullable=False),
        sa.Column("INDULGENTA", sa.Integer(), nullable=False, server_default="75"),
        sa.Column("MIN_TUTORI_ECHIPA", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("MAX_TUTORI_ECHIPA", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("DURATA_SESIUNE", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("STARE", sa.String(16), nullable=False, server_default="ciorna"),
        sa.Column("CREAT_DE", sa.String(120), nullable=False),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "STARE IN ('ciorna','deschisa','inchisa','planificata','arhivata')",
            name="ck_campanie_stare",
        ),
        sa.CheckConstraint("SEMESTRU IN (1,2)", name="ck_campanie_semestru"),
        sa.CheckConstraint("DATA_DESCHIDERE <= DATA_INCHIDERE", name="ck_campanie_aplicare"),
        sa.CheckConstraint("DATA_INCEPUT <= DATA_SFARSIT", name="ck_campanie_perioada"),
        sa.CheckConstraint("INDULGENTA BETWEEN 0 AND 100", name="ck_campanie_indulgenta"),
        sa.CheckConstraint("MIN_TUTORI_ECHIPA BETWEEN 1 AND 3", name="ck_campanie_min_tutori"),
        sa.CheckConstraint("MAX_TUTORI_ECHIPA BETWEEN 1 AND 3", name="ck_campanie_max_tutori"),
        sa.CheckConstraint("MIN_TUTORI_ECHIPA <= MAX_TUTORI_ECHIPA", name="ck_campanie_tutori"),
        sa.CheckConstraint("DURATA_SESIUNE BETWEEN 1 AND 6", name="ck_campanie_durata"),
    )
    op.create_index("ix_campanie_an_sem", "CAMPANIE_TUTORIATE", ["AN_UNIV", "SEMESTRU"])

    op.create_table(
        "STRUCTURA_FORMULAR",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column(
            "ID_CAMPANIE",
            sa.Integer(),
            sa.ForeignKey("CAMPANIE_TUTORIATE.ID", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("INTREBARI", sa.Text(), nullable=False),
        sa.Column("CREAT_DE", sa.String(120), nullable=False),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "POSTURI_DESCHISE",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column(
            "ID_CAMPANIE",
            sa.Integer(),
            sa.ForeignKey("CAMPANIE_TUTORIATE.ID", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("MATERIE", sa.String(160), nullable=False),
        sa.Column("DESCRIERE", sa.Text()),
        sa.Column("SPECIALIZARI", sa.String(200)),
        sa.Column("ANI", sa.String(40)),
        sa.Column("SERII", sa.String(300)),
        sa.Column("NR_MAX_TUTORI", sa.Integer()),
        sa.Column("CREAT_DE", sa.String(120), nullable=False),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_posturi_campanie", "POSTURI_DESCHISE", ["ID_CAMPANIE"])

    op.create_table(
        "APLICATIE_TUTOR",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column(
            "ID_CAMPANIE",
            sa.Integer(),
            sa.ForeignKey("CAMPANIE_TUTORIATE.ID", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ID_USER", sa.Integer(), sa.ForeignKey("USER.ID", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("RASPUNSURI", sa.Text()),
        sa.Column("DISPONIBILITATE", sa.Text()),
        sa.Column("MATERII", sa.Text()),
        sa.Column("PREFERINTE_CO_TUTORI", sa.Text()),
        sa.Column("STARE", sa.String(12), nullable=False, server_default="aplicata"),
        sa.Column("CREAT_DE", sa.String(120), nullable=False),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("ID_CAMPANIE", "ID_USER", name="uq_aplicatie_campanie_user"),
        sa.CheckConstraint(
            "STARE IN ('aplicata','acceptata','respinsa','retrasa')", name="ck_aplicatie_stare"
        ),
    )
    op.create_index("ix_aplicatie_campanie", "APLICATIE_TUTOR", ["ID_CAMPANIE"])
    op.create_index("ix_aplicatie_user", "APLICATIE_TUTOR", ["ID_USER"])

    op.create_table(
        "ECHIPA_TUTORIAT",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column(
            "ID_CAMPANIE",
            sa.Integer(),
            sa.ForeignKey("CAMPANIE_TUTORIATE.ID", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ID_POST",
            sa.Integer(),
            sa.ForeignKey("POSTURI_DESCHISE.ID", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("NUME", sa.String(160)),
        sa.Column("STARE", sa.String(12), nullable=False, server_default="propusa"),
        sa.Column("CREAT_DE", sa.String(120), nullable=False),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
        sa.CheckConstraint("STARE IN ('propusa','confirmata')", name="ck_echipa_stare"),
    )
    op.create_index("ix_echipa_campanie", "ECHIPA_TUTORIAT", ["ID_CAMPANIE"])
    op.create_index("ix_echipa_post", "ECHIPA_TUTORIAT", ["ID_POST"])

    op.create_table(
        "ECHIPA_TUTORIAT_MEMBRU",
        sa.Column(
            "ID_ECHIPA",
            sa.Integer(),
            sa.ForeignKey("ECHIPA_TUTORIAT.ID", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "ID_USER", sa.Integer(), sa.ForeignKey("USER.ID", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("PRINCIPAL", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("ORDINE", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_echipa_membru_user", "ECHIPA_TUTORIAT_MEMBRU", ["ID_USER"])

    op.create_table(
        "SESIUNE_TUTORIAT",
        sa.Column("ID", sa.Integer(), primary_key=True),
        sa.Column(
            "ID_CAMPANIE",
            sa.Integer(),
            sa.ForeignKey("CAMPANIE_TUTORIATE.ID", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ID_POST",
            sa.Integer(),
            sa.ForeignKey("POSTURI_DESCHISE.ID", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ID_ECHIPA",
            sa.Integer(),
            sa.ForeignKey("ECHIPA_TUTORIAT.ID", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ZI_SAPTAMANA", sa.String(10), nullable=False),
        sa.Column("ORA_INCEPUT", sa.Time(), nullable=False),
        sa.Column("ORA_SFARSIT", sa.Time(), nullable=False),
        sa.Column("SALA", sa.Integer(), sa.ForeignKey("SALA.ID_SALA", ondelete="SET NULL")),
        sa.Column("LOC", sa.String(160)),
        sa.Column("ACOPERIRE", sa.Integer()),
        sa.Column("EXPLICATIE", sa.Text()),
        sa.Column("STARE", sa.String(12), nullable=False, server_default="propusa"),
        sa.Column("ID_TUTORIAT", sa.Integer(), sa.ForeignKey("TUTORIAT.ID", ondelete="SET NULL")),
        sa.Column("CREAT_DE", sa.String(120), nullable=False),
        sa.Column("CREAT_LA", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "STARE IN ('propusa','confirmata','respinsa')", name="ck_sesiune_stare"
        ),
        sa.CheckConstraint("ORA_INCEPUT < ORA_SFARSIT", name="ck_sesiune_ore"),
    )
    op.create_index("ix_sesiune_campanie", "SESIUNE_TUTORIAT", ["ID_CAMPANIE"])
    op.create_index("ix_sesiune_post", "SESIUNE_TUTORIAT", ["ID_POST"])
    op.create_index("ix_sesiune_echipa", "SESIUNE_TUTORIAT", ["ID_ECHIPA"])


def downgrade() -> None:
    for tabela in (
        "SESIUNE_TUTORIAT",
        "ECHIPA_TUTORIAT_MEMBRU",
        "ECHIPA_TUTORIAT",
        "APLICATIE_TUTOR",
        "POSTURI_DESCHISE",
        "STRUCTURA_FORMULAR",
        "CAMPANIE_TUTORIATE",
    ):
        op.drop_table(tabela)
