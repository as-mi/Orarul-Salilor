"""Incarcarea unui orar dintr-un link, din panoul de admin.

Lantul adevarat (captura din Drive, OCR) dureaza minute si cere retea; aici e inlocuit cu
unul fals. Ce se verifica e tot ce sta in jurul lui: ce linkuri se accepta, cine are voie,
ca ruleaza o singura incarcare o data si ce vede adminul la final.
"""

from __future__ import annotations

import re
import threading

import pytest
from sqlalchemy import delete

from orar.db.models import User
from orar.web.auth import hash_parola
from orar.worker import incarcare
from orar.worker.sync import RaportSincronizare

ADMIN, PAROLA_ADMIN = "sefu", "parola-adminului-de-test"
LINK = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view"
LINK_PROF = "https://drive.google.com/file/d/1ZyXwVuTsRqPoNmLkJiHgFeDcBa543210/view"


@pytest.fixture(autouse=True)
def _curat(db, monkeypatch):
    monkeypatch.setenv("ORAR_ADMIN_USER", ADMIN)
    monkeypatch.setenv("ORAR_ADMIN_PAROLA", PAROLA_ADMIN)
    monkeypatch.delenv("ORAR_ADMIN_PAROLA_HASH", raising=False)
    monkeypatch.setattr(incarcare, "_stare", incarcare.Stare())
    yield
    if incarcare._fir is not None:
        incarcare._fir.join(timeout=5)
    db.rollback()
    db.execute(delete(User))
    db.commit()
    for obiect in [o for o in db.identity_map.values() if isinstance(o, User)]:
        db.expunge(obiect)


def _csrf(client, cale: str = "/login") -> str:  # noqa: ANN001
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


@pytest.fixture
def admin(client):
    client.post("/login", data={"csrf": _csrf(client), "email": ADMIN, "parola": PAROLA_ADMIN})
    return client


def _trimite(client, **campuri):  # noqa: ANN001, ANN003, ANN202
    date = {
        "csrf": _csrf(client, "/admin"),
        "url_grupe": LINK,
        "url_profesori": LINK_PROF,
        "semestru": "2",
        "an_universitar": "2025-2026",
        **campuri,
    }
    return client.post("/admin/orar", data=date, follow_redirects=False)


def _asteapta() -> None:
    incarcare._fir.join(timeout=5)
    assert not incarcare._fir.is_alive()


def _lant_fals(monkeypatch, *, ingestate=("sem 2 / grupe: 98 pagini, 1146 ore",), eroare=None):  # noqa: ANN001, ANN202
    cereri = []

    def fals(cerere):  # noqa: ANN001, ANN202
        cereri.append(cerere)
        if eroare:
            raise eroare
        return RaportSincronizare(ingestate=list(ingestate), avertismente=["un avertisment"])

    monkeypatch.setattr(incarcare, "_incarca", fals)
    return cereri


# ------------------------------------------------------------------ linkurile


@pytest.mark.parametrize(
    "url",
    [
        LINK,
        "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
        "https://docs.google.com/viewer?srcid=abc",
        "https://bit.ly/4qM4mKn",
    ],
)
def test_linkuri_acceptate(url):
    assert incarcare.valideaza_link(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "",
        "drive.google.com/file/d/abc",  # fara schema
        "http://drive.google.com/file/d/abc",  # nu https
        "https://evil.example/orar.pdf",
        "https://drive.google.com.evil.example/file/d/abc",
        "https://user:parola@drive.google.com/file/d/abc",
        "https://drive.google.com:8443/file/d/abc",
        "https://127.0.0.1/admin",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ],
)
def test_linkuri_respinse(url):
    assert incarcare.valideaza_link(url)


@pytest.mark.parametrize(
    ("an", "bun"),
    [("2025-2026", True), ("2026-2027", True), ("2025-2027", False), ("2025", False), ("", False)],
)
def test_anul_universitar(an, bun):
    assert (incarcare.valideaza_an(an) is None) == bun


class _Raspuns:
    def __init__(self, tinta: str | None) -> None:
        self.headers = {"location": tinta} if tinta else {}
        self.is_redirect = tinta is not None


def test_linkul_scurt_se_urmeaza_doar_pana_pe_drive(monkeypatch):
    import httpx

    tinte = {"https://bit.ly/bun": LINK, "https://bit.ly/rau": "http://127.0.0.1:8000/admin"}
    monkeypatch.setattr(httpx, "get", lambda url, **_: _Raspuns(tinte.get(url)))

    assert incarcare.rezolva_link(LINK) == LINK  # deja pe Drive: fara nicio cerere
    assert incarcare.rezolva_link("https://bit.ly/bun") == LINK
    with pytest.raises(incarcare.LinkRespins):
        incarcare.rezolva_link("https://bit.ly/rau")
    with pytest.raises(incarcare.LinkRespins):
        incarcare.rezolva_link("https://bit.ly/inexistent")


# -------------------------------------------------------------------- accesul


