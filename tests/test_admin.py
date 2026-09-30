"""Panoul de administrare: adminul din mediu, rolurile, si cine nu are ce cauta acolo."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from orar.db.models import ROLURI, User
from orar.web.auth import hash_parola

ADMIN, PAROLA_ADMIN = "sefu", "parola-adminului-de-test"
EMAIL, PAROLA = "dan@example.com", "parola-lui-dan-lunga"
RADACINA = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _mediu(db, monkeypatch):
    """Adminul principal vine din mediu; testele isi pun propriul admin, nu pe cel din `.env`."""
    monkeypatch.setenv("ORAR_ADMIN_USER", ADMIN)
    monkeypatch.setenv("ORAR_ADMIN_PAROLA", PAROLA_ADMIN)
    monkeypatch.delenv("ORAR_ADMIN_PAROLA_HASH", raising=False)
    yield
    db.rollback()
    db.execute(delete(User))
    db.commit()
    # stergerea e directa pe tabela; scoatem si obiectele ramase in sesiunea comuna
    for obiect in [o for o in db.identity_map.values() if isinstance(o, User)]:
        db.expunge(obiect)


@pytest.fixture
def dan(db) -> User:
    user = User(nume="Dan Ionescu", email=EMAIL, parola_hash=hash_parola(PAROLA))
    db.add(user)
    db.commit()
    return user


def _csrf(client, cale: str = "/login") -> str:  # noqa: ANN001
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


def _intra(client, cine: str = ADMIN, parola: str = PAROLA_ADMIN, inapoi: str = ""):  # noqa: ANN001, ANN202
    return client.post(
        "/login",
        data={"csrf": _csrf(client), "email": cine, "parola": parola, "inapoi": inapoi},
        follow_redirects=False,
    )


def _rol(db) -> str:  # noqa: ANN001
    db.expire_all()
    return db.scalar(select(User.rol).where(User.email == EMAIL))


# ------------------------------------------------------------------ accesul


@pytest.mark.parametrize("cale", ["/admin", "/admin/review", "/admin/decupaj/1"])
def test_fara_autentificare_nu_se_intra(client, cale):
    r = client.get(cale, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?inapoi=/admin")


@pytest.mark.parametrize("cale", ["/asmin", "/admin/intrare", "/admin/iesire"])
def test_nu_exista_alta_usa_de_intrare(client, cale):
    """Adminul intra doar prin /login; /admin e doar panoul."""
    assert client.get(cale, follow_redirects=False).status_code == 404


def test_adminul_din_mediu_intra_si_ajunge_in_panou(client, dan):
    r = _intra(client)
    assert r.status_code == 303 and r.headers["location"] == "/admin"

    panou = client.get("/admin")
    assert panou.status_code == 200
    assert f"Autentificat ca <strong>{ADMIN}</strong>" in panou.text
    assert "adminul principal" in panou.text
    assert EMAIL in panou.text  # lista de utilizatori
    assert client.get("/admin/review").status_code == 200

    # adminul nu are "orarul meu": are panoul
    acasa = client.get("/").text
    assert 'href="/admin"' in acasa and "orarul meu" not in acasa
    assert client.get("/orarul-meu", follow_redirects=False).headers["location"] == "/admin"


def test_adminul_din_mediu_cu_parola_gresita(client):
    r = _intra(client, parola="gresita")
    assert r.status_code == 401 and "Email sau parolă greșită." in r.text
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_fara_variabile_de_mediu_adminul_nu_exista(client, monkeypatch):
    monkeypatch.delenv("ORAR_ADMIN_USER")
    monkeypatch.delenv("ORAR_ADMIN_PAROLA")
    assert _intra(client).status_code == 401
    assert _intra(client, cine="", parola="").status_code == 401


def test_adminul_poate_fi_dat_si_ca_hash(client, monkeypatch):
    monkeypatch.delenv("ORAR_ADMIN_PAROLA")
    monkeypatch.setenv("ORAR_ADMIN_PAROLA_HASH", hash_parola(PAROLA_ADMIN))
    assert _intra(client).headers["location"] == "/admin"


def test_sesiunea_de_admin_cade_cand_mediul_il_schimba(client, monkeypatch):
    _intra(client)
    monkeypatch.setenv("ORAR_ADMIN_USER", "altcineva")
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_un_cont_obisnuit_nu_intra_in_panou(client, dan):
    assert _intra(client, EMAIL, PAROLA).headers["location"] == "/orarul-meu"
    assert client.get("/admin").status_code == 403
    assert client.get("/admin/review").status_code == 403


@pytest.mark.parametrize("rau", ["https://evil.example/", "//evil.example", "/\\evil.example"])
def test_dupa_autentificare_ramai_pe_sit(client, rau):
    assert _intra(client, inapoi=rau).headers["location"] == "/admin"


# ------------------------------------------------------------------ rolurile


def _seteaza(client, user_id: int, rol: str, *, csrf: str | None = None, profesor: str = ""):  # noqa: ANN001, ANN202
    return client.post(
        f"/admin/utilizatori/{user_id}/rol",
        data={
            "csrf": csrf if csrf is not None else _csrf(client, "/admin"),
            "rol": rol,
            "profesor": profesor,
        },
        follow_redirects=False,
    )


def _un_profesor(db) -> str:  # noqa: ANN001
    """Numele unui profesor cu ore in orar, dintre cei care se pot alege."""
    from orar.db.queries import ore_pentru_profesor, profesori_de_ales

    return next(n for n in profesori_de_ales(db) if ore_pentru_profesor(db, n))


@pytest.mark.parametrize("rol", ["voluntar", "profesor", "admin", "student"])
def test_adminul_seteaza_rolul(client, db, dan, rol):
    assert _rol(db) == "student"
    _intra(client)
    r = _seteaza(client, dan.id, rol, profesor=_un_profesor(db) if rol == "profesor" else "")
    assert r.status_code == 303 and r.headers["location"] == "/admin?salvat=1#utilizatori"
    assert _rol(db) == rol
    assert f'<option value="{rol}" selected>' in client.get("/admin").text


def test_rolurile_din_panou_sunt_cele_cerute(client, dan):
    assert set(ROLURI) == {"student", "voluntar", "profesor", "admin"}
    _intra(client)
    panou = client.get("/admin").text
    for rol in ("voluntar", "profesor", "admin"):
        assert f'<option value="{rol}"' in panou


def test_rol_necunoscut_si_cont_inexistent(client, db, dan):
    _intra(client)
    assert _seteaza(client, dan.id, "imparat").status_code == 400
    assert _seteaza(client, 99999, "voluntar").status_code == 404
    assert _rol(db) == "student"


def test_fara_csrf_rolul_nu_se_schimba(client, db, dan):
    _intra(client)
    _seteaza(client, dan.id, "admin", csrf="gresit")
    assert _rol(db) == "student"


def test_un_cont_obisnuit_nu_poate_seta_roluri(client, db, dan):
    _intra(client, EMAIL, PAROLA)
    token = _csrf(client, "/")
    assert _seteaza(client, dan.id, "admin", csrf=token).status_code == 403
    assert _rol(db) == "student"


def test_contul_ridicat_la_admin_primeste_panoul_fara_sa_reintre(client, db, dan):
    from fastapi.testclient import TestClient

    from orar.web.app import app

    _intra(client, EMAIL, PAROLA)  # Dan e deja autentificat, ca student
    assert client.get("/admin").status_code == 403

    with TestClient(app) as seful:
        _intra(seful)
        _seteaza(seful, dan.id, "admin")

    # rolul se citeste din baza la fiecare cerere
    assert client.get("/admin").status_code == 200
    acasa = client.get("/").text
    assert 'href="/admin"' in acasa and "orarul meu" not in acasa


# ------------------------------------------------------------ credentialele


def test_credentialele_adminului_nu_sunt_in_cod():
    """Stau doar in mediu (`.env`, care nu intra in git)."""
    for cale in [*RADACINA.glob("src/**/*.py"), RADACINA / ".env.example"]:
        text = cale.read_text(encoding="utf-8")
        assert "fabi67sasesapte" not in text, cale
        assert not re.search(r"scrypt\$\d+\$\d+\$\d+\$[0-9a-f]{16,}", text), cale


# ------------------------------------------------------------------ conturile de profesor


def test_rolul_de_profesor_cere_si_profesorul(client, db, dan):
    _intra(client)
    r = _seteaza(client, dan.id, "profesor")
    assert r.status_code == 303 and "eroare_rol" in r.headers["location"]
    assert _rol(db) == "student"
    assert _seteaza(client, dan.id, "profesor", profesor="Nu Exista Asa Om").status_code == 303
    assert _rol(db) == "student"

    nume = _un_profesor(db)
    _seteaza(client, dan.id, "profesor", profesor=nume)
    db.expire_all()
    assert (dan.rol, dan.profesor) == ("profesor", nume)
    # la alt rol, legatura cu profesorul dispare
    _seteaza(client, dan.id, "voluntar")
    db.expire_all()
    assert (dan.rol, dan.profesor) == ("voluntar", None)


def test_orarul_meu_al_unui_profesor_e_orarul_lui(client, db, dan):
    from orar.db.queries import gaseste_profesor, ore_pentru_profesor

    nume = _un_profesor(db)
    dan.rol, dan.profesor = "profesor", nume
    db.commit()
    _intra(client, EMAIL, PAROLA)
    r = client.get("/orarul-meu", follow_redirects=False)
    assert r.headers["location"] == f"/profesor/{gaseste_profesor(db, nume).slug}"

    pagina = client.get(r.headers["location"])
    assert pagina.status_code == 200
    assert nume in pagina.text and ">orarul meu</span>" in pagina.text
    o = ore_pentru_profesor(db, nume)[0]
    assert o.materie.nume in pagina.text and o.grupa.nume in pagina.text
    assert client.get("/profesor/nu-exista").status_code == 404


def test_cererea_de_profesor_din_cont_si_aprobarea_ei(client, db, dan):
    nume = _un_profesor(db)
    _intra(client, EMAIL, PAROLA)
    assert "Cere rolul de profesor" in client.get("/account").text

    # un nume care nu e in lista nu devine cerere
    client.post(
        "/account/profesor", data={"csrf": _csrf(client, "/account"), "profesor": "Altcineva"}
    )
    db.expire_all()
    assert dan.cerere_profesor is None

    client.post("/account/profesor", data={"csrf": _csrf(client, "/account"), "profesor": nume})
    db.expire_all()
    # cererea nu da singura rolul
    assert (dan.rol, dan.profesor, dan.cerere_profesor) == ("student", None, nume)
    assert "așteaptă să fie aprobată" in client.get("/account").text

    # un cont obisnuit nu-si poate aproba singur cererea
    r = client.post(
        f"/admin/utilizatori/{dan.id}/cerere",
        data={"csrf": _csrf(client, "/account"), "actiune": "aproba"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    client.post("/logout", data={"csrf": _csrf(client, "/account")})

    _intra(client)
    panou = client.get("/admin").text
    assert "1 cerere pentru rolul de profesor" in panou and nume in panou
    client.post(
        f"/admin/utilizatori/{dan.id}/cerere",
        data={"csrf": _csrf(client, "/admin"), "actiune": "aproba"},
    )
    db.expire_all()
    assert (dan.rol, dan.profesor, dan.cerere_profesor) == ("profesor", nume, None)


def test_cererea_se_poate_respinge_sau_retrage(client, db, dan):
    nume = _un_profesor(db)
    dan.cerere_profesor = nume
    db.commit()
    _intra(client)
    client.post(
        f"/admin/utilizatori/{dan.id}/cerere",
        data={"csrf": _csrf(client, "/admin"), "actiune": "respinge"},
    )
    db.expire_all()
    assert (dan.rol, dan.cerere_profesor) == ("student", None)

    dan.cerere_profesor = nume
    db.commit()
    client.post("/logout", data={"csrf": _csrf(client, "/admin")})
    _intra(client, EMAIL, PAROLA)
    client.post("/account/profesor", data={"csrf": _csrf(client, "/account"), "renunta": "1"})
    db.expire_all()
    assert dan.cerere_profesor is None
