"""Incarca orarul tutoriatelor (grila aSc cu celule albe) in tabela TUTORIAT.

Spre deosebire de orarul grupelor, fiecare celula e o sedinta de tutoriat: `materie` + `sala`,
fara tutore (tutorii se asigneaza separat). Pagina e per serie: `Tutoriat (MATE seria 10)`,
`Tutoriat (INFO serile 13, 14, 15)`. Ingestia le creeaza direct publicate: orarul facultatii
e sursa oficiala, iar studentii trebuie sa le vada imediat dupa incarcare; responsabilul le
poate retrage oricand in ciorna din panoul de tutoriate.

Citirea celulei e simpla: parserul de orar (tuned pe `materie + profesor + sala`) pune materia
tutoriatei in campul `profesor` cand nu o recunoaste in vocabular. Aici nu ne bazam pe acele
campuri: luam grupurile brute din celula si le separam pe sala (recunoscuta) de restul (materia).

Idempotenta: tutoriatele aduse din orar (marcate prin `creat_de` = `CREAT_DE_ORAR`) se sterg si
se recreeaza la fiecare incarcare; cele facute de mana (CSV, formular) raman neatinse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from orar.db.models import Sala, Tutoriat
from orar.db.structura import perioade_anului
from orar.domain.hierarchy import TipPagina, parse_titlu
from orar.domain.rooms import normalizeaza_sala

__all__ = ["RaportTutoriate", "CREAT_DE_ORAR", "incarca_tutoriate"]

#: Marca tutoriatelor aduse automat din orar (spre deosebire de cele facute de mana).
CREAT_DE_ORAR = "orarul tutoriatelor"


@dataclass
class RaportTutoriate:
    pagini: int = 0
    tutoriate: int = 0
    avertismente: list[str] = field(default_factory=list)


def _interval_semestru(s: Session, an_univ: str, semestru: int) -> tuple[date, date]:
    """Perioada didactica a semestrului; fara structura anului, semestrele standard."""
    didactice = [
        p
        for p in perioade_anului(s, an_univ)
        if p.fel == "didactica" and p.semestru == semestru and p.pentru != "terminali"
    ]
    if didactice:
        return min(p.data_inceput for p in didactice), max(p.data_sfarsit for p in didactice)
    inceput = int(an_univ.split("-")[0])
    if semestru == 1:
        return date(inceput, 10, 1), date(inceput + 1, 2, 1)
    return date(inceput + 1, 2, 1), date(inceput + 1, 6, 1)


def _materie_din_brut(brut: list[str], sala: str) -> str:
    """Materia = grupurile brute din celula, fara cel al salii."""
    slug_sala = normalizeaza_sala(sala).slug if sala else ""
    rest = [g for g in brut if normalizeaza_sala(g).slug != slug_sala]
    return " ".join(rest).strip()


def _sala(s: Session, cache: dict[str, Sala], nume: str) -> Sala | None:
    if not nume.strip():
        return None
    norm = normalizeaza_sala(nume)
    if norm.slug in cache:
        return cache[norm.slug]
    gasita = s.scalar(select(Sala).where(Sala.slug == norm.slug))
    if gasita is None:
        gasita = Sala(nume=norm.nume, slug=norm.slug, tip=norm.tip.value)
        s.add(gasita)
    cache[norm.slug] = gasita
    return gasita


def incarca_tutoriate(
    s: Session,
    pagini: list[dict],
    *,
    an_universitar: str,
    semestru: int,
) -> RaportTutoriate:
    """Creeaza tutoriatele semestrului. `pagini` sunt paginile citite: fiecare un dict cu
    `titlu` si `activitati` (o lista cu `zi`, `ore`, `sala` si `brut`)."""
    de_la, pana_la = _interval_semestru(s, an_universitar, semestru)
    sali: dict[str, Sala] = {}

    s.execute(
        delete(Tutoriat).where(
            Tutoriat.an_univ == an_universitar,
            Tutoriat.semestru == semestru,
            Tutoriat.creat_de == CREAT_DE_ORAR,
        )
    )

    rap = RaportTutoriate()
    for pagina in pagini:
        titlu = parse_titlu(pagina.get("titlu", ""))
        if titlu.tip is not TipPagina.TUTORIAT:
            continue
        serii = ",".join(f"seria-{n}" for n in titlu.serii_tinta)
        rap.pagini += 1
        for act in pagina.get("activitati", []):
            ore = (act.get("ore") or "").split("-")
            if len(ore) != 2 or not ore[0].isdigit() or not ore[1].isdigit():
                rap.avertismente.append(
                    f"{titlu.raw} / {act.get('zi')}: intervalul {act.get('ore')!r} nu se înțelege"
                )
                continue
            materie = _materie_din_brut(act.get("brut", []), act.get("sala") or "")
            if not materie:
                continue
            s.add(
                Tutoriat(
                    an_univ=an_universitar,
                    semestru=semestru,
                    materie=materie[:160],
                    zi_saptamana=act["zi"],
                    ora_inceput=time(int(ore[0])),
                    ora_sfarsit=time(int(ore[1])),
                    sala=_sala(s, sali, act.get("sala") or ""),
                    data_inceput=de_la,
                    data_sfarsit=pana_la,
                    specializari=titlu.specializare or "",
                    serii=serii,
                    stare="publicat",
                    creat_de=CREAT_DE_ORAR,
                    creat_la=datetime.now(),
                )
            )
            rap.tutoriate += 1
    return rap