def test_doar_adminul_poate_incarca(client, db, monkeypatch):
    cereri = _lant_fals(monkeypatch)
    r = client.post("/admin/orar", data={"url_grupe": LINK}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.get("/admin/orar/stare", follow_redirects=False).status_code == 303

    db.add(User(nume="Dan", email="dan@example.com", parola_hash=hash_parola("parola-lui-dan-x")))
    db.commit()
    client.post(
        "/login",
        data={"csrf": _csrf(client), "email": "dan@example.com", "parola": "parola-lui-dan-x"},
    )
    assert _trimite_fara_panou(client).status_code == 403
    assert cereri == [] and incarcare.stare_curenta().faza == "inactiv"


def _trimite_fara_panou(client):  # noqa: ANN001, ANN202
    """Un cont fara rol de admin nu poate deschide /admin, deci isi ia tokenul de pe `/`."""
    return client.post(
        "/admin/orar",
        data={"csrf": _csrf(client, "/"), "url_grupe": LINK, "semestru": "2"},
        follow_redirects=False,
    )


def test_fara_csrf_nu_porneste_nimic(admin, monkeypatch):
    cereri = _lant_fals(monkeypatch)
    _trimite(admin, csrf="gresit")
    assert cereri == [] and incarcare.stare_curenta().faza == "inactiv"


# ----------------------------------------------------------------- incarcarea


def test_panoul_are_formularul(admin):
    panou = admin.get("/admin").text
    assert "Încarcă un orar" in panou
    assert 'action="/admin/orar"' in panou and 'name="url_grupe"' in panou
    assert "Nicio încărcare pornită" in panou


@pytest.mark.parametrize(
    ("campuri", "mesaj"),
    [
        ({"url_grupe": "https://evil.example/x"}, "Linkul trebuie"),
        ({"url_grupe": ""}, "Lipse"),
        ({"url_profesori": "http://drive.google.com/x"}, "https"),
        ({"semestru": "3"}, "Semestrul"),
        ({"an_universitar": "2025"}, "Anul universitar"),
    ],
)
def test_date_gresite_nu_pornesc_incarcarea(admin, monkeypatch, campuri, mesaj):
    cereri = _lant_fals(monkeypatch)
    r = _trimite(admin, **campuri)
    assert r.status_code == 303 and "eroare_orar=" in r.headers["location"]
    assert mesaj in admin.get(r.headers["location"]).text
    assert cereri == [] and incarcare.stare_curenta().faza == "inactiv"


def test_incarcarea_reusita(admin, monkeypatch):
    cereri = _lant_fals(monkeypatch)
    r = _trimite(
        admin, url_profesori="https://bit.ly/prof", semestru="1", an_universitar="2026-2027"
    )
    assert r.status_code == 303 and r.headers["location"] == "/admin#orar"
    _asteapta()

    (cerere,) = cereri
    assert (cerere.url_grupe, cerere.url_profesori) == (LINK, "https://bit.ly/prof")
    assert (cerere.semestru, cerere.an_universitar, cerere.de_catre) == (1, "2026-2027", ADMIN)

    stare = incarcare.stare_curenta()
    assert stare.faza == "reusit" and stare.terminat_la is not None
    panou = admin.get("/admin").text
    assert "Încărcat." in panou and "98 pagini, 1146 ore" in panou and "un avertisment" in panou
    assert 'hx-get="/admin/orar/stare"' not in panou  # terminata: nu mai intreaba


def test_o_singura_incarcare_o_data(admin, monkeypatch):
    poate_termina = threading.Event()

    def lent(_cerere):  # noqa: ANN001, ANN202
        poate_termina.wait(timeout=5)
        return RaportSincronizare(ingestate=["gata"])

    monkeypatch.setattr(incarcare, "_incarca", lent)
    _trimite(admin)
    try:
        assert incarcare.stare_curenta().in_lucru

        r = _trimite(admin)
        assert "deja" in admin.get(r.headers["location"]).text

        # cat e in lucru: caseta se reimprospateaza singura, iar butonul e oprit
        caseta = admin.get("/admin/orar/stare").text
        assert "În lucru" in caseta and 'hx-get="/admin/orar/stare"' in caseta
        assert re.search(r'class="principal"\s+disabled', admin.get("/admin").text)
    finally:
        poate_termina.set()
    _asteapta()
    assert incarcare.stare_curenta().faza == "reusit"


def test_orarul_necitit_lasa_baza_neschimbata(admin, monkeypatch):
    """Captura sau citirea au picat: nimic incarcat, iar adminul afla de ce."""
    _lant_fals(monkeypatch, ingestate=())
    _trimite(admin)
    _asteapta()
    stare = incarcare.stare_curenta()
    assert stare.faza == "esuat" and "neschimbată" in stare.eroare
    panou = admin.get("/admin").text
    assert "Eșuat." in panou and "un avertisment" in panou


def test_eroarea_neasteptata_ajunge_in_panou(admin, monkeypatch):
    _lant_fals(monkeypatch, eroare=RuntimeError("s-a rupt ceva"))
    _trimite(admin)
    _asteapta()
    assert incarcare.stare_curenta().faza == "esuat"
    assert "s-a rupt ceva" in admin.get("/admin").text


def test_linkul_care_nu_duce_pe_drive(admin, monkeypatch):
    _lant_fals(monkeypatch, eroare=incarcare.LinkRespins("Linkul scurt nu duce nicăieri."))
    _trimite(admin, url_grupe="https://bit.ly/oriunde")
    _asteapta()
    stare = incarcare.stare_curenta()
    assert stare.faza == "esuat" and stare.eroare == "Linkul scurt nu duce nicăieri."


def test_orarul_profesorilor_e_obligatoriu(admin, monkeypatch):
    cereri = _lant_fals(monkeypatch)
    r = _trimite(admin, url_profesori="")
    assert r.status_code == 303 and "eroare_orar" in r.headers["location"]
    assert cereri == [] and incarcare.stare_curenta().faza == "inactiv"
    assert 'name="url_profesori" required' in admin.get("/admin").text
