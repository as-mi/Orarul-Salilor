"""conturile raman doar pentru admin

Studentii nu mai au conturi: ce vor sa vada se tine in cookie. USER ramane -- e in schema
ceruta -- dar doar pentru administratori:

  - USER_OPTIONAL dispare, la fel USER.ID_GRUPA si USER.SEMIGRUPA (preferintele);
  - USER.NUME devine unic: e numele cu care se intra pe /admin;
  - conturile de student existente se sterg.

Pasul a fost revizuit de migrarea urmatoare (`6c1f0a7d2e41`): conturile au revenit, iar
adminul principal vine din mediu (`ORAR_ADMIN_USER` / `ORAR_ADMIN_PAROLA`), nu din baza.
O versiune mai veche a acestei migrari crea aici un cont de admin; cea urmatoare il sterge.

Revision ID: 28e38d5d6abb
Revises: 9242bdc586c9
Create Date: 2026-09-29 21:15:55.324082

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "28e38d5d6abb"
down_revision: Union[str, Sequence[str], None] = "9242bdc586c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_user = sa.table("USER", sa.column("NUME", sa.String))


def upgrade() -> None:
    op.drop_table("USER_OPTIONAL")

    # Conturile de student nu mai au ce tine; numele lor ar putea si sa se repete, ceea ce
    # ar strica constrangerea de unicitate de mai jos.
    op.execute(_user.delete())

    # SQLite nu stie ALTER TABLE ... DROP CONSTRAINT: batch reconstruieste tabela. Cheia
    # straina spre GRUPA pleaca odata cu coloana ei.
    with op.batch_alter_table("USER", schema=None) as batch_op:
        batch_op.drop_index("ix_USER_ID_GRUPA")
        batch_op.drop_column("ID_GRUPA")
        batch_op.drop_column("SEMIGRUPA")
        batch_op.create_unique_constraint("uq_user_nume", ["NUME"])


def downgrade() -> None:
    with op.batch_alter_table("USER", schema=None) as batch_op:
        batch_op.drop_constraint("uq_user_nume", type_="unique")
        batch_op.add_column(sa.Column("SEMIGRUPA", sa.VARCHAR(length=8), nullable=True))
        batch_op.add_column(sa.Column("ID_GRUPA", sa.INTEGER(), nullable=True))
        batch_op.create_foreign_key(
            "fk_user_grupa", "GRUPA", ["ID_GRUPA"], ["ID_GRUPA"], ondelete="SET NULL"
        )
        batch_op.create_index("ix_USER_ID_GRUPA", ["ID_GRUPA"], unique=False)

    op.create_table(
        "USER_OPTIONAL",
        sa.Column("ID_USER", sa.INTEGER(), nullable=False),
        sa.Column("ID_GRUPA", sa.INTEGER(), nullable=False),
        sa.ForeignKeyConstraint(["ID_GRUPA"], ["GRUPA.ID_GRUPA"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ID_USER"], ["USER.ID"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ID_USER", "ID_GRUPA"),
    )
