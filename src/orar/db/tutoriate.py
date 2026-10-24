"""Tutoriatele in baza: cine e tutor, ce sali sunt libere saptamanal, ce intervale se
potrivesc publicului si ce apare pe orarul cuiva.

Termeni
-------
* **Tutor**: un cont asignat de responsabil la o materie, pe o perioada (implicit 6 luni).
  Asignarile se aduna (mai multe materii) si se pot prelungi.
* **Tutoriat**: o sedinta saptamanala, intr-o zi, la o ora, intr-o sala, intre doua date,
  tinuta de o echipa de tutori, pentru un public (specializari, ani, serii).
* **Sedinta**: o zi anume in care se tine tutoriatul (vezi `domain/tutoriate.py`); se poate
  anula individual.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from orar.db.evenimente import cheie_an, sali_libere, saptamana_calendaristica
from orar.db.models import Grupa, Perioada, Sala, Tutor, Tutoriat, TutoriatTutor, User
from orar.domain.grid import ORA_MAX, ORA_MIN, ZILE, ZILE_SAPTAMANA, EvenimentInZi
from orar.domain.tutoriate import sedinte
from orar.domain.weeks import CalendarAcademic

__all__ = [
    "BlocTutoriat",
    "IntervalPropus",
    "SalaSaptamanala",
    "DURATA_ASIGNARE",
    "alese_din_cont",
    "conflicte_tutori",
    "leaga_invitatiile",
    "oferite_grupei",
    "ocupa_sala",
    "pentru_grupa",
    "pune_alese_in_cont",
    "sali_libere_saptamanal",
    "sedintele",
    "sugereaza_intervale",
    "tutori_activi",
    "tutoriate_in_grila",
    "tutoriatele_tutorului",
]

#: Cat tine implicit o asignare de tutor: un semestru, cu marja (6 luni).
DURATA_ASIGNARE = timedelta(days=183)


def _calendar(s: Session) -> CalendarAcademic:
    from orar.worker.sync import calendar_din_baza

    return calendar_din_baza(s) or CalendarAcademic()


def sedintele(s: Session, t: Tutoriat, calendar: CalendarAcademic | None = None) -> list[date]:
    """Zilele in care se tine tutoriatul (fara cele fara activitate didactica). Sedintele
    anulate raman in lista: pe orar apar ca anulate, nu dispar."""
    return sedinte(
        t.zi_saptamana, t.data_inceput, t.data_sfarsit, calendar or _calendar(s), t.frecventa
    )


def _anulate(t: Tutoriat) -> set[date]:
    return {a.data for a in t.anulari}


# ------------------------------------------------------------------ publicul


def _serie(g: Grupa) -> str | None:
    nod = g
    while nod is not None:
        if nod.tip == "serie":
            return nod.slug
        nod = nod.parinte
    return None


def pentru_grupa(t: Tutoriat, g: Grupa) -> bool:
    """Tutoriatul se adreseaza formatiunii? Fiecare criteriu conteaza doar daca e ales."""
    if t.an_univ != g.an_universitar:
        return False
    if t.coduri and g.specializare not in t.coduri:
        return False
    if t.ani_alesi and cheie_an(g) not in t.ani_alesi:
        return False
    return not t.serii_alese or _serie(g) in t.serii_alese


# ------------------------------------------------------------------ tutorii


def tutori_activi(
    s: Session, azi: date | None = None, *, materie: str | None = None
) -> list[Tutor]:
    """Asignarile valabile azi (cu cont), optional doar la o materie."""
    azi = azi or date.today()
    stmt = (
        select(Tutor)
        .options(joinedload(Tutor.user))
        .where(Tutor.user_id.is_not(None), Tutor.valabil_de_la <= azi, Tutor.valabil_pana_la >= azi)
        .order_by(Tutor.materie)
    )
    if materie:
        stmt = stmt.where(func.lower(Tutor.materie) == materie.strip().lower())
    return list(s.scalars(stmt))


def leaga_invitatiile(s: Session, user: User) -> int:
    """Asignarile facute pe emailul unui cont nou devin ale contului."""
    if not user.email:
        return 0
    invitatii = list(
        s.scalars(
            select(Tutor).where(
                Tutor.user_id.is_(None), func.lower(Tutor.email) == user.email.lower()
            )
        )
    )
    for t in invitatii:
        t.user = user
    return len(invitatii)


def tutoriatele_tutorului(s: Session, user: User) -> list[Tutoriat]:
    return list(
        s.scalars(
            select(Tutoriat)
            .join(TutoriatTutor, TutoriatTutor.tutoriat_id == Tutoriat.id)
            .where(TutoriatTutor.user_id == user.id, Tutoriat.stare != "incheiat")
            .order_by(Tutoriat.data_inceput)
        )
    )


def conflicte_tutori(
    s: Session,
    user_ids: list[int],
    zi: str,
    inceput: time,
    sfarsit: time,
    perioada: Perioada | None,
) -> list[str]:
    """Tutorii din echipa care au ei insisi ore in intervalul tutoriatului (din grupa lor)."""
    from orar.db.queries import ore_pentru_grupa

    mesaje = []
    for user in s.scalars(select(User).where(User.id.in_(user_ids or [-1]))):
        if user.grupa_id is None or perioada is None:
            continue
        for o in ore_pentru_grupa(
            s, user.grupa_id, perioada_id=perioada.id, include_optionale=False
        ):
            if o.zi_saptamana == zi and o.ora_inceput < sfarsit and inceput < o.ora_sfarsit:
                materie = o.materie.nume if o.materie else "o activitate"
                mesaje.append(
                    f"{user.nume} are {materie} ({o.ora_inceput:%H}-{o.ora_sfarsit:%H}) în grupa {user.grupa.nume}"
                )
    return mesaje


# ------------------------------------------------------------------ salile


def ocupa_sala(
    s: Session, zi: date, inceput: time, sfarsit: time, *, fara: int | None = None
) -> set[int]:
    """Salile ocupate de tutoriate in `zi`, intre `inceput` si `sfarsit` (ciornele inclusiv:
    cat timp se planifica, nu trebuie date de doua ori). Sedintele anulate elibereaza sala."""
    nume_zi = ZILE_SAPTAMANA[zi.weekday()]
    calendar = None
    ocupate = set()
    for t in s.scalars(
        select(Tutoriat)
        .options(selectinload(Tutoriat.anulari))
        .where(
            Tutoriat.zi_saptamana == nume_zi,
            Tutoriat.stare != "incheiat",
            Tutoriat.sala_id.is_not(None),
            Tutoriat.data_inceput <= zi,
            Tutoriat.data_sfarsit >= zi,
            Tutoriat.ora_inceput < sfarsit,
            Tutoriat.ora_sfarsit > inceput,
        )
    ):
        if t.id == fara or zi in _anulate(t):
            continue
        calendar = calendar or _calendar(s)
        if zi in sedintele(s, t, calendar):
            ocupate.add(t.sala_id)
    return ocupate


@dataclass
class SalaSaptamanala:
    sala: Sala
    #: La cate sedinte e libera, din cate.
    libera: int
    total: int
    #: Datele in care e ocupata.
    ocupata_in: list[date] = field(default_factory=list)

    @property
    def mereu(self) -> bool:
        return self.libera == self.total


def sali_libere_saptamanal(
    s: Session,
    zi: str,
    inceput: time,
    sfarsit: time,
    de_la: date,
    pana_la: date,
    *,
    frecventa: str | None = None,
    fara: int | None = None,
) -> tuple[list[SalaSaptamanala], list[date]]:
    """Salile, cu la cate sedinte sunt libere: intai cele libere la toate, apoi cele libere
    la cele mai multe; la egalitate, cea mai incapatoare. Intoarce si sedintele verificate."""
    from orar.db.orare import perioada_implicita

    calendar = _calendar(s)
    zile = sedinte(zi, de_la, pana_la, calendar, frecventa)
    orar = perioada_implicita(s)
    sali = {x.id: x for x in s.scalars(select(Sala).where(Sala.tip == "fizica"))}
    libere: dict[int, int] = dict.fromkeys(sali, 0)
    ocupata_in: dict[int, list[date]] = {k: [] for k in sali}
    for d in zile:
        libere_azi = {
            x.sala.id
            for x in sali_libere(
                s, d, inceput, sfarsit, perioada_id=orar.id if orar else None,
                saptamana=calendar.saptamana(d), fara_tutoriat=fara,
            )
        }  # fmt: skip
        for sala_id in sali:
            if sala_id in libere_azi:
                libere[sala_id] += 1
            else:
                ocupata_in[sala_id].append(d)
    rezultat = [SalaSaptamanala(sali[k], libere[k], len(zile), ocupata_in[k]) for k in sali]
    rezultat.sort(
        key=lambda x: (-x.libera, x.sala.nr_locuri is None, -(x.sala.nr_locuri or 0), x.sala.nume)
    )
    return rezultat, zile


@dataclass
class IntervalPropus:
    zi: str
    inceput: int
    sfarsit: int
    #: Cat din public e liber (in cel mai aglomerat ceas al intervalului), in procente.
    liberi: int
    persoane_libere: int
    sali: list[SalaSaptamanala]


def sugereaza_intervale(
    s: Session,
    perioada: Perioada,
    *,
    specializari: list[str],
    ani: list[str],
    serii: list[str],
    de_la: date,
    pana_la: date,
    durata: int = 2,
    cate: int = 8,
) -> tuple[list[IntervalPropus], int]:
    """Cele mai bune intervale pentru un tutoriat: cand e liber cel mai mare procent din
    public si exista o sala libera la toate sedintele. Intoarce si marimea publicului.

    Publicul liber se masoara cu "Orarul in cifre" (`db/statistici.py`), in varful dintre
    saptamanile semestrului: un interval bun nu are voie sa fie bun doar in unele saptamani.
    """
    from orar.db.statistici import Filtre, calculeaza

    st = calculeaza(s, perioada, Filtre(specializari=specializari, ani=ani, serii=serii))
    candidati = []
    for zi in ZILE:
        for h in range(ORA_MIN, ORA_MAX - durata + 1):
            ocupati = max(st.celule[(zi, ora)].persoane for ora in range(h, h + durata))
            liberi = st.populatie - ocupati
            procent = round(100 * liberi / st.populatie) if st.populatie else 0
            candidati.append((procent, liberi, zi, h))
    candidati.sort(key=lambda c: (-c[0], ZILE.index(c[2]), c[3]))

    propuse: list[IntervalPropus] = []
    for procent, liberi, zi, h in candidati[: cate * 3]:
        sali, _ = sali_libere_saptamanal(s, zi, time(h), time(h + durata), de_la, pana_la)
        bune = [x for x in sali if x.mereu][:5]
        if bune:
            propuse.append(IntervalPropus(zi, h, h + durata, procent, liberi, bune))
        if len(propuse) == cate:
            break
    return propuse, st.populatie


# ------------------------------------------------------------------ pe orar


@dataclass
class BlocTutoriat:
    """O sedinta de tutoriat, in forma in care o deseneaza grila (ca un eveniment)."""

    tutoriat: Tutoriat
    zi: date
    anulat: bool = False
    #: Contul care se uita e in echipa: "tii tu".
    al_meu: bool = False
    fel: str = "tutoriat"
    clasa_culoare: str = ""
    stil_culoare: str = ""

    @property
    def id(self) -> int:
        return self.tutoriat.id

    @property
    def titlu(self) -> str:
        return f"Tutoriat {self.tutoriat.materie}"

    @property
    def data_inceput(self) -> date:
        return self.zi

    @property
    def ora_inceput(self) -> time:
        return self.tutoriat.ora_inceput

    @property
    def ora_sfarsit(self) -> time:
        return self.tutoriat.ora_sfarsit

    @property
    def sala(self) -> Sala | None:
        return self.tutoriat.sala

    @property
    def loc(self) -> str | None:
        return self.tutoriat.loc


def tutoriate_in_grila(
    s: Session,
    zi: date,
    *,
    grupa: Grupa | None = None,
    sala: Sala | None = None,
    alese: set[int] | None = None,
    tutor: User | None = None,
) -> list[EvenimentInZi]:
    """Sedintele din saptamana lui `zi`, de pus in grila.

    Pe orarul unei **sali**: toate tutoriatele care o ocupa. Pe al unei **formatiuni**: cele
    publicate pentru publicul ei pe care le-a ales cine se uita (`alese`), plus cele pe care
    le tine el insusi (`tutor`) -- acelea apar mereu.
    """
    luni, duminica = saptamana_calendaristica(zi)
    stmt = (
        select(Tutoriat)
        .options(
            joinedload(Tutoriat.sala), selectinload(Tutoriat.anulari), selectinload(Tutoriat.echipa)
        )
        .where(
            Tutoriat.data_inceput <= duminica,
            Tutoriat.data_sfarsit >= luni,
            Tutoriat.stare != "incheiat",
        )
    )
    if sala is not None:
        stmt = stmt.where(Tutoriat.sala_id == sala.id)
    calendar = _calendar(s)
    blocuri = []
    for t in s.scalars(stmt).unique():
        al_meu = tutor is not None and any(m.user_id == tutor.id for m in t.echipa)
        if sala is None:
            ales = (
                t.stare == "publicat"
                and t.id in (alese or set())
                and grupa is not None
                and pentru_grupa(t, grupa)
            )
            if not (al_meu or ales):
                continue
        for d in sedintele(s, t, calendar):
            if luni <= d <= duminica:
                bloc = BlocTutoriat(t, d, anulat=d in _anulate(t), al_meu=al_meu)
                blocuri.append(EvenimentInZi(eveniment=bloc, zi=ZILE_SAPTAMANA[d.weekday()]))
    return blocuri


def oferite_grupei(s: Session, grupa: Grupa, azi: date | None = None) -> list[Tutoriat]:
    """Tutoriatele publicate, inca neterminate, pentru publicul unei formatiuni: ce se poate
    alege din dropdownul "Tutoriate" de pe pagina ei."""
    azi = azi or date.today()
    lista = s.scalars(
        select(Tutoriat)
        .options(
            joinedload(Tutoriat.sala), selectinload(Tutoriat.echipa).joinedload(TutoriatTutor.user)
        )
        .where(Tutoriat.stare == "publicat", Tutoriat.data_sfarsit >= azi)
        .order_by(Tutoriat.materie, Tutoriat.zi_saptamana, Tutoriat.ora_inceput)
    ).unique()
    return [t for t in lista if pentru_grupa(t, grupa)]


# ------------------------------------------------------------------ alegerile contului


def alese_din_cont(user: User | None) -> set[int]:
    if user is None or not user.tutoriate_alese:
        return set()
    try:
        date_ = json.loads(user.tutoriate_alese)
    except ValueError:
        return set()
    return {int(x) for x in date_ if isinstance(x, int)} if isinstance(date_, list) else set()


def pune_alese_in_cont(user: User, alese: set[int]) -> None:
    user.tutoriate_alese = json.dumps(sorted(alese)) if alese else None
