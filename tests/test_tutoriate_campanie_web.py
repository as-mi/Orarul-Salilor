"""Fluxul web al campaniilor de tutoriate: creare, formular, posturi, aplicare, echipe,
planificare. Exerseaza si sabloanele (prin TestClient), nu doar rutele."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from orar.db.models import AplicatieTutor, Base, CampanieTutoriate, EchipaTutoriat, PosturiDeschise, User
from orar.db.queries import gaseste_grupa
from orar.ingest.consolidate import consolideaza
from orar.ingest.load import incarca_pagini
from tests.conftest import _engine_memorie, intra_sso

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


def _csrf(client) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/login?admin=1").text).group(1)


def _intra(client, email: str = ADMIN, parola: str = PAROLA) -> None:
    r = client.post(
        "/login", data={"csrf": _csrf(client), "email": email, "parola": parola}, follow_redirects=False
    )
    assert r.status_code == 303


def _campanie_post(client) -> int:
    r = client.post(
        "/tutoriate/campanii/noua",
        data={
            "csrf": _csrf(client),
            "nume": "Tutoriate test",
            "an_univ": "2025-2026",
            "semestru": "2",
            "data_deschidere": "2026-01-01",
            "data_inchidere": "2026-12-31",
            "data_inceput": "2026-02-01",
            "data_sfarsit": "2026-06-30",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    return int(r.headers["location"].rstrip("/").split("/")[-1])


def test_flux_complet_campanie(client, s, sso_fals):
    _intra(client)

    # 1. lista de campanii
    assert client.get("/tutoriate/campanii").status_code == 200

    # 2. creare campanie
    campanie_id = _campanie_post(client)
    assert client.get(f"/tutoriate/campanii/{campanie_id}/formular").status_code == 200

    # 3. formularul dinamic
    intrebari = json.dumps(
        [
            {"cheie": "motivatie", "tip": "text", "text": "De ce?", "obligatoriu": True, "optiuni": []},
            {"cheie": "experienta", "tip": "radio", "text": "Ai mai fost?", "obligatoriu": False, "optiuni": ["Da", "Nu"]},
        ]
    )
    r = client.post(
        f"/tutoriate/campanii/{campanie_id}/formular",
        data={"csrf": _csrf(client), "intrebari": intrebari},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # 4. un post
    r = client.post(
        f"/tutoriate/campanii/{campanie_id}/posturi",
        data={"csrf": _csrf(client), "materie": "Algebră I"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # 5. deschide campania
    r = client.post(
        f"/tutoriate/campanii/{campanie_id}/stare",
        data={"csrf": _csrf(client), "stare": "deschisa"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # 6. studentul aplica (prin SSO)
    client.cookies.clear()
    intra_sso(client, sso_fals, email="ana@s.unibuc.ro", nume="Ana Pop", sub="sub-ana")
    assert client.get(f"/tutoriate/campanii/{campanie_id}/aplica").status_code == 200

    raspunsuri = json.dumps({"motivatie": "Îmi place să explic", "experienta": "Da"})
    disp_tot = json.dumps({zi: list(range(8, 20)) for zi in ("Luni", "Marti", "Miercuri", "Joi", "Vineri")})
    posturi = s.scalars(select(PosturiDeschise)).all()
    materii = json.dumps([{"post": posturi[0].id, "motiv": None}])
    r = client.post(
        f"/tutoriate/campanii/{campanie_id}/aplica",
        data={
            "csrf": _csrf(client),
            "raspunsuri": raspunsuri,
            "disponibilitate": disp_tot,
            "materii": materii,
            "preferinte_co_tutori": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    student = s.scalar(select(User).where(User.sso_sub == "sub-ana"))
    aplicatie = s.scalar(select(AplicatieTutor).where(AplicatieTutor.user_id == student.id))
    assert aplicatie is not None
    assert json.loads(aplicatie.raspunsuri)["motivatie"] == "Îmi place să explic"

    # 7. responsabilul vede aplicatia si o accepta
    client.cookies.clear()
    _intra(client)
    pagina = client.get(f"/tutoriate/campanii/{campanie_id}/aplicatii").text
    assert "Ana Pop" in pagina

    r = client.post(
        f"/tutoriate/campanii/{campanie_id}/aplicatii/{aplicatie.id}/stare",
        data={"csrf": _csrf(client), "stare": "acceptata"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # 8. formeaza o echipa
    r = client.post(
        f"/tutoriate/campanii/{campanie_id}/echipe",
        data={"csrf": _csrf(client), "post_id": posturi[0].id, "membri": student.id, "principal": student.id},
        follow_redirects=False,
    )
    assert r.status_code == 303
    echipa = s.scalars(select(EchipaTutoriat)).one()
    assert [m.user_id for m in echipa.membri] == [student.id]

    # 9. planificarea ruleaza si propune sesiuni
    prop = client.get(f"/tutoriate/campanii/{campanie_id}/propuneri?echipa_id={echipa.id}")
    assert prop.status_code == 200
    assert "acoperă" in prop.text or "Niciun interval" in prop.text

    # 10. tabul de planificare se randeaza
    assert client.get(f"/tutoriate/campanii/{campanie_id}/planificare").status_code == 200
