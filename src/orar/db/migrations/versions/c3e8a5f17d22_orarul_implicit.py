"""orarul implicit

Baza poate tine mai multe orare deodata -- cate unul pe (an universitar, semestru) -- iar
situl arata unul singur: cel implicit, ales de un admin, sau cel pe care si-l alege
vizitatorul din panoul de versiuni. `PERIOADA.IMPLICITA` tine alegerea adminului.

Fara `batch_alter_table`: ar reconstrui PERIOADA, iar ORA o refera.

Revision ID: c3e8a5f17d22
Revises: b7d2c41e9a05
Create Date: 2026-09-30 12:40:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3e8a5f17d22"
down_revision: Union[str, Sequence[str], None] = "b7d2c41e9a05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('ALTER TABLE "PERIOADA" ADD COLUMN "IMPLICITA" BOOLEAN NOT NULL DEFAULT 0')


def downgrade() -> None:
    op.execute('ALTER TABLE "PERIOADA" DROP COLUMN "IMPLICITA"')
