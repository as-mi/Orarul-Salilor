"""felul tutoriate in SURSA_ORAR

Orarul tutoriatelor e al treilea orar publicat de facultate si isi tine si el starea per
sursa (`semestru x fel`), ca watcher-ul sa nu reia captura si ca panoul sa arate cand a
fost actualizat. CHECK-ul vechi permitea doar `grupe` si `profesori`; SQLite nu stie
ALTER TABLE ... DROP CONSTRAINT, deci batch reconstruieste tabela.

Revision ID: c4f1a9e73b20
Revises: b7a8c9d0e1f2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4f1a9e73b20"
down_revision = "b7a8c9d0e1f2"
branch_labels = None
depends_on = None


def _tabela(valori_fel: str) -> sa.Table:
    """Definitia tabelului dupa reconstructie (batch copiaza datele in ea)."""
    return sa.Table(
        "SURSA_ORAR",
        sa.MetaData(),
        sa.Column("ID_SURSA", sa.Integer(), nullable=False),
        sa.Column("AN_UNIV", sa.String(length=9), nullable=False),
        sa.Column("SEMESTRU", sa.Integer(), nullable=False),
        sa.Column("FEL", sa.String(length=12), nullable=False),
        sa.Column("URL", sa.String(length=255), nullable=False),
        sa.Column("ACTUALIZAT", sa.DateTime(), nullable=True),
        sa.Column("VERIFICAT_LA", sa.DateTime(), nullable=True),
        sa.Column("INGESTAT_LA", sa.DateTime(), nullable=True),
        sa.CheckConstraint(f"FEL IN ({valori_fel})", name="ck_sursa_fel"),
        sa.CheckConstraint("SEMESTRU IN (1,2)", name="ck_sursa_semestru"),
        sa.PrimaryKeyConstraint("ID_SURSA"),
        sa.UniqueConstraint("AN_UNIV", "SEMESTRU", "FEL", name="uq_sursa"),
    )


def upgrade() -> None:
    with op.batch_alter_table(
        "SURSA_ORAR",
        copy_from=_tabela("'grupe','profesori','tutoriate'"),
        recreate="always",
    ):
        pass


def downgrade() -> None:
    # Randurile sursei de tutoriate nu incap in vechiul CHECK: le scoatem inainte de
    # reconstructie, altfel copierea datelor ar pica.
    op.execute(sa.text("DELETE FROM SURSA_ORAR WHERE FEL = 'tutoriate'"))
    with op.batch_alter_table(
        "SURSA_ORAR",
        copy_from=_tabela("'grupe','profesori'"),
        recreate="always",
    ):
        pass
