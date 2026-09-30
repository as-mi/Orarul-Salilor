"""Validare incrucisata cu orarul profesorilor.

Doua surse independente pentru aceleasi ore
-------------------------------------------
FMI publica acelasi orar de doua ori: o data pivotat pe formatiune (98 de pagini, cate una
pe grupa) si o data pivotat pe profesor (191 de pagini, cate una pe om). Aceeasi activitate
apare deci in amandoua, scrisa altfel:

    orarul grupelor                     orarul profesorilor
    +---------------------------+       +---------------------------+
    | Alexe B            Gr_3   |       | AdvMachLearn (sem, SI)    |
    |   AdvMachLearn (sem, SI)  |       |        407/411/412        |
    |                    S-415  |       | Gr_3               S-415  |
    +---------------------------+       +---------------------------+
      pagina = "INFO Master 407"          pagina = "Alexe Bogdan"

De aici castigam doua lucruri pe care o singura sursa nu le poate da:

1. **Lista autoritativa de profesori.** Titlul fiecarei pagini e un nume intreg, scris mare
   si singur pe rand -- se citeste mult mai sigur decat abrevierea `Alexe B` dintr-o banda
   de 20 px. 191 de nume, fara sa le ghicim.
2. **Confirmare pe fiecare activitate.** Daca acelasi slot (zi, interval, sala, materie)
   apare in ambele surse, extragerea e coroborata de o a doua citire, independenta.
   Daca apare doar intr-una, merita privit.

Ce **nu** face
--------------
Nu suprascrie nimic de la sine. `verifica` raporteaza; `completeaza_profesorii` umple doar
campurile pe care extragerea insasi le-a marcat nesigure -- adica exact acolo unde am spus
ca nu stim, si unde a doua sursa chiar aduce informatie noua.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from orar.db.models import Ora, Profesor, SlotProfesor
from orar.domain.names import potriveste
from orar.domain.rooms import normalizeaza_sala
from orar.ingest.lexicon import Lexicon
from orar.ingest.ocr import MotorRecunoastere, citeste_fisier
from orar.ingest.segment import EroareSegmentare

log = logging.getLogger(__name__)

__all__ = [
    "IndexProfesori",
    "RaportCrosscheck",
    "citeste_orarul_profesorilor",
    "verifica",
    "completeaza_profesorii",
    "extinde_numele",
    "salveaza_index",
    "profesori_potriviti",
]

#: Cheia dupa care legam cele doua surse: ce se poate citi la fel din amandoua.
#: Profesorul si grupa lipsesc dinadins -- tocmai ele difera intre surse si le comparam.
Cheie = tuple[str, int, int, str, str]


def _cheie(zi: str, inceput: int, sfarsit: int, sala: str | None, materie: str | None) -> Cheie:
    return (
        zi,
        inceput,
        sfarsit,
        normalizeaza_sala(sala).slug if sala else "",
        (materie or "").strip().lower(),
    )


@dataclass
class IndexProfesori:
    """Ce am citit din orarul profesorilor, gata de interogat."""

    #: Numele intregi, din titlurile paginilor. Sursa autoritativa.
    nume: list[str] = field(default_factory=list)
    #: cheie de slot -> profesorii care apar acolo.
    pe_slot: dict[Cheie, set[str]] = field(default_factory=lambda: defaultdict(set))
    #: Formatiunile scrise in celula, pentru fiecare slot.
    formatiuni: dict[Cheie, set[str]] = field(default_factory=lambda: defaultdict(set))
    #: cheie de slot -> (profesor, frecventa, saptamani): o activitate impartita pe
    #: saptamani are acelasi slot, dar alt om in fiecare interval.
    detalii: dict[Cheie, set[tuple[str, str, str]]] = field(
        default_factory=lambda: defaultdict(set)
    )
    pagini: int = 0
    avertismente: list[str] = field(default_factory=list)

    def profesori(self, ora: Ora) -> set[str]:
        """Profesorii activitatii. Cand slotul e impartit pe saptamani, doar cei care tin
        exact saptamanile ei; daca nu se potriveste niciunul, toti cei din slot."""
        k = _cheie_ora(ora)
        toti = self.pe_slot.get(k, set())
        return _pe_saptamani(self.detalii.get(k, set()), ora.frecventa, ora.saptamani) or toti


def _pe_saptamani(
    detalii: set[tuple[str, str, str]], frecventa: str | None, saptamani: str | None
) -> set[str]:
    """Din profesorii unui slot, cei cu aceeasi frecventa si aceleasi saptamani. Multimea
    goala cand slotul nu e impartit (toti au aceleasi saptamani) sau nu se potriveste nimeni."""
    if len({(f, sp) for _, f, sp in detalii}) < 2:
        return set()
    cautat = (_norm(frecventa), _norm(saptamani))
    return {n for n, f, sp in detalii if (f, sp) == cautat}


def _norm(text: str | None) -> str:
    return "".join((text or "").lower().split())


def _cheie_ora(o: Ora) -> Cheie:
    return _cheie(
        o.zi_saptamana,
        o.ora_inceput.hour if isinstance(o.ora_inceput, time) else int(o.ora_inceput),
        o.ora_sfarsit.hour if isinstance(o.ora_sfarsit, time) else int(o.ora_sfarsit),
        o.sala.nume if o.sala else None,
        o.materie.nume if o.materie else None,
    )


def citeste_orarul_profesorilor(
    director: Path, motor: MotorRecunoastere, lexicon: Lexicon
) -> IndexProfesori:
    """Segmenteaza si citeste toate paginile din orarul profesorilor."""
    index = IndexProfesori()
    for cale in sorted(director.rglob("*.png")):
        try:
            pagina = citeste_fisier(cale, motor, lexicon)
        except EroareSegmentare:
            continue  # coperta / cuprins
        index.pagini += 1
        nume = pagina.titlu.strip()
        if not nume:
            index.avertismente.append(f"{cale.name}: pagina fara titlu")
            continue
        index.nume.append(nume)
        for a in pagina.activitati:
            inceput, sfarsit = _interval(a.ore)
            if inceput is None:
                continue
            k = _cheie(a.zi, inceput, sfarsit, a.sala, a.materie)
            index.pe_slot[k].add(nume)
            index.detalii[k].add((nume, _norm(a.frecventa), _norm(a.saptamani)))
            index.formatiuni[k].update(a.formatiuni)
    return index


def _interval(ore: str) -> tuple[int | None, int]:
    parti = ore.split("-")
    if len(parti) != 2:
        return None, 0
    try:
        return int(parti[0]), int(parti[1])
    except ValueError:
        return None, 0


@dataclass
class RaportCrosscheck:
    pagini_profesori: int = 0
    nume_gasite: int = 0
    #: Activitati din baza confirmate de a doua sursa (acelasi slot).
    confirmate: int = 0
    #: Confirmate, si cu acelasi profesor.
    profesor_confirmat: int = 0
    #: Slot gasit, dar profesorul din baza nu apare printre cei de acolo.
    divergente: list[str] = field(default_factory=list)
    #: Activitati care nu apar deloc in orarul profesorilor.
    neconfirmate: list[str] = field(default_factory=list)
    #: Profesori din orarul profesorilor care lipsesc din baza.
    nume_noi: list[str] = field(default_factory=list)

    @property
    def acoperire(self) -> float:
        total = self.confirmate + len(self.neconfirmate)
        return self.confirmate / total if total else 0.0

    def __str__(self) -> str:
        return "\n".join(
            [
                f"pagini de profesor citite : {self.pagini_profesori}",
                f"nume in orarul profesorilor: {self.nume_gasite}",
                f"activitati confirmate      : {self.confirmate} ({self.acoperire * 100:.1f}%)",
                f"  dintre care cu acelasi profesor: {self.profesor_confirmat}",
                f"divergente de profesor     : {len(self.divergente)}",
                f"neconfirmate               : {len(self.neconfirmate)}",
                f"nume necunoscute in baza   : {len(self.nume_noi)}",
            ]
        )


def verifica(s: Session, index: IndexProfesori) -> RaportCrosscheck:
    """Compara baza cu orarul profesorilor, fara sa modifice nimic."""
    rap = RaportCrosscheck(pagini_profesori=index.pagini, nume_gasite=len(set(index.nume)))

    # "Nou" inseamna ca niciun profesor din baza nu se potriveste cu numele intreg -- nu ca
    # sirurile difera: in baza scrie `Alexe B`, in titlu `Alexe Bogdan`.
    # Un rand `PROFESOR` poate tine mai multi oameni (`Baetica C / Mincu G`), asa cum sunt
    # scrisi in celula; ii despartim inainte de comparatie.
    cunoscuti = [
        parte.strip()
        for p in s.scalars(select(Profesor))
        for parte in (p.nume or "").split("/")
        if parte.strip()
    ]
    rap.nume_noi = sorted(
        n for n in set(index.nume) if not any(potriveste(c, [n]) for c in cunoscuti)
    )

    for o in s.scalars(select(Ora)):
        gasiti = index.profesori(o)
        if not gasiti:
            rap.neconfirmate.append(_descrie(o))
            continue
        rap.confirmate += 1
        al_nostru = o.profesor.nume if o.profesor else ""
        if _se_potriveste(al_nostru, gasiti):
            rap.profesor_confirmat += 1
        else:
            rap.divergente.append(f"{_descrie(o)}: avem {al_nostru!r}, acolo {sorted(gasiti)}")
    return rap


def _se_potriveste(al_nostru: str, gasiti: set[str]) -> bool:
    """Numele prescurtat din celula descrie vreunul dintre profesorii gasiti in a doua sursa?

    Regulile de prescurtare sunt in `domain/names.py` -- nu sunt cele evidente.
    """
    if not al_nostru:
        return False
    return any(potriveste(parte.strip(), gasiti) for parte in al_nostru.split("/"))


def _descrie(o: Ora) -> str:
    return (
        f"{o.grupa.nume if o.grupa else '?'} {o.zi_saptamana} "
        f"{str(o.ora_inceput)[:5]}-{str(o.ora_sfarsit)[:5]} "
        f"{o.materie.nume if o.materie else '?'} @ {o.sala.nume if o.sala else '?'}"
    )


def completeaza_profesorii(s: Session, index: IndexProfesori) -> list[str]:
    """Umple profesorii pe care extragerea i-a marcat nesigure, din a doua sursa.

    Atinge **numai** randurile cu `profesor` in `CAMPURI_NESIGURE`: acolo am spus deja ca nu
    stim, deci o citire independenta e strict mai buna decat ce aveam. Restul raman cum sunt,
    chiar daca difera -- o divergenta se raporteaza, nu se rezolva tacit in favoarea uneia
    dintre surse.

    Cand o activitate e tinuta de mai multi, ea apare pe pagina **fiecaruia**, deci slotul
    intoarce mai multe nume: le luam pe toate, in ordine alfabetica. Nu cerem sa fie exact
    cate am citit noi -- tocmai ca n-am putut citi e motivul pentru care suntem aici.
    """
    schimbate: list[str] = []
    for o in s.scalars(select(Ora).where(Ora.campuri_nesigure.contains("profesor"))):
        gasiti = index.profesori(o)
        if not gasiti:
            continue
        nume = " / ".join(sorted(gasiti))
        vechi = o.profesor.nume if o.profesor else ""
        o.profesor = _profesor(s, nume)
        o.campuri_nesigure = _fara(o.campuri_nesigure, "profesor")
        schimbate.append(f"{_descrie(o)}: {vechi!r} -> {nume!r}")
    s.flush()
    return schimbate


def extinde_numele(s: Session, index: IndexProfesori) -> tuple[list[str], list[str]]:
    """Inlocuieste prescurtarile din baza cu numele intregi din orarul profesorilor.

    `Alexe B` devine `Alexe Bogdan` -- numele scris mare in titlul paginii lui, unde se
    citeste mult mai sigur decat intr-o banda de 20 px. Interfata arata de aici incolo omul,
    nu abrevierea lui.

    Numai cand potrivirea e **fara alternativa**: daca `Popescu A` s-ar potrivi si cu Adrian,
    si cu Ana, prescurtarea chiar nu-i distinge, iar a alege una ar fi o inventie. Alea se
    intorc ca lista a doua, de raportat.
    """
    intregi = sorted(set(index.nume))
    extinse: list[str] = []
    ambigue: list[str] = []
    for p in list(s.scalars(select(Profesor))):
        parti = [x.strip() for x in (p.nume or "").split("/") if x.strip()]
        if not parti:
            continue
        rezolvate = []
        for parte in parti:
            gasit = potriveste(parte, intregi)
            if len(gasit) != 1:
                if len(gasit) > 1:
                    ambigue.append(f"{parte!r} -> {gasit}")
                rezolvate = []
                break
            rezolvate.append(gasit[0])
        if not rezolvate:
            continue
        nou = " / ".join(rezolvate)
        if nou == p.nume:
            continue
        # Daca numele intreg exista deja ca rand, mutam orele pe el si il stergem pe cel vechi.
        existent = s.scalar(select(Profesor).where(Profesor.nume == nou, Profesor.id != p.id))
        if existent is not None:
            for o in list(p.ore):
                o.profesor = existent
            s.delete(p)
        else:
            p.nume = nou
            p.slug = _slug_unic(s, nou, p.id)
        extinse.append(f"{' / '.join(parti)!r} -> {nou!r}")
    s.flush()
    return extinse, sorted(set(ambigue))


def _slug_unic(s: Session, nume: str, propriu: int) -> str:
    from orar.ingest.load import _slug

    baza = _slug(nume)
    candidat, n = baza, 2
    while s.scalar(select(Profesor).where(Profesor.slug == candidat, Profesor.id != propriu)):
        candidat, n = f"{baza}-{n}", n + 1
    return candidat


def _profesor(s: Session, nume: str) -> Profesor:
    from orar.ingest.load import _slug

    gasit = s.scalar(select(Profesor).where(Profesor.nume == nume))
    if gasit is None:
        gasit = Profesor(nume=nume, slug=_slug(nume))
        s.add(gasit)
        s.flush()
    return gasit


def _fara(campuri: str | None, camp: str) -> str | None:
    ramase = [c for c in (campuri or "").split(",") if c and c != camp]
    return ",".join(ramase) or None


# ---------------------------------------------------------------------------
# A doua sursa, pastrata in baza
# ---------------------------------------------------------------------------


def salveaza_index(s: Session, index: IndexProfesori, *, an_universitar: str, semestru: int) -> int:
    """Pastreaza orarul profesorilor: numele intregi devin lista din care se alege un
    profesor, iar activitatile lor raman pentru potrivirea din coada de verificare.

    Inlocuieste ce era pastrat pentru acelasi semestru. Intoarce numarul de profesori.
    """
    s.execute(
        delete(SlotProfesor).where(
            SlotProfesor.an_univ == an_universitar, SlotProfesor.semestru == semestru
        )
    )
    for (zi, inceput, sfarsit, sala, materie), oameni in index.detalii.items():
        for nume, frecventa, saptamani in oameni:
            s.add(
                SlotProfesor(
                    an_univ=an_universitar,
                    semestru=semestru,
                    profesor=nume,
                    zi=zi,
                    ora_inceput=inceput,
                    ora_sfarsit=sfarsit,
                    sala=sala,
                    materie=materie[:200],
                    frecventa=frecventa or None,
                    saptamani=saptamani or None,
                )
            )
    nume_unice = sorted(set(index.nume))
    from orar.ingest.load import _slug

    for nume in nume_unice:
        # acelasi om poate fi deja in baza cu alta scriere a numelui (o cratima, o litera
        # citita altfel) care da acelasi slug: e randul lui, nu unul nou
        p = s.scalar(select(Profesor).where(Profesor.nume == nume)) or s.scalar(
            select(Profesor).where(Profesor.slug == _slug(nume))
        )
        if p is None:
            p = Profesor(nume=nume, slug=_slug(nume))
            s.add(p)
        p.din_orar = True
        s.flush()
    s.flush()
    return len(nume_unice)


def profesori_potriviti(s: Session, ore: list[Ora]) -> dict[int, list[str]]:
    """Pentru fiecare activitate, profesorii pe care orarul profesorilor ii are in acelasi
    loc: aceeasi zi si acelasi interval, in aceeasi sala sau la aceeasi materie.

    Mai ingaduitor decat potrivirea de la ingest (care cere si sala, si materia): aici un om
    se uita la propunere si hotaraste. Intr-un slot impartit pe saptamani raman doar cei cu
    saptamanile activitatii.
    """
    if not ore:
        return {}
    perioade = {(o.perioada.an_univ, o.perioada.semestru) for o in ore}
    pe_moment: dict[tuple, list[SlotProfesor]] = defaultdict(list)
    for an, semestru in perioade:
        for slot in s.scalars(
            select(SlotProfesor).where(
                SlotProfesor.an_univ == an, SlotProfesor.semestru == semestru
            )
        ):
            pe_moment[an, semestru, slot.zi, slot.ora_inceput, slot.ora_sfarsit].append(slot)

    propuneri: dict[int, list[str]] = {}
    for o in ore:
        _, inceput, sfarsit, sala, materie = _cheie_ora(o)
        candidati = pe_moment.get(
            (o.perioada.an_univ, o.perioada.semestru, o.zi_saptamana, inceput, sfarsit), []
        )
        # intai potrivirea stricta (sala si materie); altfel macar una dintre ele
        gasiti = (
            [x for x in candidati if x.sala == sala and x.materie == materie]
            or [x for x in candidati if materie and x.materie == materie]
            or [x for x in candidati if sala and x.sala == sala]
        )
        if not gasiti:
            continue
        detalii = {(x.profesor, _norm(x.frecventa), _norm(x.saptamani)) for x in gasiti}
        nume = _pe_saptamani(detalii, o.frecventa, o.saptamani) or {x.profesor for x in gasiti}
        propuneri[o.id] = sorted(nume)
    return propuneri
