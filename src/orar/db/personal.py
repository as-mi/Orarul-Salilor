"""Orarul propriu: evenimentele personale si activitatile importate de pe alte grupe/serii.

Un cont isi poate personaliza orarul cu doua feluri de blocuri, tinute pe cont:

* **evenimente personale** -- saptamanale (zi + interval) sau cu data fixa, cu frecventa
  SI/SP optionala la cele saptamanale;
* **activitati importate** -- copii ale activitatilor altor formatiuni, cu sursa scrisa pe
  ele. Sunt snapshot-uri (nu referinte la ORA), ca sa reziste reincarcarii orarului.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from orar.db.evenimente import saptamana_calendaristica
from orar.db.models import ActivitateImportata, EvenimentPersonal, Grupa, Perioada, User
from orar.db.queries import ore_pentru_grupa
from orar.domain.grid import ZILE, ZILE_SAPTAMANA, EvenimentInZi

__all__ = [
    "BlocPersonal",
    "blocuri_personale",
    "formatiuni_import",
    "activitati_import",
    "evenimentele_contului",
    "importatele_contului",
]


@dataclass
class BlocPersonal:
    """Un bloc personal (eveniment custom sau activitate importata), in forma pe care o
    deseneaza grila -- ca un eveniment, cu `fel` distinct."""

    fel: str  # "personal" sau "importat"
    id: int
    titlu: str
    zi_saptamana: str
    ora_inceput: time
    ora_sfarsit: time
    loc: str = ""  # eveniment: locul; importat: sala
    sursa: str = ""  # importat: formatiunea de la care a venit
    tip: str = ""  # importat: curs/lab/seminar/proiect
    profesor: str = ""  # importat
    frecventa: str | None = None
    saptamani: str | None = None
    data: date | None = None  # eveniment cu data fixa

    @property
    def data_inceput(self) -> date | None:
        return self.data


def _bloc_eveniment(ev: EvenimentPersonal) -> BlocPersonal:
    return BlocPersonal(
        fel="personal",
        id=ev.id,
        titlu=ev.titlu,
        zi_saptamana=ev.zi_saptamana or "",
        ora_inceput=ev.ora_inceput,
        ora_sfarsit=ev.ora_sfarsit,
        loc=ev.loc or "",
        frecventa=ev.frecventa,
        data=ev.data,
    )


def _bloc_importat(im: ActivitateImportata) -> BlocPersonal:
    return BlocPersonal(
        fel="importat",
        id=im.id,
        titlu=im.materie,
        zi_saptamana=im.zi_saptamana,
        ora_inceput=im.ora_inceput,
        ora_sfarsit=im.ora_sfarsit,
        loc=im.sala or "",
        sursa=im.sursa,
        tip=im.tip or "",
        profesor=im.profesor or "",
        frecventa=im.frecventa,
        saptamani=im.saptamani,
    )


def blocuri_personale(s: Session, user: User, zi: date) -> list[EvenimentInZi]:
    """Blocurile personale ale contului, de pus in grila saptamanii lui `zi`."""
    luni, duminica = saptamana_calendaristica(zi)
    blocuri: list[EvenimentInZi] = []

    evenimente = s.scalars(
        select(EvenimentPersonal).where(EvenimentPersonal.user_id == user.id)
    )
    for ev in evenimente:
        if ev.zi_saptamana is not None:
            zi_bloc = ev.zi_saptamana
        else:
            # eveniment cu data fixa: apare doar in saptamana lui
            if not luni <= ev.data <= duminica:
                continue
            zi_bloc = ZILE_SAPTAMANA[ev.data.weekday()]
        blocuri.append(EvenimentInZi(eveniment=_bloc_eveniment(ev), zi=zi_bloc))

    importate = s.scalars(
        select(ActivitateImportata).where(ActivitateImportata.user_id == user.id)
    )
    for im in importate:
        blocuri.append(EvenimentInZi(eveniment=_bloc_importat(im), zi=im.zi_saptamana))

    return blocuri


def formatiuni_import(s: Session, perioada: Perioada | None) -> list[Grupa]:
    """Grupele si seriile din care se poate importa, in orarul dat."""
    stmt = select(Grupa).where(Grupa.tip.in_(("grupa", "serie"))).order_by(Grupa.nume)
    if perioada is not None:
        stmt = stmt.where(Grupa.an_universitar == perioada.an_univ)
    return list(s.scalars(stmt))


def activitati_import(s: Session, formatiune_id: int, perioada: Perioada | None) -> list:
    """Activitatile unei formatiuni, pentru lista de import (cu mostenire), ordonate pe
    materie, zi si ora ca sa fie usor de parcurs."""
    activitati = list(
        ore_pentru_grupa(
            s, formatiune_id, perioada_id=perioada.id if perioada is not None else None
        )
    )
    zile = {z: i for i, z in enumerate(ZILE)}
    return sorted(
        activitati,
        key=lambda o: (
            o.materie.nume if o.materie is not None else "?",
            zile.get(o.zi_saptamana, 9),
            o.ora_inceput,
        ),
    )


def evenimentele_contului(s: Session, user: User) -> list[EvenimentPersonal]:
    return list(
        s.scalars(
            select(EvenimentPersonal)
            .where(EvenimentPersonal.user_id == user.id)
            .order_by(EvenimentPersonal.creat_la)
        )
    )


def importatele_contului(s: Session, user: User) -> list[ActivitateImportata]:
    return list(
        s.scalars(
            select(ActivitateImportata)
            .where(ActivitateImportata.user_id == user.id)
            .order_by(ActivitateImportata.creat_la)
        )
    )
