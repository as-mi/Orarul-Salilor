"""conturi cu rol, grupa si preferinte

Conturile revin: oricine isi poate face unul, isi alege grupa si isi tine in cont orarul
propriu -- semigrupa si optionalele/facultativele alese.

  - USER.ROL: student (implicit) | voluntar | profesor | admin, setat din /admin;
  - USER.ID_GRUPA, USER.SEMIGRUPA, USER.ASCUNSE: grupa contului si alegerile de pe orarul ei;
  - USER.NUME nu mai e unic: autentificarea se face cu emailul, iar doi oameni pot avea
    acelasi nume;
  - dispar randurile fara email -- contul de admin creat de o versiune anterioara a migrarii
    precedente. Adminul principal vine acum din mediu (`ORAR_ADMIN_USER`), nu din baza.

Revision ID: 6c1f0a7d2e41
Revises: 28e38d5d6abb
Create Date: 2026-09-30 10:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6c1f0a7d2e41"
down_revision: Union[str, Sequence[str], None] = "28e38d5d6abb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_user = sa.table("USER", sa.column("EMAIL", sa.String))


def upgrade() -> None:
    op.execute(_user.delete().where(_user.c.EMAIL.is_(None)))

    with op.batch_alter_table("USER", schema=None) as batch_op:
        batch_op.drop_constraint("uq_user_nume", type_="unique")
        batch_op.add_column(
            sa.Column("ROL", sa.String(length=12), nullable=False, server_default="student")
        )
        batch_op.add_column(sa.Column("ID_GRUPA", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("SEMIGRUPA", sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column("ASCUNSE", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_user_grupa", "GRUPA", ["ID_GRUPA"], ["ID_GRUPA"], ondelete="SET NULL"
        )
        batch_op.create_index("ix_USER_ID_GRUPA", ["ID_GRUPA"], unique=False)
        batch_op.create_check_constraint(
            "ck_user_rol", "ROL IN ('student','voluntar','profesor','admin')"
        )


def downgrade() -> None:
    with op.batch_alter_table("USER", schema=None) as batch_op:
        batch_op.drop_constraint("ck_user_rol", type_="check")
        batch_op.drop_index("ix_USER_ID_GRUPA")
        batch_op.drop_constraint("fk_user_grupa", type_="foreignkey")
        batch_op.drop_column("ASCUNSE")
        batch_op.drop_column("SEMIGRUPA")
        batch_op.drop_column("ID_GRUPA")
        batch_op.drop_column("ROL")
        batch_op.create_unique_constraint("uq_user_nume", ["NUME"])
