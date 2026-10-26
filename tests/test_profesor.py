"""Accesul la orarul profesorilor: implicit doar adminul, apoi cine are permisiunea.

Un profesor isi vede oricand propriul orar; restul conturilor primesc permisiunea
`orar_profesori` de la un admin (sau, optional, prin rolul de client din Keycloak).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import delete, select

from orar.db.models import Permisiune, Profesor, SsoSesiune, User
from tests.conftest import intra_sso


@pytest.fixture(autouse=True)
def _curata(db):
    """Testele isi fac propriile conturi; baza comuna ramane curata pentru alte module."""
    yield
    db.execute(delete(Permisiune))
    db.execute(delete(SsoSesiune))
    db.execute(delete(User))
    db.commit()
    for obiect in [o for o in db.identity_map.values() if isinstance(o, User)]:
        db.expunge(obiect)


def _profesor(db) -> Profesor:
    return db.scalar(select(Profesor).where(Profesor.din_orar).limit(1)) or db.scalar(
        select(Profesor).limit(1)
    )


def _user(db, email: str) -> User:
    return db.scalar(select(User).where(User.email == email))


def _acorda(db, user: User, cheie: str = "orar_profesori") -> None:
    db.add(Permisiune(user=user, cheie=cheie, acordat_de="test", acordat_la=datetime.now()))
    db.commit()


def test_fara_cont_orarul_profesorilor_cere_logare(client, db):
    p = _profesor(db)
    r = client.get(f"/profesor/{p.slug}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?inapoi=/profesor/")


def test_cont_fara_permisiune_primeste_403(client, db, sso_fals):
    p = _profesor(db)
    intra_sso(client, sso_fals, email="student@example.com")
    assert client.get(f"/profesor/{p.slug}").status_code == 403


def test_permisiunea_acordata_deschide_orarul(client, db, sso_fals):
    p = _profesor(db)
    intra_sso(client, sso_fals, email="student@example.com")
    assert client.get(f"/profesor/{p.slug}").status_code == 403

    _acorda(db, _user(db, "student@example.com"))

    r = client.get(f"/profesor/{p.slug}")
    assert r.status_code == 200
    assert p.nume in r.text


def test_adminul_vede_orarul_profesorilor(client, db, sso_fals):
    p = _profesor(db)
    intra_sso(client, sso_fals, email="sef@example.com", realm=("cddd",))
    assert client.get(f"/profesor/{p.slug}").status_code == 200


def test_fragmentul_orarului_pentru_panou(client, db, sso_fals):
    """Panoul de admin deschide orarul profesorului in pagina, dintr-un fragment, nu cu un
    redirect la pagina intreaga."""
    p = _profesor(db)
    intra_sso(client, sso_fals, email="sef@example.com", realm=("cddd",))
    r = client.get(f"/profesor/{p.slug}?fragment=1")
    assert r.status_code == 200
    assert p.nume in r.text
    # fragment, nu pagina intreaga: fara bara de sus si fara subsol
    assert 'id="orar-profesor"' in r.text
    assert "class=\"bara\"" not in r.text


def test_profesorul_isi_vede_orarul_lui_si_nu_al_altuia(client, db, sso_fals):
    p = _profesor(db)
    alt = db.scalar(select(Profesor).where(Profesor.id != p.id).limit(1))
    intra_sso(client, sso_fals, email="prof@example.com")
    u = _user(db, "prof@example.com")
    u.rol, u.profesor = "profesor", p.nume
    db.commit()

    assert client.get(f"/profesor/{p.slug}").status_code == 200
    if alt is not None:
        assert client.get(f"/profesor/{alt.slug}").status_code == 403


def test_tabul_profesori_apare_doar_cu_permisiune(client, db, sso_fals):
    intra_sso(client, sso_fals, email="student@example.com")
    assert "panou-profesori" not in client.get("/").text

    _acorda(db, _user(db, "student@example.com"))

    pagina = client.get("/").text
    assert "panou-profesori" in pagina
    assert "/profesor/" in pagina


def test_permisiunea_nu_e_implicita_la_niciun_ofici():
    """Oficiile din SSO au setul lor; orarul profesorilor ramane implicit doar la admini."""
    from orar.web import sso

    for permisiuni in sso.ROLURI_REALM_PERMISIUNI.values():
        assert "orar_profesori" not in permisiuni
