"""Ajutoarele abonamentului la calendar: linkurile, cookie-ul de client anonim, contextul
meniului „Exportă”.

Un vizitator fara cont isi regaseste abonamentul dupa un cookie opac (`CLIENT`), nu dupa
sesiune: feed-ul poate fi citit de Google/Apple fara cookie, dar meniul are nevoie sa stie
ce token sa arate. Cookie-ul nu e o credentiala -- tokenul din URL e.
"""

from __future__ import annotations

import os
import re
import secrets
from urllib.parse import quote

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from orar.db.models import AbonamentCalendar
from orar.web.auth import Cont

__all__ = [
    "COOKIE_CLIENT",
    "baza_publica",
    "client_din_cookie",
    "context_abonament",
    "domeniu_ics",
    "linkuri",
    "scrie_client",
    "token_nou",
]

COOKIE_CLIENT = "orar_abonament_client"
_RE_CLIENT = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_RE_TINTA = re.compile(r"^/(grupa|profesor)/([^/]+)$")
#: Domeniul din UID-urile ICS, cand `ORAR_ICS_DOMAIN` nu e setat.
DOMENIU_IMPLICIT = "orar.asmi.ro"


def domeniu_ics() -> str:
    return os.getenv("ORAR_ICS_DOMAIN", "").strip() or DOMENIU_IMPLICIT


def baza_publica(request: Request) -> str:
    """Radacina URL-urilor absolute: `ORAR_URL_PUBLICA` sau, in lipsa ei, gazda cererii.

    Uvicorn ruleaza cu `--proxy-headers`, deci `request.base_url` vede schema si gazda
    reale si dupa un proxy HTTPS.
    """
    return (os.getenv("ORAR_URL_PUBLICA", "").strip() or str(request.base_url)).rstrip("/")


def client_din_cookie(request: Request) -> str | None:
    valoare = request.cookies.get(COOKIE_CLIENT, "")
    return valoare if _RE_CLIENT.match(valoare) else None


def token_nou() -> str:
    return secrets.token_urlsafe(18)


def scrie_client(raspuns: Response, client: str) -> None:
    raspuns.set_cookie(
        COOKIE_CLIENT,
        client,
        max_age=400 * 24 * 3600,
        path="/",
        samesite="lax",
        httponly=True,
        secure=os.getenv("ORAR_HTTPS", "").strip().lower() in ("1", "true", "da", "yes"),
    )


def linkuri(request: Request, token: str) -> dict[str, str]:
    """Linkul feed-ului si cele trei intrari ale meniului."""
    baza = baza_publica(request)
    ics = f"{baza}/calendar/{token}.ics"
    return {
        "ics": ics,
        "google": "https://calendar.google.com/calendar/render?cid=" + quote(ics, safe=""),
        "webcal": "webcal://" + ics.split("://", 1)[-1],
        "ajutor": f"/calendar/{token}/ajutor",
    }


def _existent(
    s: Session,
    request: Request,
    cont: Cont | None,
    *,
    fel: str,
    al_meu: bool,
    grupa_slug: str | None,
    an_univ: str | None,
    profesor_nume: str | None,
) -> AbonamentCalendar | None:
    if al_meu and cont is not None and cont.user is not None:
        return s.scalar(
            select(AbonamentCalendar).where(
                AbonamentCalendar.user_id == cont.user.id,
                AbonamentCalendar.fel == fel,
                AbonamentCalendar.revocat_la.is_(None),
            )
        )
    client = client_din_cookie(request)
    if client is None:
        return None
    return s.scalar(
        select(AbonamentCalendar).where(
            AbonamentCalendar.client == client,
            AbonamentCalendar.fel == fel,
            AbonamentCalendar.grupa_slug == grupa_slug,
            AbonamentCalendar.an_univ == an_univ,
            AbonamentCalendar.profesor_nume == profesor_nume,
            AbonamentCalendar.revocat_la.is_(None),
        )
    )


def context_abonament(
    s: Session,
    request: Request,
    cont: Cont | None,
    *,
    fel: str,
    al_meu: bool,
    activ: bool = True,
    grupa_slug: str | None = None,
    an_univ: str | None = None,
    profesor_nume: str | None = None,
) -> dict[str, object]:
    """Ce pune pagina in context pentru meniul „Exportă”.

    `activ=False` pe arhive, pe fragmente si pe paginile unde abonamentul nu are sens: acolo
    meniul ramane doar cu imprimare si imagini.
    """
    if not activ:
        return {"abonament_formular": False, "abonament_fel": fel, "abonament": None}
    abonament = _existent(
        s,
        request,
        cont,
        fel=fel,
        al_meu=al_meu,
        grupa_slug=grupa_slug,
        an_univ=an_univ,
        profesor_nume=profesor_nume,
    )
    return {
        "abonament_formular": True,
        "abonament_fel": fel,
        "abonament": linkuri(request, abonament.token) if abonament is not None else None,
    }


def tinta_valida(inapoi: str) -> tuple[str, str] | None:
    """`(fel, identificator)` dintr-o adresa interna de grupa sau profesor, altfel None."""
    if not inapoi or "\\" in inapoi:
        return None
    cale = inapoi.split("?", 1)[0].split("#", 1)[0]
    potrivire = _RE_TINTA.match(cale)
    if potrivire is None:
        return None
    fel, identificator = potrivire.groups()
    return fel, identificator
