"""Fixturi comune.

Baza de test se construieste o singura data pe sesiune, dintr-un SQLite in memorie
incarcat din setul golden. E suficient de rapid (~1 s) si testeaza exact drumul real:
JSON -> loader -> consolidare -> interogari.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from orar.db.models import Base
from orar.ingest.consolidate import consolideaza
from orar.ingest.load import incarca_pagini

RADACINA = Path(__file__).resolve().parent
GOLDEN = RADACINA / "golden" / "date.json"
FIXTURI = RADACINA / "fixtures"


@pytest.fixture(scope="session")
def pagini_golden() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _engine_memorie():
    """SQLite in memorie, vizibil din toate firele de executie.

    Implicit, fiecare *conexiune* la `sqlite://` primeste propria baza goala, iar
    TestClient ruleaza aplicatia pe alt fir -- deci ar vedea o baza fara tabele.
    StaticPool tine o singura conexiune partajata.
    """
    return create_engine(
        "sqlite://",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


@pytest.fixture(scope="session")
def _engine():
    engine = _engine_memorie()

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # noqa: ANN001
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture(scope="session")
def _sesiune_incarcata(_engine, pagini_golden) -> Session:
    """Baza cu golden-ul incarcat SI consolidat -- starea reala a aplicatiei.

    Ingestul ruleaza in sesiunea lui, apoi testele primesc una noua. Asa e si in
    productie (worker-ul scrie, web-ul citeste), si asa nu putem trece din greseala
    teste care de fapt citesc obiecte ramase in identity map-ul ingestului.
    """
    Sesiune = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    with Sesiune() as ingest:
        incarca_pagini(ingest, pagini_golden)
        consolideaza(ingest, an_universitar="2025-2026")
        ingest.commit()
    return Sesiune()


@pytest.fixture
def db(_sesiune_incarcata) -> Session:
    return _sesiune_incarcata


@pytest.fixture(scope="session")
def sesiune_neconsolidata(_engine, pagini_golden) -> Session:
    """Sesiune separata, fara consolidare -- pentru testele care o masoara."""
    engine = _engine_memorie()
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    incarca_pagini(s, pagini_golden)
    s.commit()
    return s


@pytest.fixture
def client(db):
    """Client HTTP legat de baza de test.

    `dependency_overrides` inlocuieste `get_db` peste tot -- inclusiv pentru dependinta de
    autentificare care ruleaza pe toata aplicatia -- deci si bara de sus vede aceleasi date
    ca rutele, nu baza reala de pe disc.
    """
    from fastapi.testclient import TestClient

    from orar.web import deps
    from orar.web.app import app

    app.dependency_overrides[deps.get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ------------------------------------------------------------------ SSO-ul ASMI, fals
#
# Fara Keycloak: furnizorul fals semneaza JWT-uri reale cu o cheie RSA locala, deci
# verificarea din aplicatie (semnatura, emitent, audienta, expirare) ruleaza neschimbata.
# Doar redirectul la Keycloak si schimbul codului sunt simulate.

SSO_ISSUER = "https://sso.test/realms/asmi-test"
SSO_CLIENT = "orarul-salilor-web"


@pytest.fixture(autouse=True)
def _fara_sso_din_mediu(monkeypatch):
    """Testele nu mostenesc `.env`-ul dezvoltatorului.

    `app.py` incarca `.env` la import, deci o instalare locala cu SSO configurat ar porni
    SSO-ul in toate testele si ar strica cele care verifica tocmai lipsa lui. Fiecare test
    porneste de la SSO oprit; `sso_fals` e cel care il activeaza explicit.
    """
    from orar.web import sso

    for cheie in ("ORAR_SSO_ISSUER", "ORAR_SSO_CLIENT_ID", "ORAR_SSO_CLIENT_SECRET", "ORAR_SSO_ROL_ADMIN"):
        monkeypatch.delenv(cheie, raising=False)
    sso.seteaza_furnizorul(None)


class FurnizorFals:
    """Inlocuieste `web.sso.Furnizor`. `identitate` e cine se autentifica la urmatorul
    login (si ce roluri primeste la urmatorul refresh)."""

    def __init__(self, cfg) -> None:  # noqa: ANN001
        from joserfc.jwk import RSAKey

        self.cfg = cfg
        self.cheie = RSAKey.generate_key(2048, parameters={"kid": "cheie-test"})
        self.identitate: dict = {}
        self.refuza_refresh = False
        self.extra: dict = {}

    def token(self, **claims) -> str:  # noqa: ANN003
        import time

        from joserfc import jwt

        acum = int(time.time())
        continut = {"iss": SSO_ISSUER, "aud": SSO_CLIENT, "iat": acum, "exp": acum + 300, **claims}
        return jwt.encode({"alg": "RS256", "kid": "cheie-test"}, continut, self.cheie)

    def jwks(self, *, proaspat: bool = False) -> dict:
        from joserfc.jwk import KeySet

        return KeySet([self.cheie]).as_dict(private=False)

    def metadata(self) -> dict:
        baza = f"{SSO_ISSUER}/protocol/openid-connect"
        return {"end_session_endpoint": f"{baza}/logout", "token_endpoint": f"{baza}/token"}

    def url_logout(self, id_token, inapoi):  # noqa: ANN001, ANN201
        from orar.web.sso import Furnizor

        return Furnizor.url_logout(self, id_token, inapoi)

    async def redirect(self, request, redirect_uri: str, **extra: str):  # noqa: ANN001, ANN201
        from fastapi.responses import RedirectResponse

        self.extra = extra
        return RedirectResponse(f"{redirect_uri}?code=cod-test&state=stare-test", status_code=302)

    def _tokenuri(self) -> dict:
        i = dict(self.identitate)
        roluri = {
            "realm_access": {"roles": i.pop("realm", [])},
            "resource_access": {SSO_CLIENT: {"roles": i.pop("client", [])}},
        }
        sid = i.pop("sid", "sid-" + i.get("sub", "x"))
        return {
            "access_token": self.token(sid=sid, azp=SSO_CLIENT, **roluri, sub=i.get("sub")),
            "refresh_token": "refresh-" + i.get("sub", "x"),
            "id_token": "id-token-test",
            "expires_in": 300,
            "refresh_expires_in": 1800,
            "userinfo": {"sid": sid, **i},
        }

    async def schimba_codul(self, request) -> dict:  # noqa: ANN001
        return self._tokenuri()

    def reimprospateaza(self, refresh_token: str) -> dict:
        from orar.web.sso import EroareSso

        if self.refuza_refresh:
            raise EroareSso("sesiune inchisa")
        return self._tokenuri()


@pytest.fixture
def sso_fals(monkeypatch):
    """SSO-ul pornit, cu furnizorul fals."""
    from orar.web import sso

    monkeypatch.setenv("ORAR_SSO_ISSUER", SSO_ISSUER)
    monkeypatch.setenv("ORAR_SSO_CLIENT_ID", SSO_CLIENT)
    monkeypatch.setenv("ORAR_SSO_CLIENT_SECRET", "secret-test")
    monkeypatch.delenv("ORAR_SSO_ROL_ADMIN", raising=False)
    f = FurnizorFals(sso.configurare())
    sso.seteaza_furnizorul(f)
    yield f
    sso.seteaza_furnizorul(None)


def intra_sso(  # noqa: PLR0913
    client,  # noqa: ANN001
    furnizor: FurnizorFals,
    *,
    email: str,
    nume: str = "Cont Test",
    sub: str | None = None,
    realm: tuple[str, ...] = ("student",),
    roluri_client: tuple[str, ...] = (),
    email_verified: bool = True,
    inapoi: str = "",
    **profil: str,
):
    """Autentificare completa prin SSO-ul fals: /login -> /auth/callback. Intoarce raspunsul
    callback-ului (redirectul final, neurmat)."""
    furnizor.identitate = {
        "sub": sub or f"sub-{email}",
        "email": email,
        "email_verified": email_verified,
        "name": nume,
        "realm": list(realm),
        "client": list(roluri_client),
        **profil,
    }
    r = client.get("/login" + (f"?inapoi={inapoi}" if inapoi else ""), follow_redirects=False)
    assert r.status_code == 302, r.text[:300]
    return client.get(r.headers["location"], follow_redirects=False)
