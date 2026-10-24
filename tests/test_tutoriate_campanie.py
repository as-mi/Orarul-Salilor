"""Podul campaniilor de tutoriate: intrarea schedulerului din "Orarul in cifre",
parsarea disponibilitatii si materializarea unei sesiuni confirmate in `Tutoriat`."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from orar.db.models import (
    AplicatieTutor,
    Base,
    CampanieTutoriate,
    EchipaTutoriat,
    EchipaTutoriatMembru,
    PosturiDeschise,
    SesiuneTutoriat,
    StructuraFormular,
    Tutoriat,
    TutoriatTutor,
    User,
)
from orar.db.orare import perioada_implicita
from orar.db.tutoriate_campanie import (
    disponibilitate_tutor,
    incarca_json,
    materializeaza_sesiune,
    ocupare_public,
    scrie_json,
)
from orar.domain.grid import ZILE
from orar.ingest.consolidate import consolideaza
from orar.ingest.load import incarca_pagini
from tests.conftest import _engine_memorie

LUNI = date(2026, 3, 2)


@pytest.fixture
def s(pagini_golden):
    engine = _engine_memorie()

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as sesiune:
        incarca_pagini(sesiune, pagini_golden)
        consolideaza(sesiune, an_universitar="2025-2026")
        sesiune.commit()
        yield sesiune


def _campanie(s, **campuri) -> CampanieTutoriate:
    c = CampanieTutoriate(
        an_univ="2025-2026",
        semestru=2,
        nume="Tutoriate semestrul II",
        data_deschidere=LUNI,
        data_inchidere=LUNI,
        data_inceput=LUNI,
        data_sfarsit=LUNI,
        creat_de="test",
        creat_la=datetime(2026, 2, 1),
        **campuri,
    )
    s.add(c)
    s.commit()
    return c


def _post(s, campanie, **campuri) -> PosturiDeschise:
    p = PosturiDeschise(
        campanie_id=campanie.id,
        materie="Programare orientată pe obiecte",
        creat_de="test",
        creat_la=datetime(2026, 2, 1),
        **campuri,
    )
    s.add(p)
    s.commit()
    return p


def _echipa(s, campanie, post, nume: list[str], *, principal=0) -> EchipaTutoriat:
    e = EchipaTutoriat(
        campanie_id=campanie.id,
        post_id=post.id,
        creat_de="test",
        creat_la=datetime(2026, 2, 1),
    )
    for i, n in enumerate(nume):
        u = User(nume=n)
        s.add(u)
        s.flush()
        e.membri.append(
            EchipaTutoriatMembru(user_id=u.id, principal=(i == principal), ordine=i)
        )
    s.add(e)
    s.commit()
    return e


def test_ocupare_public_umple_grila(s):
    post = _post(s, _campanie(s))  # fara filtre => tot publicul
    public = ocupare_public(s, perioada_implicita(s), post)
    assert public.populatie > 0
    assert set(public.ocupare) == {(zi, h) for zi in ZILE for h in range(8, 20)}
    # dominantele au aceleasi chei; unele numesc cursul dominant
    assert set(public.dominante) == set(public.ocupare)


def test_ocupare_public_ia_varful_dintre_saptamani(s):
    post = _post(s, _campanie(s))
    public = ocupare_public(s, perioada_implicita(s), post)
    # niciun ceas nu poate tine mai multi oameni decat tot publicul
    assert all(n <= public.populatie for n in public.ocupare.values())


def test_disponibilitate_tutor_parseaza_grila(s):
    ap = AplicatieTutor(disponibilitate=scrie_json({"Luni": [8, 9, 10], "Marti": [14, 15]}))
    disp = disponibilitate_tutor(ap)
    assert disp == {"Luni": {8, 9, 10}, "Marti": {14, 15}}


def test_disponibilitate_tutor_ignora_stricaturi(s):
    assert disponibilitate_tutor(AplicatieTutor(disponibilitate="{stricat")) == {}
    assert disponibilitate_tutor(AplicatieTutor(disponibilitate=None)) == {}
    assert disponibilitate_tutor(AplicatieTutor(disponibilitate=scrie_json(["nu", "dict"]))) == {}


def test_materializeaza_sesiune_creaza_tutoriat_si_membri(s):
    campanie = _campanie(s)
    post = _post(s, campanie, specializari="I1,I2", ani="L1", serii="s1")
    echipa = _echipa(s, campanie, post, ["Ana", "Dan"], principal=1)

    sesiune = SesiuneTutoriat(
        campanie_id=campanie.id,
        post_id=post.id,
        echipa_id=echipa.id,
        zi_saptamana="Joi",
        ora_inceput=time(14),
        ora_sfarsit=time(16),
        acoperire=85,
        explicatie="Joi 14:00 — lipit de curs, acoperă 85% din public",
        stare="propusa",
        creat_de="test",
        creat_la=datetime(2026, 2, 1),
    )
    s.add(sesiune)
    s.commit()

    tutoriat = materializeaza_sesiune(s, sesiune, de_catre="test")
    s.commit()

    assert sesiune.stare == "confirmata"
    assert sesiune.tutoriat_id == tutoriat.id
    assert tutoriat.materie == post.materie
    assert tutoriat.specializari == "I1,I2"
    assert tutoriat.stare == "publicat"
    assert tutoriat.zi_saptamana == "Joi"

    membri = s.scalars(
        select(TutoriatTutor).where(TutoriatTutor.tutoriat_id == tutoriat.id)
    ).all()
    assert len(membri) == 2
    # principal e "Dan" (ordine 1)
    assert [m.principal for m in membri] == [False, True]
