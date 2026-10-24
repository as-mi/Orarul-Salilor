"""Planificatorul de tutoriate: acoperire, continuitate si Set Cover.

Corectitudine peste viteza: spatiul de cautare e mic (cateva zile x ore, k <= 3 sesiuni pe
materie), deci cautare exhaustiva peste submulțimi de sloturi. Lucreaza pe **agregatul**
"Orarul in cifre": `ocupare[(zi, ora)]` = cati oameni din publicul-ținta sunt ocupati in
ceasul respectiv (varful dintre saptamanile semestrului, ca in `db.statistici.calculeaza`).

Principii
---------
* **Acoperirea** unui slot e fractiunea din public libera pe tot intervalul; pentru un set
  de sesiuni e suma (plafonata la 100%) -- estimare agregata, nu o garantie per student.
* **"Fara insule"**: un slot e "lipit" daca ceasul de dinainte sau de dupa e un ceas de curs
  (peste `PRAG_BLOC` din public ocupat). Insulele se penalizeaza.
* **Indulgenta**: pragul minim de acoperire acceptat. O solutie "lipita" la `indulgenta`% e
  preferata uneia de 100% cu insule -- dar fara sa sacrificam prea multa acoperire.
* **Prioritatile** (materia dorita a fiecarui tutor) nu intra aici: tin de alocarea echipelor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from orar.domain.grid import ORA_MAX, ORA_MIN, ZILE

__all__ = [
    "PENALIZARE_INSULA",
    "PRAG_BLOC",
    "Propunere",
    "SetPropus",
    "Slot",
    "acoperire_slot",
    "lipit_si_curs",
    "planifica",
    "sloturi_candidate",
]

#: Ponderea unei "insule" in cost (in puncte de acoperire: 200 = 20 puncte procentuale).
#: Mai mare decat castigul tipic de acoperire dintre un slot lipit si unul liber, ca
#: "lipit la 85%" sa bata "insula la 100%".
PENALIZARE_INSULA = 200
#: Fractiune din public ocupata ca un ceas sa conteze drept "curs" (pentru lipire).
PRAG_BLOC = 0.5
#: Ponderea suprapunerii dintre sesiuni (ore identice in aceeasi zi => aceeasi cohorta).
PENALIZARE_SUPRAPUNERE = 3
#: Ponderea unei sesiuni in plus (preferam mai putine sesiuni, la egalitate).
PENALIZARE_SESIUNE = 1


@dataclass(frozen=True)
class Slot:
    """Un slot candidat: o zi si ora de inceput (durata e cea a campaniei)."""

    zi: str
    h: int


@dataclass
class Propunere:
    """O sesiune propusa, cu acoperirea si explicatia ei."""

    zi: str
    h: int
    durata: int
    acoperire: int  # % (0..100)
    liberi: int  # persoane libere in cel mai aglomerat ceas
    populatie: int
    lipit: bool
    curs: str | None  # numele cursului de care e lipit, sau None
    explicatie: str


@dataclass
class SetPropus:
    """Un set de sesiuni (1..k) pentru o materie, cu scorul lui."""

    propuneri: list[Propunere]
    acoperire: int  # estimarea combinata (suma plafonata la 100)
    nr_insule: int
    meets_indulgenta: bool
    cost: int  # mai mic = mai bun
    _cheie: tuple = field(default=(), repr=False, compare=False)

    @property
    def explicatii(self) -> list[str]:
        return [p.explicatie for p in self.propuneri]


def acoperire_slot(
    ocupare: dict[tuple[str, int], int], populatie: int, zi: str, h: int, durata: int
) -> tuple[int, int]:
    """(acoperire %, persoane libere) pentru slotul `[h, h+durata)`.

    Un student ocupat oricand in interval nu poate veni, deci numaram varful de ocupatie.
    """
    if populatie <= 0:
        return 0, 0
    ocupati = max(ocupare.get((zi, ora), 0) for ora in range(h, h + durata))
    liberi = max(0, populatie - ocupati)
    return round(100 * liberi / populatie), liberi


def lipit_si_curs(
    ocupare: dict[tuple[str, int], int],
    populatie: int,
    zi: str,
    h: int,
    durata: int,
    *,
    dominante: dict[tuple[str, int], str | None] | None = None,
    prag: float = PRAG_BLOC,
) -> tuple[bool, str | None]:
    """Slotul e lipit de un curs? Daca da, care curs (cel dominant din ceasul alaturat)."""
    prag_persoane = prag * populatie
    dominante = dominante or {}
    inainte, dupa = h - 1, h + durata
    if inainte >= ORA_MIN and ocupare.get((zi, inainte), 0) >= prag_persoane:
        return True, dominante.get((zi, inainte))
    if dupa < ORA_MAX and ocupare.get((zi, dupa), 0) >= prag_persoane:
        return True, dominante.get((zi, dupa))
    return False, None


def sloturi_candidate(
    ocupare: dict[tuple[str, int], int], durata: int, *, fezabile: set[tuple[str, int]] | None = None
) -> list[Slot]:
    """Toate sloturile `(zi, h)` din grila, ordonate zi-apoi-ora; doar cele fezabile daca se cere."""
    fezabile = fezabile if fezabile is not None else None
    sloturi = []
    for zi in ZILE:
        for h in range(ORA_MIN, ORA_MAX - durata + 1):
            if fezabile is not None and (zi, h) not in fezabile:
                continue
            sloturi.append(Slot(zi, h))
    return sloturi


def _cost(acoperire: int, nr_insule: int, nr_sesiuni: int, suprapunere: int) -> int:
    # 10 puncte de cost pe punct procentual de acoperire; insula costa 200; supra/sesiune putin.
    return -10 * acoperire + PENALIZARE_INSULA * nr_insule + PENALIZARE_SESIUNE * nr_sesiuni + PENALIZARE_SUPRAPUNERE * suprapunere


def _suprapunere(sloturi: list[Slot], durata: int) -> int:
    """Ore comune intre sesiuni in aceeasi zi (sesiuni redundante => aceeasi cohorta)."""
    total = 0
    for a, b in combinations(sloturi, 2):
        if a.zi != b.zi:
            continue
        start, stop = max(a.h, b.h), min(a.h + durata, b.h + durata)
        if stop > start:
            total += stop - start
    return total


def planifica(
    ocupare: dict[tuple[str, int], int],
    populatie: int,
    *,
    durata: int,
    max_sesiuni: int = 3,
    indulgenta: int = 75,
    dominante: dict[tuple[str, int], str | None] | None = None,
    fezabile: set[tuple[str, int]] | None = None,
    prag: float = PRAG_BLOC,
    top: int = 3,
) -> list[SetPropus]:
    """Cele mai bune seturi de sesiuni pentru o materie (top-K, cel mai bun primul).

    Cautare exhaustiva peste toate submulțimile de sloturi fezabile de marime 1..`max_sesiuni`.
    Intorce o lista goala cand nu exista niciun slot fezabil.
    """
    sloturi = sloturi_candidate(ocupare, durata, fezabile=fezabile)
    if not sloturi:
        return []

    def _propunere(s: Slot) -> Propunere:
        acoperire, liberi = acoperire_slot(ocupare, populatie, s.zi, s.h, durata)
        lipit, curs = lipit_si_curs(ocupare, populatie, s.zi, s.h, durata, dominante=dominante, prag=prag)
        if lipit:
            ce = f"lipit de {curs}" if curs else "lipit de curs"
            explicatie = f"{s.zi} {s.h:02d}:00 — {ce}, acoperă {acoperire}% din public"
        else:
            explicatie = f"{s.zi} {s.h:02d}:00 — {acoperire}% din public liberi"
        return Propunere(s.zi, s.h, durata, acoperire, liberi, populatie, lipit, curs, explicatie)

    seturi: list[SetPropus] = []
    limita = min(max_sesiuni, len(sloturi))
    for k in range(1, limita + 1):
        for combinatie in combinations(sloturi, k):
            propuneri = [_propunere(s) for s in combinatie]
            acoperire = min(100, sum(p.acoperire for p in propuneri))
            nr_insule = sum(0 if p.lipit else 1 for p in propuneri)
            suprapunere = _suprapunere(list(combinatie), durata)
            cost = _cost(acoperire, nr_insule, k, suprapunere)
            # Indulgenta intra ca un al doilea nivel: la acelasi cost, preferam sa atingem pragul.
            meets = acoperire >= indulgenta
            seturi.append(
                SetPropus(
                    propuneri=propuneri,
                    acoperire=acoperire,
                    nr_insule=nr_insule,
                    meets_indulgenta=meets,
                    cost=cost,
                    _cheie=(not meets, nr_insule, -acoperire, k, suprapunere, ZILE.index(combinatie[0].zi), combinatie[0].h),
                )
            )

    seturi.sort(key=lambda s: (s._cheie[0], s._cheie[1], s._cheie[2], s._cheie[3], s._cheie[4], s._cheie[5], s._cheie[6]))
    return seturi[:top]
