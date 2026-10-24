"""Autentificarea prin SSO-ul ASMI: contul legat de `sub`, legarea conturilor vechi pe email,
adminul dat de rolul `cddd`, permisiunile din rolurile de client, reimprospatarea rolurilor,
logout-ul (si cel backchannel) si refuzul tokenurilor care nu sunt pentru Orar."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from orar.db.models import Base, SsoSesiune, User
from orar.db.queries import gaseste_grupa
from orar.ingest.consolidate import consolideaza
from orar.ingest.load import incarca_pagini
from orar.web import sso
from tests.conftest import SSO_CLIENT, SSO_ISSUER, _engine_memorie, intra_sso

ADMIN, PAROLA_ADMIN = "sefa", "o-parola-doar-pentru-teste"


@pytest.fixture
def s(pagini_golden, monkeypatch):
    monkeypatch.setenv("ORAR_ADMIN_USER", ADMIN)
    monkeypatch.setenv("ORAR_ADMIN_PAROLA", PAROLA_ADMIN)
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


def _csrf(client, cale: str = "/") -> str:  # noqa: ANN001
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


# ------------------------------------------------------------------ intrarea


def test_login_trimite_la_sso_si_creeaza_contul(client, s, sso_fals):
    r = intra_sso(client, sso_fals, email="ana@s.unibuc.ro", nume="Ana Pop")
    assert r.status_code == 303 and r.headers["location"] == "/account?nou=1"
    user = s.scalar(select(User).where(User.email == "ana@s.unibuc.ro"))
    assert user.sso_sub == "sub-ana@s.unibuc.ro" and user.nume == "Ana Pop"
    assert user.parola_hash is None
    assert "Ana Pop" in client.get("/account").text
    # tokenurile raman pe server: cookie-ul are doar id-ul sesiunii
    assert "refresh-" not in str(client.cookies.get("session") or "")
    assert s.scalar(select(SsoSesiune)).refresh_token == "refresh-sub-ana@s.unibuc.ro"


def test_a_doua_intrare_gaseste_acelasi_cont_dupa_sub(client, s, sso_fals):
    intra_sso(client, sso_fals, email="ana@s.unibuc.ro", sub="abc")
    client.cookies.clear()
    # si-a schimbat numele si emailul in Keycloak: se actualizeaza, contul ramane acelasi
    intra_sso(client, sso_fals, email="ana.pop@s.unibuc.ro", nume="Ana Popescu", sub="abc")
    useri = list(s.scalars(select(User)))
    assert len(useri) == 1
    assert (useri[0].email, useri[0].nume) == ("ana.pop@s.unibuc.ro", "Ana Popescu")


def test_contul_vechi_se_leaga_pe_email_verificat(client, s, sso_fals):
    vechi = User(
        nume="Ana", email="ana@s.unibuc.ro", parola_hash="scrypt$x", grupa=gaseste_grupa(s, "244")
    )
    s.add(vechi)
    s.commit()
    r = intra_sso(client, sso_fals, email="ANA@s.unibuc.ro")
    assert r.headers["location"] == "/orarul-meu"  # are deja grupa
    s.refresh(vechi)
    assert vechi.sso_sub and vechi.parola_hash is None and vechi.grupa.nume == "244"
    assert s.scalar(select(User).where(User.id != vechi.id)) is None


def test_email_neverificat_nu_leaga_contul_vechi(client, s, sso_fals):
    vechi = User(nume="Ana", email="ana@s.unibuc.ro", grupa=gaseste_grupa(s, "244"))
    s.add(vechi)
    s.commit()
    intra_sso(client, sso_fals, email="ana@s.unibuc.ro", email_verified=False)
    s.refresh(vechi)
    assert vechi.sso_sub is None
    nou = s.scalar(select(User).where(User.id != vechi.id))
    assert nou.sso_sub and nou.email is None  # adresa ramane a contului vechi


def test_inapoi_extern_e_ignorat(client, sso_fals):
    r = intra_sso(client, sso_fals, email="ana@s.unibuc.ro", inapoi="//rau.example/x")
    assert r.headers["location"] == "/account?nou=1"
    client.cookies.clear()
    r = intra_sso(client, sso_fals, email="ana@s.unibuc.ro", inapoi="/sala")
    assert r.headers["location"] == "/sala"


def test_signup_deschide_inregistrarea_din_keycloak(client, sso_fals):
    r = client.get("/signup", follow_redirects=False)
    assert r.status_code == 302 and sso_fals.extra == {"prompt": "create"}


def test_renuntarea_pe_pagina_keycloak(client, s, sso_fals):
    client.get("/login", follow_redirects=False)
    r = client.get("/auth/callback?error=access_denied", follow_redirects=False)
    assert r.status_code == 303 and s.scalar(select(User)) is None


# ------------------------------------------------------------------ rolurile


def test_cddd_e_admin_ceilalti_nu(client, sso_fals):
    intra_sso(client, sso_fals, email="student@s.unibuc.ro")
    assert client.get("/admin").status_code == 403
    client.cookies.clear()
    r = intra_sso(client, sso_fals, email="sef@asmi.ro", realm=("student", "cddd"))
    assert r.headers["location"] == "/admin"
    assert client.get("/admin").status_code == 200


def test_rolul_de_admin_e_configurabil(client, sso_fals, monkeypatch):
    monkeypatch.setenv("ORAR_SSO_ROL_ADMIN", "asmi-admin")
    sso_fals.cfg = sso.configurare()
    intra_sso(client, sso_fals, email="sef@asmi.ro", realm=("cddd",))
    assert client.get("/admin").status_code == 403


def test_rolul_retras_in_keycloak_se_pierde_la_reimprospatare(client, s, sso_fals):
    intra_sso(client, sso_fals, email="sef@asmi.ro", realm=("cddd",))
    assert client.get("/admin").status_code == 200
    sso_fals.identitate["realm"] = []  # retras din Keycloak
    assert client.get("/admin").status_code == 200  # inca nu s-a reimprospatat
    sesiune = s.scalar(select(SsoSesiune))
    sesiune.verificat_la = datetime.now() - sso.REIMPROSPATARE - timedelta(seconds=1)
    s.commit()
    assert client.get("/admin").status_code == 403


def test_refresh_refuzat_inchide_sesiunea(client, s, sso_fals):
    intra_sso(client, sso_fals, email="ana@s.unibuc.ro")
    sso_fals.refuza_refresh = True
    s.scalar(select(SsoSesiune)).verificat_la = datetime.now() - timedelta(hours=1)
    s.commit()
    r = client.get("/account", follow_redirects=False)
    assert r.headers["location"].startswith("/login")
    assert s.scalar(select(SsoSesiune)) is None


# ------------------------------------------------------------------ iesirea


def test_logout_iese_si_din_sso(client, s, sso_fals):
    intra_sso(client, sso_fals, email="ana@s.unibuc.ro")
    r = client.post("/logout", data={"csrf": _csrf(client, "/account")}, follow_redirects=False)
    assert r.headers["location"].startswith(f"{SSO_ISSUER}/protocol/openid-connect/logout?")
    assert "id_token_hint=id-token-test" in r.headers["location"]
    assert s.scalar(select(SsoSesiune)) is None
    assert client.get("/account", follow_redirects=False).status_code == 303


def test_logout_backchannel_inchide_sesiunea(client, s, sso_fals):
    intra_sso(client, sso_fals, email="ana@s.unibuc.ro")
    sid = s.scalar(select(SsoSesiune)).sid
    jeton = sso_fals.token(
        sid=sid, events={sso.EVENIMENT_LOGOUT: {}}, jti="x", sub="sub-ana@s.unibuc.ro"
    )
    assert client.post("/auth/backchannel-logout", data={"logout_token": jeton}).status_code == 200
    assert s.scalar(select(SsoSesiune)) is None
    assert client.get("/account", follow_redirects=False).status_code == 303


def test_logout_backchannel_refuza_tokenurile_false(client, s, sso_fals):
    intra_sso(client, sso_fals, email="ana@s.unibuc.ro")
    sid = s.scalar(select(SsoSesiune)).sid
    fara_eveniment = sso_fals.token(sid=sid)
    alt_client = sso_fals.token(sid=sid, aud="alt-client", events={sso.EVENIMENT_LOGOUT: {}})
    for jeton in (fara_eveniment, alt_client, "nu-e-jwt"):
        assert (
            client.post("/auth/backchannel-logout", data={"logout_token": jeton}).status_code == 400
        )
    assert s.scalar(select(SsoSesiune)) is not None


# ------------------------------------------------------------------ tokenurile


def test_access_token_al_altui_client_sau_emitent_e_refuzat(sso_fals):
    bun = sso_fals.token(sub="x")
    assert sso.verifica_access_token(sso_fals, bun)["sub"] == "x"
    for rau in (
        sso_fals.token(sub="x", aud="alt-client", azp="alt-client"),
        sso_fals.token(sub="x", iss="https://altundeva/realms/x"),
        sso_fals.token(sub="x", exp=1),
    ):
        with pytest.raises(sso.EroareSso):
            sso.verifica_access_token(sso_fals, rau)
    # semnat cu alta cheie
    from tests.conftest import FurnizorFals

    strain = FurnizorFals(sso_fals.cfg)
    with pytest.raises(sso.EroareSso):
        sso.verifica_access_token(sso_fals, strain.token(sub="x"))


def test_roluri_din_token():
    claims = {
        "realm_access": {"roles": ["student", "cddd"]},
        "resource_access": {
            SSO_CLIENT: {"roles": ["statistici"]},
            "alt": {"roles": ["pm"]},
        },
    }
    roluri = sso.roluri_din_token(claims, SSO_CLIENT)
    assert roluri == {"realm": ["cddd", "student"], "client": ["statistici"]}
    cfg = sso.Configurare(SSO_ISSUER, SSO_CLIENT, "x")
    assert sso.drepturi(roluri, cfg) == (True, frozenset({"statistici"}))


def test_oficiul_aduce_permisiunile_implicite():
    cfg = sso.Configurare(SSO_ISSUER, SSO_CLIENT, "x")
    for oficiu, asteptate in (
        ("secretar", {"editare", "sesizari", "incarcare", "evenimente"}),
        ("decanat", {"statistici", "structura", "evenimente", "sesizari"}),
        ("administrator", {"incarcare", "review", "editare", "sesizari", "evenimente", "statistici", "structura"}),
    ):
        roluri = {"realm": [oficiu], "client": []}
        admin, permisiuni = sso.drepturi(roluri, cfg)
        assert not admin
        assert permisiuni == frozenset(asteptate)
        assert sso.oficii(roluri) == frozenset({oficiu})
    # un rol de client se adauga peste oficiu
    roluri = {"realm": ["secretar"], "client": ["statistici"]}
    assert sso.drepturi(roluri, cfg)[1] == frozenset(
        {"editare", "sesizari", "incarcare", "evenimente", "statistici"}
    )


def test_permisiunile_au_oglinda_de_client_pe_roluri():
    from orar.db.models import PERMISIUNI

    assert {c for c, _ in PERMISIUNI} == set(sso.ROLURI_CLIENT.values())


def test_secretarul_intra_pe_sesizari_insa_nu_in_panou(client, sso_fals):
    intra_sso(client, sso_fals, email="sec@s.unibuc.ro", realm=("secretar",))
    assert client.get("/admin/sesizari").status_code == 200
    assert client.get("/admin").status_code == 403  # panoul e doar al lui cddd
    assert client.get("/admin/statistici").status_code == 403  # statisticile sunt ale decanatului


# ------------------------------------------------------------------ grupa propusa


def test_grupa_propusa_din_profilul_asmi(client, s, sso_fals):
    intra_sso(
        client, sso_fals, email="ana@s.unibuc.ro",
        faculty="Facultatea de Matematică și Informatică", studyYear="licenta-2", specialization="Informatică",
    )  # fmt: skip
    pagina = client.get("/account").text
    assert "Potrivite cu profilul tău ASMI" in pagina
    potrivite = pagina.split("Potrivite cu profilul tău ASMI")[1].split("</optgroup>")[0]
    assert ">244<" in potrivite


def test_alta_facultate_nu_propune_nimic(s):
    from orar.db.models import Grupa

    grupe = list(s.scalars(select(Grupa).where(Grupa.tip == "grupa")))
    assert (
        sso.grupe_potrivite(grupe, {"faculty": "Facultatea de Drept", "studyYear": "licenta-1"})
        == []
    )
    assert sso.grupe_potrivite(grupe, {"studyYear": "__alta__"}) == []


# ------------------------------------------------------------------ fara SSO


def test_fara_sso_ramane_doar_adminul_de_urgenta(client):
    r = client.get("/login")
    assert r.status_code == 200 and "Intră cu contul ASMI" not in r.text
    assert "Acces de urgență" in r.text
    assert client.get("/signup", follow_redirects=False).headers["location"] == "/login"
    r = client.post(
        "/login",
        data={"csrf": _csrf(client, "/login"), "email": ADMIN, "parola": PAROLA_ADMIN},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/admin"


def test_adminul_de_urgenta_merge_si_cu_sso(client, sso_fals):
    assert client.get("/login", follow_redirects=False).status_code == 302  # spre Keycloak
    r = client.get("/login?admin=1")
    assert r.status_code == 200 and "Acces de urgență" in r.text


def test_un_cont_din_baza_nu_mai_intra_cu_parola(client, s):
    from orar.web.auth import hash_parola

    s.add(User(nume="Ana", email="ana@s.unibuc.ro", parola_hash=hash_parola("parola-veche-lunga")))
    s.commit()
    r = client.post(
        "/login",
        data={
            "csrf": _csrf(client, "/login"),
            "email": "ana@s.unibuc.ro",
            "parola": "parola-veche-lunga",
        },
    )
    assert r.status_code == 401
