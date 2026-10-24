"""Orarul propriu: evenimente personale si activitati importate din alte grupe/serii."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import delete, select

from orar.db.models import ActivitateImportata, EvenimentPersonal, Ora, SsoSesiune, User
from orar.db.queries import gaseste_grupa
from tests.conftest import intra_sso

EMAIL = "ana@example.com"


@pytest.fixture
def curat(db):
    yield
    db.rollback()
    db.execute(delete(ActivitateImportata))
    db.execute(delete(EvenimentPersonal))
    db.execute(delete(SsoSesiune))
    db.execute(delete(User))
    db.commit()
    for obiect in list(db.new) + list(db.dirty):
        db.expunge(obiect)


def _csrf(client, cale: str = "/") -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


def _cont_cu_grupa(client, db, sso_fals, grupa: str = "244"):
    intra_sso(client, sso_fals, email=EMAIL, nume="Ana Pop")
    client.post(
        "/account",
        data={"csrf": _csrf(client, "/account"), "grupa_id": str(gaseste_grupa(db, grupa).id)},
    )
    return gaseste_grupa(db, grupa)


def test_evenimentul_personal_apare_pe_orarul_meu(client, db, sso_fals, curat):
    grupa = _cont_cu_grupa(client, db, sso_fals)
    acasa = f"/grupa/{grupa.slug}"
    r = client.post(
        "/orarul-meu/eveniment",
        data={
            "csrf": _csrf(client, acasa),
            "tip": "saptamanal",
            "titlu": "Sală de sport",
            "zi_saptamana": "Luni",
            "ora_inceput": "18:00",
            "ora_sfarsit": "19:00",
            "loc": "Sala 220",
            "frecventa": "",
            "inapoi": acasa,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    ev = db.scalar(select(EvenimentPersonal))
    assert ev.titlu == "Sală de sport" and ev.zi_saptamana == "Luni"
    assert "Sală de sport" in client.get(acasa).text


def test_evenimentul_cu_data_are_ziua_data(client, db, sso_fals, curat):
    grupa = _cont_cu_grupa(client, db, sso_fals)
    client.post(
        "/orarul-meu/eveniment",
        data={
            "csrf": _csrf(client, f"/grupa/{grupa.slug}"),
            "tip": "data",
            "titlu": "Examen",
            "data": "2026-01-15",
            "ora_inceput": "09:00",
            "ora_sfarsit": "11:00",
            "inapoi": f"/grupa/{grupa.slug}",
        },
        follow_redirects=False,
    )
    ev = db.scalar(select(EvenimentPersonal))
    assert ev.data is not None and ev.zi_saptamana is None


def test_importa_activitate_marcata(client, db, sso_fals, curat):
    grupa = _cont_cu_grupa(client, db, sso_fals)
    # o activitate reala care NU apartine grupei alese
    o = db.scalar(select(Ora).where(Ora.grupa_id != grupa.id).limit(1))
    assert o is not None
    r = client.post(
        "/orarul-meu/import",
        data={
            "csrf": _csrf(client, f"/grupa/{grupa.slug}"),
            "activitati": [str(o.id)],
            "inapoi": f"/grupa/{grupa.slug}",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    im = db.scalar(select(ActivitateImportata))
    assert im is not None and im.materie == o.materie.nume
    pagina = client.get(f"/grupa/{grupa.slug}").text
    assert "importat de la" in pagina
