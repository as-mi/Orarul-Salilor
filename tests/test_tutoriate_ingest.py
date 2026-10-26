"""Ingestia orarului de tutoriate: celule albe, o pagina per serie."""

from __future__ import annotations

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker

from orar.db.models import Base, Tutoriat
from orar.ingest.tutoriate import CREAT_DE_ORAR, incarca_tutoriate
from tests.conftest import _engine_memorie


@pytest.fixture
def s():
    engine = _engine_memorie()

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as sesiune:
        yield sesiune


def _pagini() -> list[dict]:
    return [
        {
            "titlu": "Tutoriat (MATE seria 10)",
            "activitati": [
                {"zi": "Luni", "ore": "18-20", "sala": "S.209", "brut": ["Algebra I", "S.209"]},
                {"zi": "Joi", "ore": "18-20", "sala": "S.210", "brut": ["Geometrie I", "S.210"]},
            ],
        },
        {
            "titlu": "Tutoriat (INFO serile 13, 14, 15)",
            "activitati": [
                {"zi": "Marti", "ore": "8-10", "sala": "S.209", "brut": ["ProgAlgo", "S.209"]},
            ],
        },
    ]


def test_incarca_tutoriatele_pe_serie(s):
    rap = incarca_tutoriate(s, _pagini(), an_universitar="2025-2026", semestru=1)
    assert rap.pagini == 2 and rap.tutoriate == 3

    lista = list(s.scalars(select(Tutoriat).order_by(Tutoriat.materie)))
    assert [t.materie for t in lista] == ["Algebra I", "Geometrie I", "ProgAlgo"]
    assert [t.serii for t in lista] == ["seria-10", "seria-10", "seria-13,seria-14,seria-15"]
    assert [t.specializari for t in lista] == ["MATE", "MATE", "INFO"]
    assert all(t.stare == "publicat" and t.creat_de == CREAT_DE_ORAR for t in lista)
    assert [t.zi_saptamana for t in lista] == ["Luni", "Joi", "Marti"]


def test_reincarcarea_nu_dubleaza_tutoriatele_din_orar(s):
    incarca_tutoriate(s, _pagini(), an_universitar="2025-2026", semestru=1)
    incarca_tutoriate(s, _pagini(), an_universitar="2025-2026", semestru=1)
    assert s.scalar(select(func.count()).select_from(Tutoriat)) == 3
