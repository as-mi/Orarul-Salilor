"""Structura anului universitar: ce perioade are un an si ce lipseste din ea.

Perioadele le pune un admin (`/admin/an-universitar`). Din cele de activitate didactica
se numara saptamanile academice (`domain/weeks.py`); celelalte -- vacante, sesiuni,
licenta -- apar in calendarul ASMI si in bara de sus, in locul numarului saptamanii.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from orar.db.models import AncoraSaptamana as AncoraDB
from orar.db.models import PerioadaStructura
from orar.domain.weeks import (
    FELURI_PERIOADA,
    AncoraSaptamana,
    CalendarAcademic,
    Paritate,
    an_universitar_al,
    ce_lipseste,
)

__all__ = ["alerta_an_curent", "calendar_pentru", "interval_an", "perioade_anului"]


def perioade_anului(s: Session, an: str) -> list[PerioadaStructura]:
    """Perioadele unui an universitar, in ordinea in care incep."""
    ordine = list(FELURI_PERIOADA)
    return sorted(
        s.scalars(select(PerioadaStructura).where(PerioadaStructura.an_univ == an)),
        key=lambda p: (p.data_inceput, ordine.index(p.fel)),
    )


def interval_an(an: str) -> tuple[date, date]:
    """Datele intre care pot cadea perioadele unui an universitar: din 1 septembrie pana in
    30 septembrie anul urmator (unii ani incep inainte de 1 octombrie)."""
    inceput = int(an.split("-")[0])
    return date(inceput, 9, 1), date(inceput + 1, 9, 30)


def calendar_pentru(s: Session, an_univ: str, semestru: int) -> CalendarAcademic:
    """Calendarul academic al unui singur an universitar si semestru.

    `worker/sync.py:calendar_din_baza` incarca toate structurile si toate ancorele deodata,
    iar `numeroteaza` grupeaza doar dupa semestru: doua generatii de structura s-ar amesteca.
    Abonamentul la calendar acopera un singur semestru, deci filtrul e strict.
    """
    perioade = [p.ca_perioada() for p in perioade_anului(s, an_univ)]
    ancore = [
        AncoraSaptamana(inceput=r.inceput, numar=r.numar, paritate=Paritate(r.paritate))
        for r in s.scalars(
            select(AncoraDB).where(
                AncoraDB.an_univ == an_univ, AncoraDB.semestru == semestru
            )
        )
    ]
    return CalendarAcademic(ancore=ancore, perioade=perioade)


def alerta_an_curent(s: Session, azi: date | None = None) -> tuple[str, list[str]] | None:
    """(anul, ce lipseste) cand structura anului universitar curent nu e completa, altfel
    None. Anul nou incepe, pentru alerta, pe 30 septembrie: de atunci trebuie configurat."""
    an = an_universitar_al(azi or date.today())
    lipsuri = ce_lipseste([p.ca_perioada() for p in perioade_anului(s, an)])
    return (an, lipsuri) if lipsuri else None
