"""corectia tine activitatea intreaga

Un admin poate modifica acum orice dintr-o activitate, nu doar profesorul si sala. Corectia
tine deci activitatea intreaga cum era in orarul publicat, ca JSON in `ORIGINAL`, in locul
celor doua coloane `PROFESOR_VECHI` / `SALA_VECHE`.

Fara `batch_alter_table`: batch ar reconstrui CORECTIE, iar stergerea tabelei vechi, cu
`PRAGMA foreign_keys=ON`, ar goli `ORA.ID_CORECTIE` (ON DELETE SET NULL) -- adica ar rupe
fiecare corectie de activitatea ei. SQLite >= 3.35 adauga si sterge coloane direct.

Revision ID: b7d2c41e9a05
Revises: 9fa0364012f9
Create Date: 2026-09-30 12:10:00.000000

"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7d2c41e9a05"
down_revision: Union[str, Sequence[str], None] = "9fa0364012f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CAMPURI = ("GRUPA_SLUG", "ZI", "ORA_INCEPUT", "ORA_SFARSIT", "MATERIE", "TIP", "SEMIGRUPA")
_CAMPURI += ("FRECVENTA", "SAPTAMANI")


def _ora(text) -> str:  # noqa: ANN001
    """`10:00:00.000000` -> `10:00`."""
    return str(text)[:5]


def upgrade() -> None:
    op.execute('ALTER TABLE "CORECTIE" ADD COLUMN "ORIGINAL" TEXT')

    # Modificarile existente: pana acum coloanele spuneau *care* activitate, iar cele doua
    # `_VECHI` ce profesor si ce sala avea. Tot ce era, in afara de profesor si sala, e si ce
    # este -- deci originalul se reconstituie din ele.
    conn = op.get_bind()
    coloane = ", ".join(f'"{c}"' for c in (*_CAMPURI, "PROFESOR_VECHI", "SALA_VECHE"))
    for rand in conn.execute(
        sa.text(f'SELECT "ID_CORECTIE", {coloane} FROM "CORECTIE" WHERE "FEL" = \'modificare\'')
    ).mappings():
        original = {
            "grupa_slug": rand["GRUPA_SLUG"],
            "zi": rand["ZI"],
            "ora_inceput": _ora(rand["ORA_INCEPUT"]),
            "ora_sfarsit": _ora(rand["ORA_SFARSIT"]),
            "materie": rand["MATERIE"],
            "tip": rand["TIP"],
            "semigrupa": rand["SEMIGRUPA"],
            "frecventa": rand["FRECVENTA"],
            "saptamani": rand["SAPTAMANI"],
            "profesor": rand["PROFESOR_VECHI"],
            "sala": rand["SALA_VECHE"],
            "legaturi": [],
        }
        conn.execute(
            sa.text('UPDATE "CORECTIE" SET "ORIGINAL" = :o WHERE "ID_CORECTIE" = :id'),
            {"o": json.dumps(original, ensure_ascii=False), "id": rand["ID_CORECTIE"]},
        )

    op.execute('ALTER TABLE "CORECTIE" DROP COLUMN "PROFESOR_VECHI"')
    op.execute('ALTER TABLE "CORECTIE" DROP COLUMN "SALA_VECHE"')


def downgrade() -> None:
    op.execute('ALTER TABLE "CORECTIE" ADD COLUMN "PROFESOR_VECHI" VARCHAR(120)')
    op.execute('ALTER TABLE "CORECTIE" ADD COLUMN "SALA_VECHE" VARCHAR(80)')
    conn = op.get_bind()
    for rand in conn.execute(
        sa.text('SELECT "ID_CORECTIE", "ORIGINAL" FROM "CORECTIE" WHERE "ORIGINAL" IS NOT NULL')
    ).mappings():
        original = json.loads(rand["ORIGINAL"])
        conn.execute(
            sa.text(
                'UPDATE "CORECTIE" SET "PROFESOR_VECHI" = :p, "SALA_VECHE" = :s '
                'WHERE "ID_CORECTIE" = :id'
            ),
            {"p": original.get("profesor"), "s": original.get("sala"), "id": rand["ID_CORECTIE"]},
        )
    op.execute('ALTER TABLE "CORECTIE" DROP COLUMN "ORIGINAL"')
