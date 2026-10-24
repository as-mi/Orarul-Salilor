"""Sedintele unui tutoriat: in ce zile se tine, de fapt.

Un tutoriat se tine saptamanal, in aceeasi zi si la aceeasi ora, intre doua date. Dar nu in
fiecare saptamana calendaristica: nu in vacante, nu in sesiune, nu in zilele libere -- exact
zilele care nu sunt de activitate didactica in structura anului universitar (`weeks.py`).
Cu frecventa SI / SP, doar in saptamanile academice impare / pare.

Fara structura anului configurata nu avem de unde sti vacantele: atunci se tine in fiecare
saptamana, ca sa nu ascundem sedinte reale pe baza unei presupuneri.
"""

from __future__ import annotations

from datetime import date, timedelta

from orar.domain.grid import ZILE_SAPTAMANA
from orar.domain.weeks import CalendarAcademic

__all__ = ["sedinte", "zile_saptamanale"]


def zile_saptamanale(zi_saptamana: str, de_la: date, pana_la: date) -> list[date]:
    """Toate datele cu ziua saptamanii data, intre doua date (inclusiv)."""
    index = ZILE_SAPTAMANA.index(zi_saptamana)
    prima = de_la + timedelta(days=(index - de_la.weekday()) % 7)
    zile = []
    while prima <= pana_la:
        zile.append(prima)
        prima += timedelta(days=7)
    return zile


def _se_tine(zi: date, calendar: CalendarAcademic, frecventa: str | None) -> bool:
    if calendar.din_structura(zi):
        sapt = calendar.saptamana(zi)
        perioada = calendar.perioada(zi)
        if sapt is None or (perioada is not None and perioada.fel == "liber"):
            return False
    else:
        sapt = calendar.saptamana(zi)
    if frecventa:
        return sapt is not None and sapt.paritate.value == frecventa
    return True


def sedinte(
    zi_saptamana: str,
    de_la: date,
    pana_la: date,
    calendar: CalendarAcademic,
    frecventa: str | None = None,
) -> list[date]:
    """Zilele in care se tine un tutoriat: saptamanal, fara zilele fara activitate didactica."""
    return [
        z
        for z in zile_saptamanale(zi_saptamana, de_la, pana_la)
        if _se_tine(z, calendar, frecventa)
    ]
