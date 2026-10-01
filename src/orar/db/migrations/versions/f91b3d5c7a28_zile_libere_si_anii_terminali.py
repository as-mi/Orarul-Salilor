"""structura anului: zile libere si perioade doar pentru anii terminali sau neterminali

Constrangerea pe FEL se schimba, deci tabela se reconstruieste (batch). Se poate: nicio alta
tabela nu trimite la STRUCTURA_AN.

Revision ID: f91b3d5c7a28
Revises: e8c27a4f9d16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f91b3d5c7a28"
down_revision = "e8c27a4f9d16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("STRUCTURA_AN", recreate="always") as t:
        t.drop_constraint("ck_structura_fel", type_="check")
        t.add_column(sa.Column("PENTRU", sa.String(12), nullable=False, server_default="toti"))
        t.create_check_constraint(
            "ck_structura_fel",
            "FEL IN ('didactica','vacanta','sesiune','restante','licenta','liber')",
        )
        t.create_check_constraint(
            "ck_structura_pentru", "PENTRU IN ('toti','neterminali','terminali')"
        )


def downgrade() -> None:
    op.execute("""DELETE FROM "STRUCTURA_AN" WHERE "FEL" = 'liber'""")
    with op.batch_alter_table("STRUCTURA_AN", recreate="always") as t:
        t.drop_constraint("ck_structura_pentru", type_="check")
        t.drop_constraint("ck_structura_fel", type_="check")
        t.drop_column("PENTRU")
        t.create_check_constraint(
            "ck_structura_fel", "FEL IN ('didactica','vacanta','sesiune','restante','licenta')"
        )
