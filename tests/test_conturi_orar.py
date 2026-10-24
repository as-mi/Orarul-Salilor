"""Orarul urmat de un cont: grupele din care alege, maparea grupei 1 la 1 cand orarul e din
alt an, ramanerea pe un orar anume si resetarea grupelor la un an universitar nou."""

from __future__ import annotations

import re
from datetime import date, time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from orar.db.conturi import (
    grupa_in_orar,
    grupe_de_ales,
    orarul_contului,
    reseteaza_grupele_daca_e_cazul,
)
from orar.db.models import Base, Grupa, Ora, Perioada, PerioadaStructura, Setare, SursaOrar, User
from orar.web.auth import hash_parola
from tests.conftest import _engine_memorie, intra_sso

EMAIL, PAROLA = "ana@example.com", "parola-anei-lunga"
VECHI, NOU = "2025-2026", "2026-2027"


@pytest.fixture
def s():
    """Doi ani universitari, cu cate un orar si aceleasi grupe (244, 344) in fiecare."""
    engine = _engine_memorie()
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as sesiune:
        for an, semestru, publicat in ((VECHI, 2, date(2026, 3, 1)), (NOU, 1, date(2026, 10, 1))):
            perioada = Perioada(an_univ=an, semestru=semestru)
            sesiune.add(perioada)
            sesiune.add(
                SursaOrar(an_univ=an, semestru=semestru, fel="grupe", url="x", ingestat_la=publicat)
            )
            for nume in ("244", "344"):
                g = Grupa(nume=nume, slug=nume, tip="grupa", an_universitar=an)
                sesiune.add(g)
                sesiune.add(
                    Ora(
                        grupa=g,
                        perioada=perioada,
                        zi_saptamana="Luni",
                        ora_inceput=time(8),
                        ora_sfarsit=time(10),
                    )
                )
        sesiune.add(User(nume="Ana Pop", email=EMAIL, parola_hash=hash_parola(PAROLA)))
        sesiune.commit()
        yield sesiune


@pytest.fixture
def client(s, sso_fals):
    from fastapi.testclient import TestClient

    from orar.web import deps
    from orar.web.app import app

    app.dependency_overrides[deps.get_db] = lambda: s
    with TestClient(app) as c:
        intra_sso(c, sso_fals, email=EMAIL)
        yield c
    app.dependency_overrides.clear()


def _csrf(client, cale: str) -> str:  # noqa: ANN001
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


def _grupa(s, nume: str, an: str) -> Grupa:  # noqa: ANN001
    return s.scalar(select(Grupa).where(Grupa.nume == nume, Grupa.an_universitar == an))


def _perioada(s, an: str) -> Perioada:  # noqa: ANN001
    return s.scalar(select(Perioada).where(Perioada.an_univ == an))


def _ana(s) -> User:  # noqa: ANN001
    s.expire_all()
    return s.scalar(select(User).where(User.email == EMAIL))


def _salveaza(client, **campuri):  # noqa: ANN001, ANN003, ANN202
    return client.post(
        "/account", data={"csrf": _csrf(client, "/account"), **campuri}, follow_redirects=False
    )


def test_se_alege_doar_din_grupele_orarului_urmat(client, s):
    # orarul implicit e cel incarcat cel mai recent: anul nou
    assert {g.an_universitar for g in grupe_de_ales(s, orarul_contului(s, None))} == {NOU}
    pagina = client.get("/account").text
    lista = pagina[pagina.index('name="grupa_id"') :]
    lista = lista[: lista.index("</select>")]
    assert f'value="{_grupa(s, "244", NOU).id}"' in lista
    assert f'value="{_grupa(s, "244", VECHI).id}"' not in lista
    # o grupa din alt an, trimisa de mana, nu e primita
    _salveaza(client, orar="automat", grupa_id=str(_grupa(s, "244", VECHI).id))
    assert _ana(s).grupa_id is None


def test_grupa_se_muta_1_la_1_cand_orarul_e_din_alt_an(client, s):
    ana = _ana(s)
    ana.grupa, ana.semigrupa, ana.ascunse = _grupa(s, "244", VECHI), "Gr_1", '["algavans"]'
    s.commit()
    assert grupa_in_orar(s, ana.grupa, _perioada(s, NOU)) == _grupa(s, "244", NOU)

    r = client.get("/orarul-meu", follow_redirects=False)
    assert r.headers["location"] == "/grupa/244"
    ana = _ana(s)
    # aceeasi grupa, in anul orarului urmat, cu preferintele pastrate
    assert (ana.grupa.an_universitar, ana.semigrupa, ana.ascunse) == (NOU, "Gr_1", '["algavans"]')


