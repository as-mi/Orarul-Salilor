"""Orarul de statistici: cati oameni au ore intr-un interval, salile pline, filtrele salvate."""

from __future__ import annotations

import re
from datetime import time

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from orar.db.models import Base, FiltruSalvat, Grupa, Ora, Perioada, Sala
from orar.db.queries import gaseste_grupa
from orar.db.statistici import Filtre, calculeaza, grupe_de_baza
from orar.ingest.consolidate import consolideaza
from orar.ingest.load import incarca_pagini
from tests.conftest import _engine_memorie

ADMIN, PAROLA = "sefa", "o-parola-doar-pentru-teste"


@pytest.fixture
def s(pagini_golden, monkeypatch):
    monkeypatch.setenv("ORAR_ADMIN_USER", ADMIN)
    monkeypatch.setenv("ORAR_ADMIN_PAROLA", PAROLA)
    monkeypatch.delenv("ORAR_ADMIN_PAROLA_HASH", raising=False)
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


@pytest.fixture
def client(s):
    from fastapi.testclient import TestClient

    from orar.web import deps
    from orar.web.app import app

    app.dependency_overrides[deps.get_db] = lambda: s
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _csrf(client, cale: str) -> str:  # noqa: ANN001
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


def _intra(client) -> None:  # noqa: ANN001
    client.post(
        "/login",
        data={"csrf": _csrf(client, "/login"), "email": ADMIN, "parola": PAROLA},
        follow_redirects=False,
    )


def _orar(s) -> Perioada:  # noqa: ANN001
    return s.scalar(select(Perioada))


def _ora(s, grupa: Grupa, zi: str, inceput: int, sfarsit: int, **campuri) -> Ora:  # noqa: ANN001, ANN003
    """O activitate pusa de mana, intr-un interval in care orarul de test n-are nimic."""
    o = Ora(
        grupa=grupa,
        perioada=_orar(s),
        zi_saptamana=zi,
        ora_inceput=time(inceput),
        ora_sfarsit=time(sfarsit),
        **campuri,
    )
    s.add(o)
    s.flush()
    return o


def _gol(s) -> None:  # noqa: ANN001
    """Scoate orele din orarul de test, ca numerele sa depinda doar de ce pune testul."""
    for o in s.scalars(select(Ora)):
        s.delete(o)
    s.flush()


def test_publicul_se_numara_pe_grupe(s):
    toate = calculeaza(s, _orar(s), Filtre())
    assert toate.nr_grupe == len(grupe_de_baza(s, "2025-2026"))
    assert toate.populatie == 30 * toate.nr_grupe

    info2 = calculeaza(s, _orar(s), Filtre(specializari=["INFO"], ani=["L2"]))
    assert 0 < info2.nr_grupe < toate.nr_grupe
    assert calculeaza(s, _orar(s), Filtre(grupe=["244"])).populatie == 30
    # marimile se pot schimba
    assert calculeaza(s, _orar(s), Filtre(grupe=["244"], marime_grupa=25)).populatie == 25


def test_semigrupa_grupa_si_cursul_de_serie(s):
    _gol(s)
    g244 = gaseste_grupa(s, "244")
    seria = gaseste_grupa(s, "seria-24")
    in_serie = [g for g in grupe_de_baza(s, "2025-2026") if g.serie == "seria-24"]
    _ora(s, g244, "Luni", 8, 10, semigrupa="Gr_1", tip_ora_materie="Lab")
    _ora(s, g244, "Marti", 8, 10, tip_ora_materie="seminar")
    _ora(s, seria, "Miercuri", 8, 10, tip_ora_materie="curs")

    st = calculeaza(s, _orar(s), Filtre(serii=["seria-24"]))
    assert st.celule[("Luni", 8)].persoane == 15  # o semigrupa
    assert st.celule[("Luni", 9)].persoane == 15  # doua ore, amandoua numarate
    assert st.celule[("Marti", 8)].persoane == 30  # o grupa
    assert st.celule[("Miercuri", 8)].persoane == 30 * len(in_serie)  # toata seria
    assert st.valoare(st.celule[("Miercuri", 8)], "procent") == 100
    assert st.valoare(st.celule[("Miercuri", 8)], "libere") == 0
    assert st.celule[("Joi", 8)].persoane == 0

    # doar publicul grupei 244: cursul seriei o ocupa toata, nu mai mult
    doar_244 = calculeaza(s, _orar(s), Filtre(grupe=["244"]))
    assert doar_244.celule[("Miercuri", 8)].persoane == 30


def test_o_grupa_nu_e_numarata_de_doua_ori(s):
    _gol(s)
    g244 = gaseste_grupa(s, "244")
    # cele doua semigrupe au laborator in acelasi timp: grupa e ocupata toata, o data
    _ora(s, g244, "Luni", 10, 12, semigrupa="Gr_1")
    _ora(s, g244, "Luni", 10, 12, semigrupa="Gr_2")
    # doua activitati care alterneaza saptamanal: niciodata amandoua deodata
    _ora(s, g244, "Marti", 10, 12, frecventa="SI")
    _ora(s, g244, "Marti", 10, 12, frecventa="SP")

    st = calculeaza(s, _orar(s), Filtre(grupe=["244"]))
    assert st.celule[("Luni", 10)].persoane == 30
    assert st.celule[("Marti", 10)].persoane == 30
    assert st.celule[("Marti", 10)].activitati == 1  # varful dintre saptamani, nu suma
    impara = calculeaza(s, _orar(s), Filtre(grupe=["244"], saptamana="SI"))
    assert impara.celule[("Marti", 10)].activitati == 1


