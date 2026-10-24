"""Conturi (prin SSO-ul ASMI, fals in teste): grupa contului si "Orarul meu"."""

from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import delete, select

from orar.db.models import SsoSesiune, User
from orar.db.queries import gaseste_grupa
from orar.web.auth import hash_parola, verifica_parola
from tests.conftest import intra_sso

EMAIL, PAROLA = "ana@example.com", "o-parola-destul-de-lunga"


@pytest.fixture(autouse=True)
def _fara_conturi_ramase(db, monkeypatch):
    """Baza de test e comuna pe sesiune; fiecare test isi lasa tabela USER goala. Adminul
    din mediu e scos, ca testele de aici sa nu depinda de un `.env` local."""
    monkeypatch.delenv("ORAR_ADMIN_USER", raising=False)
    monkeypatch.delenv("ORAR_ADMIN_PAROLA", raising=False)
    monkeypatch.delenv("ORAR_ADMIN_PAROLA_HASH", raising=False)
    yield
    db.rollback()
    db.execute(delete(SsoSesiune))
    db.execute(delete(User))
    db.commit()
    # stergerea e directa pe tabela; scoatem si obiectele ramase in sesiunea comuna
    for obiect in [o for o in db.identity_map.values() if isinstance(o, User)]:
        db.expunge(obiect)


def _csrf(client, cale: str = "/login") -> str:  # noqa: ANN001
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


def _inregistreaza(client, sso, db=None, *, grupa: str = ""):  # noqa: ANN001, ANN202
    """Primul login prin SSO (contul se creeaza), apoi, optional, alegerea grupei."""
    r = intra_sso(client, sso, email=EMAIL, nume="Ana Pop")
    if grupa:
        r = client.post(
            "/account",
            data={"csrf": _csrf(client, "/account"), "grupa_id": grupa},
            follow_redirects=False,
        )
    return r


def _iese(client) -> None:  # noqa: ANN001
    client.post("/logout", data={"csrf": _csrf(client, "/")})


def _id_grupa(db, slug: str) -> str:  # noqa: ANN001
    return str(gaseste_grupa(db, slug).id)


def _user(db) -> User:  # noqa: ANN001
    db.expire_all()
    return db.scalar(select(User).where(User.email == EMAIL))


# ---------------------------------------------------------------- parole


def test_parola_se_verifica_si_hashurile_difera():
    h1, h2 = hash_parola("secret-lung"), hash_parola("secret-lung")
    assert h1 != h2  # sare diferita
    assert verifica_parola("secret-lung", h1) and verifica_parola("secret-lung", h2)
    assert not verifica_parola("altceva", h1)


@pytest.mark.parametrize("stocat", [None, "", "aiurea", "bcrypt$1$2$3$4$5", "scrypt$a$b$c$d$e"])
def test_hash_stricat_nu_arunca(stocat):
    assert verifica_parola("orice", stocat) is False


# ---------------------------------------------- inregistrare si autentificare


