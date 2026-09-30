"""Evenimentele: activitati cu data, in afara orarului saptamanal.

Trei intrebari la care raspunde modulul:

* ce evenimente se vad pe orarul unei grupe sau al unei sali, in saptamana afisata;
* ce sali sunt libere intr-o zi, intre doua ore -- tinand cont si de orarul saptamanal, si
  de celelalte evenimente;
* ce e in calendarul ASMI intr-un interval.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from orar.db.models import Eveniment, Grupa, Ora, Sala
from orar.domain.grid import ZILE_SAPTAMANA, EvenimentInZi, se_tine
from orar.domain.hierarchy import este_master
from orar.domain.weeks import Saptamana

__all__ = [
    "SalaLibera",
    "cheie_an",
    "evenimente_asmi",
    "evenimente_in_grila",
    "saptamana_calendaristica",
    "sali_libere",
]


def saptamana_calendaristica(zi: date) -> tuple[date, date]:
    """Lunea si duminica saptamanii in care cade `zi`."""
    luni = zi - timedelta(days=zi.weekday())
    return luni, luni + timedelta(days=6)


def _in_interval(de_la: date, pana_la: date):  # noqa: ANN202
    return (Eveniment.data_inceput <= pana_la, Eveniment.data_sfarsit >= de_la)


def cheie_an(grupa: Grupa) -> str | None:
    """Anul de studiu al formatiunii, cu tot cu nivel: `L2` (licenta, anul 2), `M1` (master,
    anul 1). None cand formatiunea nu tine de un an anume (conferinte, master IFR)."""
    if grupa.an_studiu is None:
        return None
    nod, master = grupa, False
    while nod is not None and not master:
        master = este_master(nod.nume)
        nod = nod.parinte
    return f"{'M' if master else 'L'}{grupa.an_studiu}"


def _e_pentru(e: Eveniment, grupa: Grupa | None) -> bool:
    """Evenimentul cu vizibilitatea `specializari` apare pe orarul formatiunii? Trebuie sa
    se potriveasca si specializarea, si anul -- fiecare doar daca a fost ales ceva la ea."""
    if grupa is None:
        return False
    if e.coduri and grupa.specializare not in e.coduri:
        return False
    return not e.ani_alesi or cheie_an(grupa) in e.ani_alesi


def evenimente_in_grila(
    s: Session, zi: date, *, grupa: Grupa | None = None, sala: Sala | None = None
) -> list[EvenimentInZi]:
    """Evenimentele cu ore din saptamana lui `zi`, de pus in grila unei grupe sau a unei sali.

    Pe orarul unei **sali** apare tot ce o rezerva. Pe al unei **formatiuni** apar cele puse
    pe toate orarele si cele puse pe specializarea si anul ei; cele cu vizibilitatea `sala`
    nu apar.
    Un eveniment pe mai multe zile apare in fiecare zi a lui din saptamana.
    """
    luni, duminica = saptamana_calendaristica(zi)
    stmt = (
        select(Eveniment)
        .options(joinedload(Eveniment.sala))
        .where(*_in_interval(luni, duminica), Eveniment.ora_inceput.is_not(None))
        .order_by(Eveniment.data_inceput, Eveniment.ora_inceput)
    )
    if sala is not None:
        stmt = stmt.where(Eveniment.sala_id == sala.id)
    else:
        stmt = stmt.where(Eveniment.vizibilitate != "sala")

    in_zile: list[EvenimentInZi] = []
    for e in s.scalars(stmt):
        if sala is None and e.vizibilitate == "specializari" and not _e_pentru(e, grupa):
            continue
        for i, nume_zi in enumerate(ZILE_SAPTAMANA):
            if e.data_inceput <= luni + timedelta(days=i) <= e.data_sfarsit:
                in_zile.append(EvenimentInZi(eveniment=e, zi=nume_zi))
    return in_zile


@dataclass
class SalaLibera:
    sala: Sala
    #: Cate ore de curs are sala in ziua aceea (in total), ca sa se vada cat e de aglomerata.
    ocupate_in_zi: int = 0


def _se_suprapun(a1: time, a2: time, b1: time, b2: time) -> bool:
    return a1 < b2 and b1 < a2


def sali_libere(
    s: Session,
    zi: date,
    inceput: time,
    sfarsit: time,
    *,
    perioada_id: int | None,
    saptamana: Saptamana | None,
    fara_eveniment: int | None = None,
) -> list[SalaLibera]:
    """Salile facultatii libere in `zi` intre `inceput` si `sfarsit`, de la cea mai
    incapatoare la cea mai mica (cele cu numar de locuri necunoscut, la sfarsit).

    O sala e ocupata daca are o activitate din orarul saptamanal (`perioada_id`) in ziua
    aceea a saptamanii, care se tine in `saptamana` academica a datei, sau un alt eveniment.
    Fara saptamana academica (vacanta, date din afara semestrului) consideram ca orele se
    tin: mai bine o sala libera in minus decat doua activitati in aceeasi sala. Sambata si
    duminica orarul saptamanal n-are nimic, deci conteaza doar evenimentele.

    `fara_eveniment`: evenimentul care se editeaza -- sala lui nu e "ocupata" de el insusi.
    """
    nume_zi = ZILE_SAPTAMANA[zi.weekday()]
    ocupate: set[int] = set()
    in_zi: dict[int, int] = {}
    if perioada_id is not None:
        for o in s.scalars(
            select(Ora).where(
                Ora.perioada_id == perioada_id,
                Ora.zi_saptamana == nume_zi,
                Ora.sala_id.is_not(None),
            )
        ):
            if not se_tine(o, saptamana):
                continue
            in_zi[o.sala_id] = in_zi.get(o.sala_id, 0) + (o.ora_sfarsit.hour - o.ora_inceput.hour)
            if _se_suprapun(o.ora_inceput, o.ora_sfarsit, inceput, sfarsit):
                ocupate.add(o.sala_id)

    for e in s.scalars(
        select(Eveniment).where(*_in_interval(zi, zi), Eveniment.sala_id.is_not(None))
    ):
        if e.id == fara_eveniment:
            continue
        # fara ore = toata ziua
        if e.ora_inceput is None or _se_suprapun(e.ora_inceput, e.ora_sfarsit, inceput, sfarsit):
            ocupate.add(e.sala_id)

    sali = s.scalars(select(Sala).where(Sala.tip == "fizica", Sala.id.not_in(ocupate or {-1})))
    return [
        SalaLibera(x, in_zi.get(x.id, 0))
        for x in sorted(sali, key=lambda x: (x.nr_locuri is None, -(x.nr_locuri or 0), x.nume))
    ]


def evenimente_asmi(s: Session, de_la: date, pana_la: date) -> list[Eveniment]:
    """Evenimentele ASMI care ating intervalul, in ordinea in care incep."""
    return list(
        s.scalars(
            select(Eveniment)
            .options(joinedload(Eveniment.sala))
            .where(Eveniment.fel == "asmi", *_in_interval(de_la, pana_la))
            .order_by(Eveniment.data_inceput, Eveniment.ora_inceput.nulls_first(), Eveniment.id)
        )
    )