def test_salile_pline(s):
    _gol(s)
    sala = Sala(nume="S.999", slug="s-999", tip="fizica", nr_locuri=30)
    s.add(sala)
    g244 = gaseste_grupa(s, "244")
    _ora(s, g244, "Luni", 8, 10, sala=sala)  # 30 de oameni in 30 de locuri
    _ora(s, g244, "Marti", 8, 10, sala=sala, semigrupa="Gr_1")  # 15 in 30

    st = calculeaza(s, _orar(s), Filtre())
    assert (st.celule[("Luni", 8)].sali, st.celule[("Luni", 8)].pline) == (1, 1)
    assert (st.celule[("Marti", 8)].sali, st.celule[("Marti", 8)].pline) == (1, 0)
    assert st.celule[("Luni", 8)].detalii[0].plina
    # acelasi curs impartit pe saptamani (1-7 un profesor, 8-14 altul) nu umple sala de doua ori
    seria = gaseste_grupa(s, "seria-24")
    mare = Sala(nume="S.998", slug="s-998", tip="fizica", nr_locuri=200)
    s.add(mare)
    _ora(s, seria, "Joi", 8, 10, sala=mare, saptamani="sapt 1-7")
    _ora(s, seria, "Joi", 8, 10, sala=mare, saptamani="sapt 8-14")
    joi = calculeaza(s, _orar(s), Filtre()).celule[("Joi", 8)]
    assert (joi.sali, joi.pline, joi.activitati) == (1, 0, 1)
    # cu pragul la 50%, si sala pe jumatate plina conteaza
    assert calculeaza(s, _orar(s), Filtre(prag_plin=50)).celule[("Marti", 8)].pline == 1


def test_filtrele_pe_activitati(s):
    _gol(s)
    g244 = gaseste_grupa(s, "244")
    _ora(s, g244, "Luni", 8, 10, tip_ora_materie="curs")
    _ora(s, g244, "Marti", 8, 10, tip_ora_materie="Lab")
    pachet = s.scalar(select(Grupa).where(Grupa.tip == "optional", Grupa.specializare == "INFO"))
    _ora(s, pachet, "Joi", 8, 10, tip_ora_materie="curs")

    def persoane(zi: str, **k) -> int:  # noqa: ANN003
        st = calculeaza(s, _orar(s), Filtre(**k))
        return st.celule[(zi, 8)].persoane if (zi, 8) in st.celule else -1

    assert persoane("Marti", tipuri=["curs"]) == 0 and persoane("Luni", tipuri=["curs"]) == 30
    assert persoane("Marti", zile=["Luni"]) == -1  # ziua nici nu mai e in grila
    assert persoane("Luni", ora_de_la=10) == -1
    # optionalele: doar la cerere, fiecare cat o grupa
    assert persoane("Joi") == 0 and persoane("Joi", optionale=True) == 30
    assert persoane("Joi", optionale=True, specializari=["MATE"]) == 0


def test_pagina_si_filtrele_salvate(client, s):
    assert client.get("/admin/statistici", follow_redirects=False).status_code in (303, 401, 403)
    _intra(client)
    r = client.get("/admin/statistici?specializari=INFO&ani=L2&metrica=libere&celula=Luni-10")
    assert r.status_code == 200
    assert 'name="specializari" value="INFO" checked' in r.text
    assert 'id="celula"' in r.text and "Persoane libere" in r.text

    parametri = "specializari=INFO&ani=L2&metrica=libere"
    r = client.post(
        "/admin/statistici/filtre",
        data={
            "csrf": _csrf(client, "/admin/statistici"),
            "nume": "  Info   anul 2 ",
            "parametri": parametri + "&celula=Luni-10",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    salvat = s.scalar(select(FiltruSalvat))
    assert (salvat.nume, salvat.parametri) == (
        "Info anul 2",
        parametri,
    )  # celula deschisa nu se salveaza
    pagina = client.get("/admin/statistici").text
    assert (
        f'href="/admin/statistici?{parametri.replace("&", "&amp;")}"' in pagina
        and "Info anul 2" in pagina
    )

    # acelasi nume inlocuieste configurarea, nu o dubleaza
    client.post(
        "/admin/statistici/filtre",
        data={
            "csrf": _csrf(client, "/admin/statistici"),
            "nume": "Info anul 2",
            "parametri": "ani=L3",
        },
    )
    s.expire_all()
    assert [x.parametri for x in s.scalars(select(FiltruSalvat))] == ["ani=L3"]

    client.post(f"/admin/statistici/filtre/{salvat.id}/sterge", data={"csrf": "gresit"})
    assert s.scalar(select(FiltruSalvat)) is not None
    client.post(
        f"/admin/statistici/filtre/{salvat.id}/sterge",
        data={"csrf": _csrf(client, "/admin/statistici")},
    )
    assert s.scalar(select(FiltruSalvat)) is None
