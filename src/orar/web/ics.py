"""Scriitorul de calendare ICS (RFC 5545) pentru abonamentul la orar.

Feed-ul are un `VEVENT` per ocurenta, fara `RRULE`: paritatea, intervalele de saptamani si
vacantele se rezolva cand se construieste feed-ul, iar clientul primeste date concrete.
Orele se scriu in UTC, ca sa nu fie nevoie de `VTIMEZONE`; ora Romaniei se aplica la
construire (`Europe/Bucharest`).

Nu punem `METHOD:PUBLISH`: unii clienti il trateaza ca invitatia unui organizator si cer
raspuns, desi e doar un calendar abonat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

__all__ = ["IntrareCalendar", "escapeaza", "pliaza", "scrie_calendar"]

#: Fusul orar in care sunt scrise orele din orar (ora Romaniei, cu ora de vara inclusa).
FUS = ZoneInfo("Europe/Bucharest")

#: RFC 5545: 75 de octeti pe linie, continuarea incepe cu un spatiu.
_LIMITA = 75


@dataclass(frozen=True)
class IntrareCalendar:
    """O ocurenta din feed: ora sau eveniment, cu sau fara ora de start."""

    uid: str
    inceput: datetime | date
    sfarsit: datetime | date
    titlu: str
    loc: str = ""
    descriere: str = ""
    toata_ziua: bool = False
    status: str = "CONFIRMED"


def escapeaza(text: str) -> str:
    """Escape-ul de text din RFC 5545: backslash, punct-virgula, virgula si newline."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def pliaza(linie: str) -> list[str]:
    """Rupe o linie la 75 de octeti UTF-8 fara sa taie vreun caracter multibyte."""
    bucati: list[str] = []
    curent = ""
    limita = _LIMITA
    for caracter in linie:
        if len((curent + caracter).encode("utf-8")) > limita:
            bucati.append(curent)
            curent = caracter
            limita = _LIMITA - 1  # linia de continuare incepe cu un spatiu
        else:
            curent += caracter
    bucati.append(curent)
    return bucati


def _utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=FUS)
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _zi(zi: date) -> str:
    return zi.strftime("%Y%m%d")


def _proprietati(nume: str, valoare: str) -> list[str]:
    bucati = pliaza(f"{nume}:{valoare}")
    # RFC 5545: linia de continuare incepe cu un spatiu, care nu face parte din valoare.
    return [bucati[0], *(f" {bucata}" for bucata in bucati[1:])]


def _eveniment(intrare: IntrareCalendar, acum: datetime) -> list[str]:
    linii = [
        "BEGIN:VEVENT",
        *_proprietati("UID", intrare.uid),
        f"DTSTAMP:{_utc(acum)}",
    ]
    if intrare.toata_ziua:
        inceput = (
            intrare.inceput.date() if isinstance(intrare.inceput, datetime) else intrare.inceput
        )
        sfarsit = (
            intrare.sfarsit.date() if isinstance(intrare.sfarsit, datetime) else intrare.sfarsit
        )
        if sfarsit <= inceput:
            sfarsit = inceput
        linii += [
            f"DTSTART;VALUE=DATE:{_zi(inceput)}",
            # DTEND e exclusiv: ziua urmatoare ultimei zile.
            f"DTEND;VALUE=DATE:{_zi(sfarsit + timedelta(days=1))}",
        ]
    else:
        linii += [
            f"DTSTART:{_utc(intrare.inceput)}",  # type: ignore[arg-type]
            f"DTEND:{_utc(intrare.sfarsit)}",  # type: ignore[arg-type]
        ]
    linii += _proprietati("SUMMARY", escapeaza(intrare.titlu))
    if intrare.loc:
        linii += _proprietati("LOCATION", escapeaza(intrare.loc))
    if intrare.descriere:
        linii += _proprietati("DESCRIPTION", escapeaza(intrare.descriere))
    linii += [f"STATUS:{intrare.status}", "END:VEVENT"]
    return linii


def scrie_calendar(
    nume: str,
    descriere: str,
    intrari: list[IntrareCalendar],
    acum: datetime | None = None,
) -> bytes:
    """Calendarul complet, gata de trimis pe fir, cu terminatoare CRLF."""
    acum = acum or datetime.now(UTC)
    linii = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ASMI//Orarul Salilor//RO",
        "CALSCALE:GREGORIAN",
        *_proprietati("X-WR-CALNAME", escapeaza(nume)),
        *_proprietati("X-WR-CALDESC", escapeaza(descriere)),
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for intrare in intrari:
        linii += _eveniment(intrare, acum)
    linii.append("END:VCALENDAR")
    return ("\r\n".join(linii) + "\r\n").encode("utf-8")
