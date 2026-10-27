"""Continutul feed-ului ICS: orarul unui semestru, ca ocurente cu data.

Grila paginii e pe o saptamana; aici se enumera zilele semestrului si fiecare activitate
devine un eveniment separat, cu UID stabil. Paritatea, intervalele de saptamani, vacantele
si zilele libere se rezolva o singura data, aici, ca clientul sa primeasca date concrete.

Feed-ul nu contine niciodata evenimente personale sau activitati importate: doar orarul
saptamanal, tutoriatele alese si evenimentele cu data vizibile pe pagina formatiunii.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from orar.db.abonamente import PreferinteAbonament
from orar.db.evenimente import evenimente_in_interval
from orar.db.models import Eveniment, Grupa, Ora, Perioada, Tutoriat, TutoriatTutor, User
from orar.db.queries import ore_pentru_grupa, ore_pentru_profesor
from orar.db.structura import calendar_pentru
from orar.db.tutoriate import pentru_grupa, sedintele, tutoriatele_tutorului
from orar.domain.grid import ZILE_SAPTAMANA, se_tine
from orar.domain.weeks import (
    CalendarAcademic,
    Paritate,
    Saptamana,
    parse_interval_saptamani,
)
from orar.web.afisare import e_ascunsa
from orar.web.ics import FUS, IntrareCalendar

__all__ = [
    "ZiDidactica",
    "intrari_grupa",
    "intrari_profesor",
    "zile_semestru",
]

#: Perioadele in care nu se tin ore, indiferent de ziua saptamanii.
NEDIDACTICE = frozenset({"liber", "vacanta", "sesiune", "restante", "licenta"})
#: Cate saptamani aproximam cand structura anului nu e configurata.
_SAPTAMANI_APROXIMATIVE = 14


@dataclass(frozen=True)
class ZiDidactica:
    """O zi lucratoare de activitate didactica, cu saptamana ei academica (daca se stie)."""

    zi: date
    saptamana: Saptamana | None


def _e_nedidactica(cal: CalendarAcademic, zi: date) -> bool:
    """Ziua cade intr-o vacanta / sesiune / zi libera a majoritatii anilor?

    Perioadele doar pentru anii terminali nu inchid ziua pentru ceilalti: altfel o vacanta
    de licenta ar goli feed-ul tuturor grupelor.
    """
    return any(p.generala and p.fel in NEDIDACTICE and p.contine(zi) for p in cal.perioade)


def _zile_didactice(cal: CalendarAcademic, semestru: int) -> list[ZiDidactica]:
    zile: dict[date, ZiDidactica] = {}
    for p in cal.perioade:
        if p.fel != "didactica" or p.semestru != semestru:
            continue
        zi = p.inceput
        while zi <= p.sfarsit:
            if zi.weekday() < 5 and not _e_nedidactica(cal, zi) and zi not in zile:
                saptamana = cal.saptamana(zi)
                if saptamana is None and p.saptamana is not None:
                    saptamana = Saptamana(
                        p.saptamana, Paritate.din_numar(p.saptamana), exact=True
                    )
                zile[zi] = ZiDidactica(zi, saptamana)
            zi += timedelta(days=1)
    return [zile[z] for z in sorted(zile)]


def _zile_aproximative(an_univ: str, semestru: int) -> list[ZiDidactica]:
    """14 saptamani luni-vineri, cand structura anului nu e configurata deloc."""
    an = int(an_univ.split("-")[0])
    inceput = date(an, 10, 1) if semestru == 1 else date(an + 1, 2, 17)
    inceput += timedelta(days=(7 - inceput.weekday()) % 7)  # prima luni
    return [
        ZiDidactica(
            inceput + timedelta(weeks=saptamana - 1, days=zi),
            Saptamana(saptamana, Paritate.din_numar(saptamana), exact=False),
        )
        for saptamana in range(1, _SAPTAMANI_APROXIMATIVE + 1)
        for zi in range(5)
    ]


def zile_semestru(
    s: Session, an_univ: str, semestru: int
) -> tuple[list[ZiDidactica], CalendarAcademic]:
    """Zilele de activitate didactica si calendarul academic al semestrului."""
    cal = calendar_pentru(s, an_univ, semestru)
    zile = _zile_didactice(cal, semestru)
    if not zile:
        zile = _zile_aproximative(an_univ, semestru)
    return zile, cal


def _interval_semestru(
    cal: CalendarAcademic, semestru: int, zile: list[ZiDidactica]
) -> tuple[date, date] | None:
    """De la prima la ultima zi a semestrului, intins pe vacantele si sesiunile lipite de el.

    Evenimentele cu data din vacantele dintre cursuri raman in feed: sunt evenimente reale.
    """
    if not zile:
        return None
    de_la, pana_la = zile[0].zi, zile[-1].zi
    for p in cal.perioade:
        if p.semestru not in (None, semestru):
            continue
        if p.inceput <= pana_la and p.sfarsit >= de_la:
            de_la, pana_la = min(de_la, p.inceput), max(pana_la, p.sfarsit)
    return de_la, pana_la


def _perioada(s: Session, an_univ: str, semestru: int) -> Perioada | None:
    return s.scalar(
        select(Perioada).where(Perioada.an_univ == an_univ, Perioada.semestru == semestru)
    )


def _se_tine_in_zi(o: Ora, zi: ZiDidactica) -> bool:
    if zi.saptamana is not None:
        return se_tine(o, zi.saptamana)
    # Fara numar academic nu putem evalua paritatea sau intervalul: includem doar
    # activitatile care nu depind de ele.
    if o.frecventa:
        return False
    return not o.saptamani or parse_interval_saptamani(o.saptamani) is None


def _titlu_ora(o: Ora) -> str:
    materie = o.materie.nume if o.materie else "Activitate"
    return f"{materie} ({o.tip_ora_materie})" if o.tip_ora_materie else materie


def _descriere_ora(o: Ora, grupa_pagina: Grupa | None) -> str:
    parti: list[str] = []
    if o.profesor:
        parti.append(o.profesor.nume)
    if grupa_pagina is None or o.grupa.nume != grupa_pagina.nume:
        parti.append(o.grupa.nume)
    if o.semigrupa:
        parti.append(o.semigrupa.replace("Gr_", "Grupa "))
    if o.frecventa == "SI":
        parti.append("doar săptămânile impare")
    elif o.frecventa == "SP":
        parti.append("doar săptămânile pare")
    if o.saptamani:
        parti.append(o.saptamani)
    return " · ".join(parti)


def _intrari_ore(
    ore: list[Ora],
    zile: list[ZiDidactica],
    *,
    domeniu: str,
    grupa_pagina: Grupa | None,
    preferinte: PreferinteAbonament | None,
) -> list[IntrareCalendar]:
    rezultat: list[IntrareCalendar] = []
    for zi in zile:
        nume_zi = ZILE_SAPTAMANA[zi.zi.weekday()]
        for o in ore:
            if o.zi_saptamana != nume_zi or not _se_tine_in_zi(o, zi):
                continue
            if preferinte is not None:
                semigrupa = preferinte.semigrupa
                if semigrupa and not (
                    o.grupa.tip == "optional" or o.semigrupa in (None, semigrupa)
                ):
                    continue
                if e_ascunsa(o, preferinte.ascunse):
                    continue
            rezultat.append(
                IntrareCalendar(
                    uid=f"ora-{o.id}-{zi.zi:%Y%m%d}@{domeniu}",
                    inceput=datetime.combine(zi.zi, o.ora_inceput, tzinfo=FUS),
                    sfarsit=datetime.combine(zi.zi, o.ora_sfarsit, tzinfo=FUS),
                    titlu=_titlu_ora(o),
                    loc=o.sala.nume if o.sala else "",
                    descriere=_descriere_ora(o, grupa_pagina),
                )
            )
    return rezultat


def _intrari_evenimente(
    s: Session,
    *,
    de_la: date,
    pana_la: date,
    grupa: Grupa | None,
    domeniu: str,
) -> list[IntrareCalendar]:
    rezultat: list[IntrareCalendar] = []
    for e in evenimente_in_interval(s, de_la, pana_la, grupa=grupa):
        zi = max(e.data_inceput, de_la)
        sfarsit_zi = min(e.data_sfarsit, pana_la)
        while zi <= sfarsit_zi:
            rezultat.append(_intrare_eveniment(e, zi, domeniu))
            zi += timedelta(days=1)
    return rezultat


def _intrare_eveniment(e: Eveniment, zi: date, domeniu: str) -> IntrareCalendar:
    ingrediente = [x for x in (e.descriere, e.link) if x]
    descriere = "\n".join(ingrediente)
    loc = e.sala.nume if e.sala else (e.loc or "")
    uid = f"eveniment-{e.id}-{zi:%Y%m%d}@{domeniu}"
    if e.ora_inceput is None:
        return IntrareCalendar(
            uid=uid,
            inceput=zi,
            sfarsit=zi,
            titlu=e.titlu,
            loc=loc,
            descriere=descriere,
            toata_ziua=True,
        )
    inceput = datetime.combine(zi, e.ora_inceput, tzinfo=FUS)
    if e.ora_sfarsit is not None and e.ora_sfarsit > e.ora_inceput:
        sfarsit = datetime.combine(zi, e.ora_sfarsit, tzinfo=FUS)
    else:
        sfarsit = inceput + timedelta(hours=1)
    return IntrareCalendar(
        uid=uid, inceput=inceput, sfarsit=sfarsit, titlu=e.titlu, loc=loc, descriere=descriere
    )


def _tutoriate_alese(s: Session, tutor: User | None, ids: frozenset[int]) -> list[int]:
    """ID-urile tutoriatele de emis: cele alese plus cele tinute de contul care se uita."""
    alese = set(ids)
    if tutor is not None:
        alese |= {t.id for t in tutoriatele_tutorului(s, tutor)}
    return sorted(alese)


def _incarca_tutoriate(
    s: Session, ids: list[int], *, an_univ: str, semestru: int, grupa: Grupa | None
) -> list[Tutoriat]:
    if not ids:
        return []
    lista = s.scalars(
        select(Tutoriat)
        .options(
            joinedload(Tutoriat.sala),
            selectinload(Tutoriat.echipa).joinedload(TutoriatTutor.user),
            selectinload(Tutoriat.anulari),
        )
        .where(Tutoriat.id.in_(ids), Tutoriat.stare == "publicat")
        .order_by(Tutoriat.materie, Tutoriat.zi_saptamana, Tutoriat.ora_inceput)
    ).unique()
    return [
        t
        for t in lista
        if t.an_univ == an_univ
        and t.semestru == semestru
        and (grupa is None or pentru_grupa(t, grupa))
    ]


def _intrari_tutoriate(
    s: Session, lista: list[Tutoriat], cal: CalendarAcademic, *, domeniu: str
) -> list[IntrareCalendar]:
    rezultat: list[IntrareCalendar] = []
    for t in lista:
        anulate = {a.data: a for a in t.anulari}
        tutori = [tt.user.nume for tt in t.echipa if tt.user is not None]
        parti = list(tutori)
        if t.descriere:
            parti.append(t.descriere)
        if t.link_teams:
            parti.append(t.link_teams)
        if t.link_whatsapp:
            parti.append(t.link_whatsapp)
        descriere = "\n".join(parti)
        loc = t.sala.nume if t.sala else (t.loc or "")
        for zi in sedintele(s, t, cal):
            anulare = anulate.get(zi)
            descriere_zi = descriere
            if anulare is not None and anulare.motiv:
                descriere_zi = f"Anulat: {anulare.motiv}\n{descriere}".strip()
            rezultat.append(
                IntrareCalendar(
                    uid=f"tutoriat-{t.id}-{zi:%Y%m%d}@{domeniu}",
                    inceput=datetime.combine(zi, t.ora_inceput, tzinfo=FUS),
                    sfarsit=datetime.combine(zi, t.ora_sfarsit, tzinfo=FUS),
                    titlu=f"Tutoriat: {t.materie}",
                    loc=loc,
                    descriere=descriere_zi,
                    status="CANCELLED" if anulare is not None else "CONFIRMED",
                )
            )
    return rezultat


def _sorteaza(intrari: list[IntrareCalendar]) -> list[IntrareCalendar]:
    def cheie(i: IntrareCalendar) -> tuple[datetime, str]:
        if isinstance(i.inceput, datetime):
            moment = i.inceput
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=FUS)
            return moment, i.uid
        return datetime.combine(i.inceput, time.min, tzinfo=FUS), i.uid

    return sorted(intrari, key=cheie)


def intrari_grupa(
    s: Session,
    grupa: Grupa,
    *,
    an_univ: str,
    semestru: int,
    preferinte: PreferinteAbonament,
    domeniu: str,
    tutor: User | None = None,
) -> list[IntrareCalendar]:
    """Tot feed-ul unei formatiuni: orele, tutoriatele alese si evenimentele cu data."""
    zile, cal = zile_semestru(s, an_univ, semestru)
    perioada = _perioada(s, an_univ, semestru)
    ore = ore_pentru_grupa(
        s, grupa.id, perioada_id=perioada.id if perioada is not None else -1
    )
    intrari = _intrari_ore(
        ore, zile, domeniu=domeniu, grupa_pagina=grupa, preferinte=preferinte
    )
    lista = _incarca_tutoriate(
        s,
        _tutoriate_alese(s, tutor, preferinte.tutoriate),
        an_univ=an_univ,
        semestru=semestru,
        grupa=grupa,
    )
    intrari += _intrari_tutoriate(s, lista, cal, domeniu=domeniu)
    interval = _interval_semestru(cal, semestru, zile)
    if interval is not None:
        intrari += _intrari_evenimente(
            s, de_la=interval[0], pana_la=interval[1], grupa=grupa, domeniu=domeniu
        )
    return _sorteaza(intrari)


def intrari_profesor(
    s: Session,
    nume: str,
    *,
    an_univ: str,
    semestru: int,
    domeniu: str,
    tutor: User | None = None,
) -> list[IntrareCalendar]:
    """Feed-ul unui profesor: orele lui, tutoriatele pe care le tine, evenimentele publice."""
    zile, cal = zile_semestru(s, an_univ, semestru)
    perioada = _perioada(s, an_univ, semestru)
    ore = ore_pentru_profesor(
        s, nume, perioada_id=perioada.id if perioada is not None else -1
    )
    intrari = _intrari_ore(
        ore, zile, domeniu=domeniu, grupa_pagina=None, preferinte=None
    )
    if tutor is not None:
        lista = _incarca_tutoriate(
            s,
            [t.id for t in tutoriatele_tutorului(s, tutor)],
            an_univ=an_univ,
            semestru=semestru,
            grupa=None,
        )
        intrari += _intrari_tutoriate(s, lista, cal, domeniu=domeniu)
    interval = _interval_semestru(cal, semestru, zile)
    if interval is not None:
        intrari += _intrari_evenimente(
            s, de_la=interval[0], pana_la=interval[1], grupa=None, domeniu=domeniu
        )
    return _sorteaza(intrari)