def test_primul_login_cere_grupa_apoi_duce_la_orarul_tau(client, db, sso_fals):
    r = _inregistreaza(client, sso_fals)
    assert r.status_code == 303 and r.headers["location"] == "/account?nou=1"
    r = client.post(
        "/account",
        data={"csrf": _csrf(client, "/account"), "grupa_id": _id_grupa(db, "244")},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/orarul-meu"
    assert client.get("/orarul-meu", follow_redirects=False).headers["location"] == "/grupa/244"
    assert "Orarul meu" in client.get("/").text

    user = _user(db)
    assert user.rol == "student" and user.parola_hash is None and user.sso_sub


def test_fara_csrf_adminul_de_urgenta_nu_intra(client, monkeypatch):
    monkeypatch.setenv("ORAR_ADMIN_USER", "sefa")
    monkeypatch.setenv("ORAR_ADMIN_PAROLA", PAROLA)
    r = client.post(
        "/login",
        data={"csrf": "gresit", "email": "sefa", "parola": PAROLA},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert client.get("/admin", follow_redirects=False).headers["location"].startswith("/login")


def test_paginile_publice_raman_publice(client):
    for cale in ("/", "/grupa/244", "/sala/Amf.501", "/sala"):
        assert client.get(cale).status_code == 200
    assert "Log in" in client.get("/").text and "Sign up" in client.get("/").text


# ------------------------------------------------------- grupa si orarul meu


def test_fara_grupa_orarul_meu_cere_alegerea_ei(client, db, sso_fals):
    _inregistreaza(client, sso_fals, db)
    assert client.get("/orarul-meu", follow_redirects=False).headers["location"] == "/account"

    r = client.post(
        "/account",
        data={"csrf": _csrf(client, "/account"), "grupa_id": _id_grupa(db, "141")},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/orarul-meu"
    assert client.get("/orarul-meu", follow_redirects=False).headers["location"] == "/grupa/141"


def test_grupa_contului_trebuie_sa_fie_o_grupa(client, db, sso_fals):
    """Un `<option>` fabricat nu poate lega contul de o serie sau de un pachet."""
    _inregistreaza(client, sso_fals, db)
    client.post(
        "/account", data={"csrf": _csrf(client, "/account"), "grupa_id": _id_grupa(db, "seria-24")}
    )
    assert _user(db).grupa_id is None


def test_pe_orarul_meu_alegerile_se_tin_in_cont(client, db, sso_fals):
    _inregistreaza(client, sso_fals, db, grupa=_id_grupa(db, "244"))

    # semigrupa: in cont, nu in cookie
    r = client.get("/grupa/244?semigrupa=Gr_1")
    assert "orar_semigrupa" not in r.headers.get("set-cookie", "")
    assert _user(db).semigrupa == "Gr_1"
    assert ">orarul meu</span>" in r.text
    assert "se ține minte în contul tău" in r.text

    # optionalele / facultativele ascunse: tot in cont
    pagina = client.get("/grupa/244").text
    oferite = re.findall(r'name="oferite" value="([^"]+)"', pagina)
    r = client.post(
        "/grupa/244/afisare",
        data={
            "csrf": re.search(r'name="csrf" value="([^"]+)"', pagina).group(1),
            "oferite": oferite,
            "vizibile": oferite[1:],
        },
        follow_redirects=False,
    )
    assert "orar_afisare" not in r.headers.get("set-cookie", "")
    assert json.loads(_user(db).ascunse) == [oferite[0]]


def test_alegerile_din_cont_te_urmeaza_pe_alt_dispozitiv(client, db, sso_fals):
    from fastapi.testclient import TestClient

    from orar.web.app import app

    _inregistreaza(client, sso_fals, db, grupa=_id_grupa(db, "244"))
    toate = int(re.search(r"<strong>(\d+)</strong> activități", client.get("/grupa/244").text)[1])
    client.get("/grupa/244?semigrupa=Gr_2")

    with TestClient(app) as alt_browser:  # fara niciun cookie de-al primului
        intra_sso(alt_browser, sso_fals, email=EMAIL)
        pagina = alt_browser.get("/grupa/244").text
        assert int(re.search(r"<strong>(\d+)</strong> activități", pagina)[1]) < toate
        assert re.search(r'class="filtru activ"\s+href="\?semigrupa=Gr_2', pagina)


def test_pe_alta_grupa_alegerile_raman_in_cookie(client, db, sso_fals):
    _inregistreaza(client, sso_fals, db, grupa=_id_grupa(db, "244"))
    r = client.get("/grupa/243?semigrupa=Gr_1")
    assert "orar_semigrupa=" in r.headers["set-cookie"]
    assert ">orarul meu</span>" not in r.text
    assert _user(db).semigrupa is None


def test_schimbarea_grupei_sterge_alegerile_vechi(client, db, sso_fals):
    _inregistreaza(client, sso_fals, db, grupa=_id_grupa(db, "244"))
    client.get("/grupa/244?semigrupa=Gr_1")
    assert _user(db).semigrupa == "Gr_1"

    client.post(
        "/account", data={"csrf": _csrf(client, "/account"), "grupa_id": _id_grupa(db, "141")}
    )
    user = _user(db)
    assert user.grupa.slug == "141"
    assert user.semigrupa is None and user.ascunse is None
