"""Abonamentele la calendarul ICS: tokenul, amprenta si preferintele tinute pe abonament.

Un abonament are un singur token (singura credentiala a feed-ului public) si un subiect:
o grupa sau un profesor. La `MOD='cont'` preferintele se citesc live din cont; la
`MOD='instantaneu'` ele stau in `OPTIUNI`, ca sa mearga si pentru vizitatorii fara cont.

Amprenta nu contine selectia: altfel fiecare bifa ar crea alt token, iar abonamentul vechi
ar ramane in calendarul utilizatorului. Continutul `AMPRENTA`:
`(fel, mod, user_id sau client, grupa_slug, an_univ, profesor_nume)`.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from orar.db.models import AbonamentCalendar, User

__all__ = [
    "PreferinteAbonament",
    "amprenta",
    "atinge",
    "creeaza_sau_actualizeaza",
    "curata",
    "dupa_token",
    "preferinte_din_json",
    "revoca",
]


@dataclass(frozen=True)
class PreferinteAbonament:
    """Ce vede un abonament: semigrupa, activitatile ascunse si tutoriatele alese."""

    semigrupa: str | None = None
    ascunse: frozenset[str] = frozenset()
    tutoriate: frozenset[int] = frozenset()

    def ca_json(self) -> str:
        date = {
            "semigrupa": self.semigrupa,
            "ascunse": sorted(self.ascunse),
            "tutoriate": sorted(self.tutoriate),
        }
        return json.dumps(date, separators=(",", ":"), ensure_ascii=False)


def preferinte_din_json(text: str | None) -> PreferinteAbonament:
    """Preferintele dintr-un snapshot; gol sau strictat = fara restrictii."""
    if not text:
        return PreferinteAbonament()
    try:
        date = json.loads(text)
    except (TypeError, ValueError):
        return PreferinteAbonament()
    if not isinstance(date, dict):
        return PreferinteAbonament()
    semigrupa = date.get("semigrupa")
    ascunse = date.get("ascunse")
    tutoriate = date.get("tutoriate")
    return PreferinteAbonament(
        semigrupa=semigrupa if isinstance(semigrupa, str) else None,
        ascunse=frozenset(x for x in ascunse if isinstance(x, str)) if isinstance(ascunse, list) else frozenset(),
        tutoriate=frozenset(x for x in tutoriate if isinstance(x, int)) if isinstance(tutoriate, list) else frozenset(),
    )


def amprenta(
    *,
    fel: str,
    mod: str,
    user_id: int | None = None,
    client: str | None = None,
    grupa_slug: str | None = None,
    an_univ: str | None = None,
    profesor_nume: str | None = None,
) -> str:
    """Cheia stabila a unui abonament, fara selectie (vezi docstring-ul modulului)."""
    bucati = (
        fel,
        mod,
        str(user_id or ""),
        client or "",
        grupa_slug or "",
        an_univ or "",
        profesor_nume or "",
    )
    return hashlib.sha256("\x1f".join(bucati).encode("utf-8")).hexdigest()


def creeaza_sau_actualizeaza(
    s: Session,
    *,
    fel: str,
    mod: str,
    semestru: int,
    preferinte: PreferinteAbonament,
    user: User | None = None,
    client: str | None = None,
    grupa_slug: str | None = None,
    an_univ: str | None = None,
    profesor_nume: str | None = None,
    acum: datetime | None = None,
) -> AbonamentCalendar:
    """Intoarce abonamentul potrivit contextului, creandu-l doar daca nu exista.

    Un abonament revocat se reactiva cu un token nou. Preferintele se actualizeaza in
    acelasi rand, ca o re-subscriere dupa schimbarea biftelor sa nu lase un calendar vechi
    in urma.
    """
    if mod == "cont" and user is None:
        raise ValueError("Abonamentul din cont are nevoie de un utilizator.")
    if mod == "instantaneu" and not client:
        raise ValueError("Abonamentul instantaneu are nevoie de un client.")
    acum = acum or datetime.now()
    amp = amprenta(
        fel=fel,
        mod=mod,
        user_id=user.id if user else None,
        client=client,
        grupa_slug=grupa_slug,
        an_univ=an_univ,
        profesor_nume=profesor_nume,
    )
    existent = s.scalar(select(AbonamentCalendar).where(AbonamentCalendar.amprenta == amp))
    if existent is not None:
        if existent.revocat_la is not None:
            existent.token = secrets.token_urlsafe(32)
            existent.revocat_la = None
        existent.semestru = semestru
        existent.optiuni = preferinte.ca_json() if mod == "instantaneu" else None
        existent.grupa_slug = grupa_slug or existent.grupa_slug
        existent.an_univ = an_univ or existent.an_univ
        existent.profesor_nume = profesor_nume or existent.profesor_nume
        existent.modificat_la = acum
        s.flush()
        return existent

    abonament = AbonamentCalendar(
        token=secrets.token_urlsafe(32),
        fel=fel,
        mod=mod,
        user_id=user.id if user else None,
        grupa_slug=grupa_slug,
        an_univ=an_univ,
        profesor_nume=profesor_nume,
        semestru=semestru,
        optiuni=preferinte.ca_json() if mod == "instantaneu" else None,
        client=client,
        amprenta=amp,
        creat_la=acum,
        modificat_la=acum,
    )
    s.add(abonament)
    s.flush()
    return abonament


def dupa_token(s: Session, token: str) -> AbonamentCalendar | None:
    return s.scalar(select(AbonamentCalendar).where(AbonamentCalendar.token == token))


def atinge(s: Session, abonament: AbonamentCalendar, acum: datetime | None = None) -> None:
    """Noteaza ultimul acces, ca sa putem curata abonamentele uitate."""
    abonament.accesat_la = acum or datetime.now()
    s.flush()


def revoca(s: Session, abonament: AbonamentCalendar, acum: datetime | None = None) -> None:
    abonament.revocat_la = acum or datetime.now()
    s.flush()


def curata(s: Session, acum: datetime | None = None, *, zile: int = 400) -> int:
    """Sterge abonamentele nefolosite de `zile` zile. Optional, din scheduler/CLI."""
    limita = (acum or datetime.now()) - timedelta(days=zile)
    rezultat = s.execute(
        delete(AbonamentCalendar).where(
            or_(
                AbonamentCalendar.accesat_la < limita,
                and_(
                    AbonamentCalendar.accesat_la.is_(None),
                    AbonamentCalendar.creat_la < limita,
                ),
            )
        )
    )
    return int(rezultat.rowcount or 0)