def test_contul_poate_ramane_pe_un_orar_anume(client, s):
    vechi = _perioada(s, VECHI)
    _salveaza(client, orar=str(vechi.id), grupa_id=str(_grupa(s, "244", VECHI).id))
    ana = _ana(s)
    assert (ana.orar_automat, ana.orar_ales_id, ana.grupa.an_universitar) == (
        False,
        vechi.id,
        VECHI,
    )
    # "Orarul meu" deschide grupa in orarul ales, nu in cel implicit
    assert (
        client.get("/orarul-meu", follow_redirects=False).headers["location"]
        == f"/grupa/244?perioada={vechi.id}"
    )

    # inapoi pe automat: grupa trece in anul orarului curent, preferintele raman
    ana.semigrupa = "Gr_2"
    s.commit()
    _salveaza(client, orar="automat", grupa_id=str(_grupa(s, "244", NOU).id))
    ana = _ana(s)
    assert (ana.orar_automat, ana.grupa.an_universitar, ana.semigrupa) == (True, NOU, "Gr_2")


def _structura_completa(s, an: str) -> None:  # noqa: ANN001
    inceput = int(an[:4])
    for fel, semestru, a, b in (
        ("didactica", 1, date(inceput, 10, 1), date(inceput, 12, 20)),
        ("didactica", 2, date(inceput + 1, 2, 22), date(inceput + 1, 6, 4)),
        ("vacanta", None, date(inceput, 12, 21), date(inceput + 1, 1, 10)),
        ("sesiune", 1, date(inceput + 1, 1, 25), date(inceput + 1, 2, 14)),
        ("restante", None, date(inceput + 1, 9, 1), date(inceput + 1, 9, 10)),
        ("licenta", None, date(inceput + 1, 7, 1), date(inceput + 1, 7, 5)),
    ):
        s.add(
            PerioadaStructura(
                an_univ=an, fel=fel, semestru=semestru, data_inceput=a, data_sfarsit=b
            )
        )
    s.commit()


def test_anul_nou_complet_reseteaza_o_data_grupele_celor_pe_automat(s):
    automat = _ana(s)
    automat.grupa, automat.semigrupa = _grupa(s, "244", NOU), "Gr_1"
    fixat = User(
        nume="Dan", email="dan@example.com", grupa=_grupa(s, "344", VECHI),
        orar_automat=False, orar_ales=_perioada(s, VECHI),
    )  # fmt: skip
    s.add(fixat)
    s.commit()
    azi = date(2026, 10, 4)

    # structura incompleta: nimic
    assert reseteaza_grupele_daca_e_cazul(s, azi) is None
    assert _ana(s).grupa_id is not None

    _structura_completa(s, NOU)
    assert reseteaza_grupele_daca_e_cazul(s, azi) == 1
    assert (_ana(s).grupa_id, _ana(s).semigrupa) == (None, None)
    assert s.get(User, fixat.id).grupa_id is not None  # a ramas pe orarul lui
    assert s.get(Setare, "grupe_resetate_pentru").valoare == NOU

    # o singura data pe an: grupa aleasa din nou nu se mai pierde
    ana = _ana(s)
    ana.grupa = _grupa(s, "344", NOU)
    s.commit()
    assert reseteaza_grupele_daca_e_cazul(s, azi) is None
    assert _ana(s).grupa_id is not None


def test_resetarea_porneste_din_pagina_anului_universitar(client, s, monkeypatch):
    from orar.db import conturi

    monkeypatch.setattr(conturi, "an_universitar_al", lambda _zi: NOU)
    ana = _ana(s)
    ana.grupa = _grupa(s, "244", NOU)
    s.commit()
    _structura_completa(s, NOU)
    # contul o descopera la urmatoarea vizita, si isi alege din nou grupa
    assert client.get("/orarul-meu", follow_redirects=False).headers["location"] == "/account"
    assert _ana(s).grupa_id is None
