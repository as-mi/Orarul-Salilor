"""Conturi: inregistrare, autentificare, grupa contului si "Orarul meu"."""

from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import delete, select

from orar.db.models import User
from orar.db.queries import gaseste_grupa
from orar.web.auth import hash_parola, verifica_parola

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
    db.execute(delete(User))
    db.commit()
    # stergerea e directa pe tabela; scoatem si obiectele ramase in sesiunea comuna
    for obiect in [o for o in db.identity_map.values() if isinstance(o, User)]:
        db.expunge(obiect)


def _csrf(client, cale: str = "/login") -> str:  # noqa: ANN001
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


def _inregistreaza(client, *, grupa: str = "", email: str = EMAIL, parola: str = PAROLA):  # noqa: ANN001, ANN202
    return client.post(
        "/signup",
        data={
            "csrf": _csrf(client, "/signup"),
            "nume": "Ana Pop",
            "email": email,
            "parola": parola,
            "grupa_id": grupa,
        },
        follow_redirects=False,
    )


def _intra(client, email: str = EMAIL, parola: str = PAROLA):  # noqa: ANN001, ANN202
    return client.post(
        "/login",
        data={"csrf": _csrf(client), "email": email, "parola": parola},
        follow_redirects=False,
    )


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


def test_inregistrarea_te_duce_la_orarul_tau(client, db):
    r = _inregistreaza(client, grupa=_id_grupa(db, "244"))
    assert r.status_code == 303 and r.headers["location"] == "/orarul-meu"
    r = client.get("/orarul-meu", follow_redirects=False)
    assert r.headers["location"] == "/grupa/244"

    user = _user(db)
    assert user.rol == "student"
    assert user.parola_hash.startswith("scrypt$") and PAROLA not in user.parola_hash


def test_parola_scurta_si_emailul_duplicat_sunt_respinse(client, db):
    assert _inregistreaza(client, parola="scurta").status_code == 400
    assert _user(db) is None
    assert _inregistreaza(client).status_code == 303
    _iese(client)
    assert _inregistreaza(client, email=EMAIL.upper()).status_code == 409


def test_intrarea_si_mesajul_unic_de_eroare(client, db):
    _inregistreaza(client, grupa=_id_grupa(db, "244"))
    _iese(client)

    gresita = _intra(client, parola="gresita-gresita")
    inexistent = _intra(client, email="nimeni@example.com")
    assert gresita.status_code == inexistent.status_code == 401
    assert "Email sau parolă greșită." in gresita.text
    assert "Email sau parolă greșită." in inexistent.text

    r = _intra(client)
    assert r.status_code == 303 and r.headers["location"] == "/orarul-meu"
    assert "Orarul meu" in client.get("/").text


def test_fara_csrf_nu_te_autentifici(client, db):
    _inregistreaza(client)
    _iese(client)
    r = client.post(
        "/login",
        data={"csrf": "gresit", "email": EMAIL, "parola": PAROLA},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert (
        client.get("/orarul-meu", follow_redirects=False).headers["location"].startswith("/login")
    )


def test_paginile_publice_raman_publice(client):
    for cale in ("/", "/grupa/244", "/sala/Amf.501", "/sala"):
        assert client.get(cale).status_code == 200
    assert "Log in" in client.get("/").text and "Sign up" in client.get("/").text


# ------------------------------------------------------- grupa si orarul meu


def test_fara_grupa_orarul_meu_cere_alegerea_ei(client, db):
    _inregistreaza(client)
    assert client.get("/orarul-meu", follow_redirects=False).headers["location"] == "/account"

    r = client.post(
        "/account",
        data={"csrf": _csrf(client, "/account"), "grupa_id": _id_grupa(db, "141")},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/orarul-meu"
    assert client.get("/orarul-meu", follow_redirects=False).headers["location"] == "/grupa/141"


def test_grupa_contului_trebuie_sa_fie_o_grupa(client, db):
    """Un `<option>` fabricat nu poate lega contul de o serie sau de un pachet."""
    _inregistreaza(client)
    client.post(
        "/account", data={"csrf": _csrf(client, "/account"), "grupa_id": _id_grupa(db, "seria-24")}
    )
    assert _user(db).grupa_id is None


def test_pe_orarul_meu_alegerile_se_tin_in_cont(client, db):
    _inregistreaza(client, grupa=_id_grupa(db, "244"))

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


def test_alegerile_din_cont_te_urmeaza_pe_alt_dispozitiv(client, db):
    from fastapi.testclient import TestClient

    from orar.web.app import app

    _inregistreaza(client, grupa=_id_grupa(db, "244"))
    toate = int(re.search(r"<strong>(\d+)</strong> activități", client.get("/grupa/244").text)[1])
    client.get("/grupa/244?semigrupa=Gr_2")

    with TestClient(app) as alt_browser:  # fara niciun cookie de-al primului
        _intra(alt_browser)
        pagina = alt_browser.get("/grupa/244").text
        assert int(re.search(r"<strong>(\d+)</strong> activități", pagina)[1]) < toate
        assert re.search(r'class="filtru activ"\s+href="\?semigrupa=Gr_2', pagina)


def test_pe_alta_grupa_alegerile_raman_in_cookie(client, db):
    _inregistreaza(client, grupa=_id_grupa(db, "244"))
    r = client.get("/grupa/243?semigrupa=Gr_1")
    assert "orar_semigrupa=" in r.headers["set-cookie"]
    assert ">orarul meu</span>" not in r.text
    assert _user(db).semigrupa is None


def test_schimbarea_grupei_sterge_alegerile_vechi(client, db):
    _inregistreaza(client, grupa=_id_grupa(db, "244"))
    client.get("/grupa/244?semigrupa=Gr_1")
    assert _user(db).semigrupa == "Gr_1"

    client.post(
        "/account", data={"csrf": _csrf(client, "/account"), "grupa_id": _id_grupa(db, "141")}
    )
    user = _user(db)
    assert user.grupa.slug == "141"
    assert user.semigrupa is None and user.ascunse is None
