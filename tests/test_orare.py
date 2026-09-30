"""Mai multe orare in baza (semestrul 1 si 2): pagina arata unul singur, toate se pot alege
din panou, iar adminul hotaraste care e cel implicit."""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from orar.db import corectii
from orar.db.models import Base, Ora, Perioada, SursaOrar
from orar.db.orare import fa_implicita, orare_vii, perioada_implicita
from orar.db.queries import gaseste_grupa
from orar.ingest.consolidate import consolideaza
from orar.ingest.load import incarca_pagini
from tests.conftest import _engine_memorie

ADMIN, PAROLA = "sefa", "o-parola-doar-pentru-teste"
#: Activitatea care exista doar in semestrul 1.
DOAR_SEM_1 = "Materie Doar Din Semestrul Unu"


@pytest.fixture
def s(pagini_golden):
    """Golden-ul ca semestrul 2, plus un semestru 1 cu o singura activitate la grupa 244."""
    engine = _engine_memorie()

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as sesiune:
        incarca_pagini(sesiune, pagini_golden)
        consolideaza(sesiune, an_universitar="2025-2026")
        sesiune.add(
            SursaOrar(
                an_univ="2025-2026",
                semestru=2,
                fel="grupe",
                url="https://bit.ly/x",
                actualizat=datetime(2026, 3, 1),
                ingestat_la=datetime(2026, 3, 1),
            )
        )
        corectii.adauga(
            sesiune,
            corectii.Valori(
                grupa=gaseste_grupa(sesiune, "244"),
                zi="Luni",
                ora_inceput=8,
                ora_sfarsit=10,
                materie=DOAR_SEM_1,
                tip="curs",
            ),
            semestru=1,
            de_catre="test",
        )
        sesiune.commit()
        yield sesiune


@pytest.fixture
def client(s, monkeypatch):
    from fastapi.testclient import TestClient

    from orar.web import deps
    from orar.web.app import app

    monkeypatch.setenv("ORAR_ADMIN_USER", ADMIN)
    monkeypatch.setenv("ORAR_ADMIN_PAROLA", PAROLA)
    monkeypatch.delenv("ORAR_ADMIN_PAROLA_HASH", raising=False)
    app.dependency_overrides[deps.get_db] = lambda: s
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _perioada(s, semestru: int) -> Perioada:  # noqa: ANN001
    return s.scalar(select(Perioada).where(Perioada.semestru == semestru))


def _csrf(client, cale: str) -> str:  # noqa: ANN001
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


def _intra(client) -> None:  # noqa: ANN001
    client.post(
        "/login",
        data={"csrf": _csrf(client, "/login"), "email": ADMIN, "parola": PAROLA},
        follow_redirects=False,
    )


# ------------------------------------------------------------------ ce e implicit


def test_fara_alegere_e_implicit_cel_incarcat_ultimul(s):
    vii = orare_vii(s)
    assert [o.perioada.semestru for o in vii] == [2, 1]
    assert [o.implicit for o in vii] == [True, False]
    assert perioada_implicita(s).semestru == 2


def test_adminul_alege_implicitul_si_ramane_unul_singur(s):
    fa_implicita(s, _perioada(s, 1))
    assert perioada_implicita(s).semestru == 1
    fa_implicita(s, _perioada(s, 2))
    s.commit()
    assert [p.semestru for p in s.scalars(select(Perioada).where(Perioada.implicita))] == [2]


# ------------------------------------------------------------------ paginile


def test_pagina_grupei_arata_un_singur_semestru(client, s):
    implicit = client.get("/grupa/244").text
    assert DOAR_SEM_1 not in implicit

    sem1 = client.get(f"/grupa/244?perioada={_perioada(s, 1).id}").text
    assert DOAR_SEM_1 in sem1
    # si nimic din semestrul 2: acolo e singura activitate
    alta = s.scalar(
        select(Ora).where(Ora.perioada_id == _perioada(s, 2).id, Ora.grupa_id.is_not(None))
    )
    assert "înapoi la orarul implicit" in sem1
    assert alta.materie.nume != DOAR_SEM_1


@pytest.mark.parametrize("cale", ["/", "/grupa/244", "/sala"])
def test_panoul_arata_tuturor_toate_orarele(client, s, cale):
    r = client.get(cale)
    assert r.status_code == 200
    assert "Semestrul 2 · 2025-2026" in r.text
    assert "Semestrul 1 · 2025-2026" in r.text
    assert f"?perioada={_perioada(s, 1).id}" in r.text


def test_linkurile_pastreaza_orarul_ales(client, s):
    pid = _perioada(s, 1).id
    r = client.get(f"/grupa/244?perioada={pid}")
    assert f'name="perioada" value="{pid}"' in r.text  # selectorul de data
    assert f"saptamana=true&amp;perioada={pid}" in r.text or f"perioada={pid}" in r.text


def test_pagina_principala_arata_doar_formatiunile_orarului_ales(client, s):
    toate = client.get("/").text
    sem1 = client.get(f"/?perioada={_perioada(s, 1).id}").text
    assert "/grupa/244" in sem1
    assert toate.count('href="/grupa/') > sem1.count('href="/grupa/')


def test_orar_inexistent_e_404(client):
    assert client.get("/grupa/244?perioada=999").status_code == 404
    assert client.get("/?perioada=999").status_code == 404


# ------------------------------------------------------------------ adminul


def test_adminul_schimba_implicitul_din_panou(client, s):
    _intra(client)
    panou = client.get("/admin")
    assert "Orare încărcate" in panou.text and "Fă-l implicit" in panou.text

    r = client.post(
        "/admin/orar/implicit",
        data={"csrf": _csrf(client, "/admin"), "perioada": str(_perioada(s, 1).id)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert perioada_implicita(s).semestru == 1
    # acum semestrul 1 e ce vede toata lumea, iar semestrul 2 se alege din panou
    pagina = client.get("/grupa/244").text
    assert DOAR_SEM_1 in pagina
    assert f"?perioada={_perioada(s, 2).id}" in pagina


def test_doar_adminul_si_doar_cu_csrf(client, s):
    pid = str(_perioada(s, 1).id)
    r = client.post("/admin/orar/implicit", data={"perioada": pid}, follow_redirects=False)
    assert perioada_implicita(s).semestru == 2 and r.status_code in (303, 401, 403)

    _intra(client)
    client.post("/admin/orar/implicit", data={"csrf": "gresit", "perioada": pid})
    assert perioada_implicita(s).semestru == 2
    r = client.post(
        "/admin/orar/implicit", data={"csrf": _csrf(client, "/admin"), "perioada": "999"}
    )
    assert r.status_code == 404


def test_activitatea_noua_intra_in_orarul_de_pe_pagina(client, s):
    _intra(client)
    pid = _perioada(s, 1).id
    formular = client.get(f"/admin/activitate/noua?grupa=244&perioada={pid}")
    assert f'name="perioada" value="{pid}"' in formular.text
    r = client.post(
        "/admin/activitate/noua",
        data={
            "csrf": _csrf(client, "/admin"),
            "perioada": str(pid),
            "grupa": "244",
            "zi": "Marti",
            "ora_inceput": "10",
            "ora_sfarsit": "12",
            "materie": "Adaugata In Semestrul Unu",
            "tip": "curs",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    o = s.scalar(
        select(Ora).join(Ora.materie).where(Ora.zi_saptamana == "Marti", Ora.perioada_id == pid)
    )
    assert o is not None and o.materie.nume == "Adaugata In Semestrul Unu"
