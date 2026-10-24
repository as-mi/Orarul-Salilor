"""Autentificarea prin SSO-ul ASMI (Keycloak, OpenID Connect).

Cine esti o spune Keycloak; ce poti face se citeste tot de acolo:

* **rolurile de realm** sunt comune tuturor aplicatiilor ASMI. `cddd` (configurabil cu
  `ORAR_SSO_ROL_ADMIN`) face dintr-un cont un admin al Orarului;
* **rolurile de client**, pe `orarul-salilor-web`, sunt ale Orarului. Fiecare se traduce
  intr-o permisiune prin `ROLURI_CLIENT` -- o functionalitate noua inseamna o intrare acolo si
  rolul adaugat in `../SSO-ASMI/scripts/configure-orarul.sh`.

Rolurile locale ramase (student / voluntar / profesor, din `USER.ROL`) si toate preferintele
contului raman in baza Orarului; din profilul ASMI tinem doar `sub` (plus numele si emailul,
copie de afisare rescrisa la fiecare autentificare).

Fluxul e Authorization Code cu PKCE S256, pe un client confidential; il face Authlib, nu noi.
Tokenurile stau pe server (`SSO_SESIUNE`), nu in cookie. La fiecare `REIMPROSPATARE` rolurile
se recitesc cu refresh token-ul, iar un logout din alt sit ASMI ajunge aici prin backchannel.

Fara `ORAR_SSO_ISSUER`, SSO-ul e oprit: ramane doar intrarea adminului de urgenta din mediu.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time as _time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orar.db.models import Grupa, SsoSesiune, User

log = logging.getLogger(__name__)

#: Cat de des se recitesc rolurile din Keycloak (cu refresh token-ul).
REIMPROSPATARE = timedelta(minutes=5)
#: Rolurile de client ale Orarului si permisiunea pe care o dau (`Cont.poate`). E oglinda din
#: Keycloak a catalogului `PERMISIUNI`: o functionalitate noua inseamna o intrare aici si
#: rolul adaugat in `../SSO-ASMI/scripts/configure-orarul.sh`.
ROLURI_CLIENT = {
    "responsabil-tutoriate": "tutoriate",
    "incarcare": "incarcare",
    "review": "review",
    "editare": "editare",
    "sesizari": "sesizari",
    "evenimente": "evenimente",
    "statistici": "statistici",
    "structura": "structura",
    "utilizatori": "utilizatori",
}
#: Rolurile de realm care desemneaza un oficiu in Orar (pe langa `cddd`, adminul).
OFICII = ("secretar", "administrator", "decanat")
#: Permisiunile pe care le aduce implicit fiecare rol de realm din `OFICII`. Un admin le
#: poate ajusta apoi per-cont, din Orar (tabela PERMISIUNE).
ROLURI_REALM_PERMISIUNI = {
    "administrator": (
        "incarcare",
        "review",
        "editare",
        "sesizari",
        "evenimente",
        "statistici",
        "structura",
    ),
    "secretar": ("editare", "sesizari", "incarcare", "evenimente"),
    "decanat": ("statistici", "structura", "evenimente", "sesizari"),
}
#: Ce cerem la autentificare. `orar-profile` aduce campurile de studii, pentru grupa propusa.
SCOPE = "openid email profile roles orar-profile"
#: Campurile din profilul ASMI pastrate pentru propunerea grupei.
CAMPURI_PROFIL = ("university", "faculty", "studyYear", "specialization")
EVENIMENT_LOGOUT = "http://schemas.openid.net/event/backchannel-logout"


class EroareSso(Exception):
    """Un token sau un raspuns Keycloak pe care nu-l acceptam."""


@dataclass(frozen=True)
class Configurare:
    issuer: str
    client_id: str
    client_secret: str
    rol_admin: str = "cddd"

    @property
    def cont_asmi(self) -> str:
        """Pagina profilului in SSO (Account Console)."""
        return f"{self.issuer}/account"


def configurare() -> Configurare | None:
    issuer = os.getenv("ORAR_SSO_ISSUER", "").strip().rstrip("/")
    client_id = os.getenv("ORAR_SSO_CLIENT_ID", "").strip()
    secret = os.getenv("ORAR_SSO_CLIENT_SECRET", "").strip()
    if not (issuer and client_id and secret):
        return None
    return Configurare(
        issuer, client_id, secret, os.getenv("ORAR_SSO_ROL_ADMIN", "").strip() or "cddd"
    )


# ------------------------------------------------------------------ furnizorul


class Furnizor:
    """Vorbeste cu Keycloak: redirect, schimbul codului, refresh, chei, verificarea JWT.

    Testele il inlocuiesc cu unul fals (`seteaza_furnizorul`)."""

    def __init__(self, cfg: Configurare) -> None:
        from authlib.integrations.starlette_client import OAuth

        self.cfg = cfg
        self._oauth = OAuth()
        self._oauth.register(
            name="asmi",
            client_id=cfg.client_id,
            client_secret=cfg.client_secret,
            server_metadata_url=f"{cfg.issuer}/.well-known/openid-configuration",
            client_kwargs={"scope": SCOPE, "code_challenge_method": "S256"},
        )
        self._metadata: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None
        self._jwks_la = 0.0
        self._lacat = threading.Lock()

    @property
    def _client(self):  # noqa: ANN202
        return self._oauth.asmi

    async def redirect(self, request, redirect_uri: str, **extra: str):  # noqa: ANN001, ANN201
        return await self._client.authorize_redirect(request, redirect_uri, **extra)

    async def schimba_codul(self, request) -> dict[str, Any]:  # noqa: ANN001
        """Tokenurile pentru codul din callback; Authlib verifica state, nonce, PKCE si
        semnatura ID token-ului (claims-urile lui ajung in `userinfo`)."""
        from authlib.integrations.base_client import OAuthError

        try:
            return await self._client.authorize_access_token(request)
        except OAuthError as e:
            raise EroareSso(e.error or "autentificare refuzata") from e

    def metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            r = httpx.get(f"{self.cfg.issuer}/.well-known/openid-configuration", timeout=10)
            r.raise_for_status()
            self._metadata = r.json()
        return self._metadata

    def jwks(self, *, proaspat: bool = False) -> dict[str, Any]:
        with self._lacat:
            # cheile se schimba rar; o cheie necunoscuta (rotatie) le recere, dar nu des
            if self._jwks is None or (proaspat and _time.monotonic() - self._jwks_la > 60):
                r = httpx.get(self.metadata()["jwks_uri"], timeout=10)
                r.raise_for_status()
                self._jwks, self._jwks_la = r.json(), _time.monotonic()
            return self._jwks

    def reimprospateaza(self, refresh_token: str) -> dict[str, Any]:
        r = httpx.post(
            self.metadata()["token_endpoint"],
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(self.cfg.client_id, self.cfg.client_secret),
            timeout=10,
        )
        if r.status_code in (400, 401):
            raise EroareSso("sesiunea SSO a expirat sau a fost inchisa")
        r.raise_for_status()
        return r.json()

    def url_logout(self, id_token: str | None, inapoi: str) -> str | None:
        capat = self.metadata().get("end_session_endpoint")
        if not capat:
            return None
        parametri = {"post_logout_redirect_uri": inapoi, "client_id": self.cfg.client_id}
        if id_token:
            parametri["id_token_hint"] = id_token
        return f"{capat}?{urlencode(parametri)}"


_furnizor: Furnizor | None = None


def furnizor() -> Furnizor | None:
    """Furnizorul SSO, sau None daca SSO-ul nu e configurat."""
    global _furnizor
    cfg = configurare()
    if cfg is None:
        return None
    if _furnizor is None or _furnizor.cfg != cfg:
        _furnizor = Furnizor(cfg)
    return _furnizor


def seteaza_furnizorul(f: Furnizor | None) -> None:
    """Pentru teste: un furnizor fals, fara Keycloak."""
    global _furnizor
    _furnizor = f


# ------------------------------------------------------------------ tokenurile


def verifica_jwt(f: Furnizor, token: str) -> dict[str, Any]:
    """Claims-urile unui JWT semnat de Keycloak: semnatura (JWKS), emitentul si expirarea.
    Audienta o verifica apelantul -- difera intre access token si logout token."""
    from joserfc import jwt
    from joserfc.errors import JoseError
    from joserfc.jwk import KeySet

    def decodeaza(chei: dict[str, Any]) -> dict[str, Any]:
        return jwt.decode(
            token, KeySet.import_key_set(chei), algorithms=["RS256", "ES256", "PS256"]
        ).claims

    try:
        try:
            claims = decodeaza(f.jwks())
        except (JoseError, ValueError):
            claims = decodeaza(f.jwks(proaspat=True))  # poate s-au rotit cheile
        jwt.JWTClaimsRegistry(
            iss={"essential": True, "value": f.cfg.issuer}, exp={"essential": True}
        ).validate(claims)
    except (JoseError, ValueError) as e:
        raise EroareSso(f"token invalid: {e}") from e
    return claims


def _audiente(claims: dict[str, Any]) -> set[str]:
    aud = claims.get("aud") or []
    return {aud} if isinstance(aud, str) else set(aud)


def verifica_access_token(f: Furnizor, token: str) -> dict[str, Any]:
    claims = verifica_jwt(f, token)
    if f.cfg.client_id not in _audiente(claims) and claims.get("azp") != f.cfg.client_id:
        raise EroareSso("access token-ul nu e emis pentru Orar")
    return claims


def verifica_logout_token(f: Furnizor, token: str) -> dict[str, Any]:
    claims = verifica_jwt(f, token)
    if f.cfg.client_id not in _audiente(claims):
        raise EroareSso("logout token-ul nu e pentru Orar")
    if EVENIMENT_LOGOUT not in (claims.get("events") or {}) or "nonce" in claims:
        raise EroareSso("nu e un logout token")
    if not (claims.get("sid") or claims.get("sub")):
        raise EroareSso("logout token fara sid si sub")
    return claims


def roluri_din_token(claims: dict[str, Any], client_id: str) -> dict[str, list[str]]:
    """`{"realm": [...], "client": [...]}` din access token."""
    realm = (claims.get("realm_access") or {}).get("roles") or []
    client = ((claims.get("resource_access") or {}).get(client_id) or {}).get("roles") or []
    return {"realm": sorted(set(realm)), "client": sorted(set(client))}


def drepturi(roluri: dict[str, list[str]], cfg: Configurare | None) -> tuple[bool, frozenset[str]]:
    """(e admin, permisiunile) din rolurile SSO ale unei sesiuni.

    Admin e rolul de realm `cddd`. Permisiunile vin din rolurile de client (oglinda din
    Keycloak) si din rolurile de realm ale oficiilor (`secretar`, `administrator`, `decanat`),
    care aduc fiecare un set implicit."""
    admin = cfg is not None and cfg.rol_admin in roluri.get("realm", [])
    permisiuni: set[str] = set()
    for rol in roluri.get("realm", []):
        permisiuni.update(ROLURI_REALM_PERMISIUNI.get(rol, ()))
    for rol in roluri.get("client", []):
        if rol in ROLURI_CLIENT:
            permisiuni.add(ROLURI_CLIENT[rol])
    return admin, frozenset(permisiuni)


def oficii(roluri: dict[str, list[str]]) -> frozenset[str]:
    """Rolurile de realm care spun oficiul contului (secretar/administrator/decanat)."""
    return frozenset(r for r in roluri.get("realm", []) if r in OFICII)


# ------------------------------------------------------------------ contul


def leaga_sau_creeaza(s: Session, claims: dict[str, Any]) -> User:
    """Contul Orarului pentru o identitate SSO.

    Dupa `sub`; altfel, daca emailul e verificat in Keycloak, contul local vechi cu acelasi
    email (care isi pastreaza grupa, alegerile si tutoriatele, dar nu si parola); altfel unul
    nou. Numele si emailul se rescriu din token: Keycloak e sursa lor.
    """
    from orar.db.tutoriate import leaga_invitatiile

    sub = str(claims.get("sub") or "")
    if not sub:
        raise EroareSso("token fara sub")
    email = (claims.get("email") or "").strip().lower() or None
    nume = (
        claims.get("name")
        or " ".join(x for x in (claims.get("given_name"), claims.get("family_name")) if x)
        or claims.get("preferred_username")
        or email
        or "Cont ASMI"
    )[:120]

    user = s.scalar(select(User).where(User.sso_sub == sub))
    if user is None and email and claims.get("email_verified"):
        user = s.scalar(select(User).where(func.lower(User.email) == email, User.sso_sub.is_(None)))
    if user is None:
        user = User(nume=nume)
        s.add(user)
    user.sso_sub = sub
    user.nume = nume
    user.parola_hash = None
    s.flush()
    if email and email != (user.email or "").lower():
        # adresa poate fi deja a altui cont local, nelegat (email neverificat): o lasam acolo
        ocupata = s.scalar(
            select(User.id).where(func.lower(User.email) == email, User.id != user.id)
        )
        if ocupata is None:
            user.email = email
    s.flush()
    leaga_invitatiile(s, user)
    return user


def deschide_sesiunea(
    s: Session,
    user: User,
    tokenuri: dict[str, Any],
    claims_access: dict[str, Any],
    cfg: Configurare,
) -> SsoSesiune:
    acum = datetime.now()
    id_claims = tokenuri.get("userinfo") or {}
    sesiune = SsoSesiune(
        id=secrets.token_urlsafe(32),
        user=user,
        creata_la=acum,
        roluri="{}",
        verificat_la=acum,
        expira_la=acum,
    )
    actualizeaza_sesiunea(sesiune, tokenuri, claims_access, cfg)
    sesiune.sid = str(claims_access.get("sid") or id_claims.get("sid") or "")[:64] or None
    profil = {k: str(v) for k in CAMPURI_PROFIL if (v := id_claims.get(k) or claims_access.get(k))}
    sesiune.profil = json.dumps(profil, ensure_ascii=False) if profil else None
    s.add(sesiune)
    return sesiune


def actualizeaza_sesiunea(
    sesiune: SsoSesiune, tokenuri: dict[str, Any], claims_access: dict[str, Any], cfg: Configurare
) -> None:
    acum = datetime.now()
    sesiune.roluri = json.dumps(roluri_din_token(claims_access, cfg.client_id))
    sesiune.refresh_token = tokenuri.get("refresh_token") or sesiune.refresh_token
    sesiune.id_token = tokenuri.get("id_token") or sesiune.id_token
    sesiune.verificat_la = acum
    # cat traieste refresh token-ul; fara el, cat access token-ul
    durata = tokenuri.get("refresh_expires_in") or tokenuri.get("expires_in") or 300
    sesiune.expira_la = (
        acum + timedelta(seconds=int(durata)) if int(durata) > 0 else acum + timedelta(days=30)
    )


def roluri_sesiunii(sesiune: SsoSesiune) -> dict[str, list[str]]:
    try:
        date_ = json.loads(sesiune.roluri or "{}")
    except ValueError:
        return {}
    return date_ if isinstance(date_, dict) else {}


def sesiune_valida(s: Session, sesiune_id: str) -> SsoSesiune | None:
    """Sesiunea SSO din cookie, reimprospatata daca rolurile sunt vechi; None daca a expirat
    sau Keycloak a inchis-o (atunci o si stergem)."""
    sesiune = s.get(SsoSesiune, sesiune_id)
    if sesiune is None:
        return None
    acum = datetime.now()
    if sesiune.expira_la < acum:
        s.delete(sesiune)
        s.commit()
        return None
    f = furnizor()
    if f is not None and sesiune.refresh_token and acum - sesiune.verificat_la > REIMPROSPATARE:
        try:
            tokenuri = f.reimprospateaza(sesiune.refresh_token)
            claims = verifica_access_token(f, tokenuri["access_token"])
        except EroareSso:
            s.delete(sesiune)
            s.commit()
            return None
        except (httpx.HTTPError, KeyError) as e:
            # Keycloak indisponibil: nu dam pe nimeni afara, incercam din nou mai tarziu
            log.warning("Nu am putut reimprospata sesiunea SSO: %s", e)
            return sesiune
        actualizeaza_sesiunea(sesiune, tokenuri, claims, f.cfg)
        s.commit()
    return sesiune


def inchide_dupa_logout_token(s: Session, claims: dict[str, Any]) -> int:
    """Sterge sesiunile pe care le inchide un logout backchannel (dupa `sid`, altfel dupa
    `sub`). Intoarce cate au fost."""
    if sid := claims.get("sid"):
        sesiuni = list(s.scalars(select(SsoSesiune).where(SsoSesiune.sid == str(sid))))
    else:
        sesiuni = list(
            s.scalars(select(SsoSesiune).join(User).where(User.sso_sub == str(claims.get("sub"))))
        )
    for x in sesiuni:
        s.delete(x)
    s.commit()
    return len(sesiuni)


# ------------------------------------------------------------------ grupa propusa


def _simplu(text: str) -> str:
    fara = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return " ".join(fara.lower().replace("-", " ").split())


def grupe_potrivite(grupe: list[Grupa], profil: dict[str, str]) -> list[Grupa]:
    """Grupele care se potrivesc profilului ASMI (anul de studiu si specializarea), daca
    profilul e de la FMI. Lista goala cand nu se potriveste nimic sigur."""
    from orar.db.evenimente import cheie_an
    from orar.domain.hierarchy import DENUMIRI_SPECIALIZARE

    facultate = _simplu(profil.get("faculty", ""))
    if facultate and "matematica si informatica" not in facultate:
        return []
    an = profil.get("studyYear", "")
    nivel, _, numar = an.partition("-")
    cheie = {"licenta": "L", "master": "M"}.get(nivel, "") + numar if numar.isdigit() else None
    if cheie is None:
        return []
    specializare = _simplu(profil.get("specialization", ""))
    coduri = {
        cod
        for cod, nume in DENUMIRI_SPECIALIZARE.items()
        if specializare and (specializare == _simplu(nume) or specializare == _simplu(cod))
    }
    return [g for g in grupe if cheie_an(g) == cheie and (not coduri or g.specializare in coduri)]
