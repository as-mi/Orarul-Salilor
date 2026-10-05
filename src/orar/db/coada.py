"""Gruparea cozii de verificare: aceeasi activitate, citita de pe mai multe pagini, se
verifica o singura data.

De ce apar dubluri
------------------
Orarul FMI repeta acelasi curs pe pagina fiecarei grupe din serie, iar acelasi optional pe
pagina fiecarui pachet care il ofera (anul 3 Mate-Info, anul 3 Info, anul 4 CTI). La
incarcare, consolidarea le uneste intr-o singura activitate -- dar numai cand textul citit e
identic. O litera citita altfel pe una din pagini (`SimblA` / `SimbIA`, `l` in loc de `I`)
le lasa separate, si fiecare ajunge in coada.

Cum decidem ca doua citiri sunt aceeasi activitate
--------------------------------------------------
Greselile de grupare sunt mai rele decat dublurile: o confirmare de grup ar scrie aceeasi
materie peste doua activitati diferite. Asa ca regulile sunt stricte, si fiecare are un motiv:

1. **Aceeasi zi, acelasi interval si aceeasi sala fizica.** O sala nu tine doua activitati
   deodata -- decat in saptamani diferite, de aceea si:
2. **Acelasi tip, aceeasi frecventa, aceleasi saptamani, aceeasi semigrupa.** Sunt campuri
   citite aproape fara greseala (peste 99.7%); daca difera, sunt chiar activitati diferite
   (doua laboratoare care alterneaza, doi profesori care isi impart semestrul).
3. **Ce e recunoscut sigur trebuie sa fie la fel.** Daca ambele citiri au materia (sau
   profesorul) confirmata de vocabular si ele difera altfel decat printr-o confuzie de OCR,
   nu e aceeasi activitate.
4. **Textul seamana.** Materia si profesorul, normalizate (litere mici, confuziile de OCR
   `l`/`I`, `0`/`O`), au o asemanare de cel putin 85%; cand ambele au materie, si materiile
   singure trebuie sa semene (cel putin 80%). Masurat pe orarul real: aceeasi activitate
   iese 93-100, activitati diferite in aceeasi sala si ora cel mult 67.

Si doua reguli prinse pe orarul real: doua celule de pe **aceeasi pagina** nu sunt niciodata
aceeasi activitate (sunt benzi diferite, de obicei semigrupe), iar paritatea ramasa in text
(`(sem, SI)` fata de `(sem, SP)`) le desparte chiar daca restul e identic.

Ce nu se potriveste dupa aceste reguli ramane separat, dar se arata langa ea, ca indiciu:
"in aceeasi sala si ora mai e ..." -- omul hotaraste.

Citirile sigure (care nu sunt in coada) ale aceleiasi activitati nu se modifica la
confirmare; servesc ca **referinta**: propunerea grupului se ia de la ele, fiindca au fost
recunoscute de vocabular.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from orar.db.models import Ora

__all__ = ["Dosar", "grupeaza", "aceeasi_activitate"]

#: Asemanarea minima a textului (materie + profesor) a doua citiri ale aceleiasi activitati.
PRAG_TEXT = 85
#: Asemanarea minima a materiilor, cand ambele citiri au materie.
PRAG_MATERIE = 80
#: Sali fara loc anume: in ele pot fi oricate activitati deodata, deci nu leaga nimic.
_FARA_SALA = {"nespecificat", "online", ""}

#: `lntegr`, `SimblA`: un `l` mic acolo unde cuvantul cere un `I` mare -- semn de citire gresita.
_RE_L_IN_LOC_DE_I = re.compile(r"(?<=[a-z&])l(?=[A-Z])|(?<=[&\s])l(?=[a-z]{2})|l(?=A\b)")

_CONFUZII = str.maketrans({"l": "i", "1": "i", "|": "i", "0": "o"})


def _norm(text: str | None) -> str:
    text = (text or "").lower().translate(_CONFUZII)
    return " ".join(re.findall(r"[a-z0-9&+]+", text))


def _tip(o: Ora) -> str:
    return _norm(o.tip_ora_materie).replace(" ", "")


def _sigur(o: Ora, camp: str) -> bool:
    """Campul a fost recunoscut de vocabular (nu e marcat nesigur) si nu e gol."""
    valoare = getattr(o, camp)
    return valoare is not None and camp not in (o.campuri_nesigure or "")


def _cheie_slot(o: Ora) -> tuple | None:
    """Ce trebuie sa fie identic ca doua citiri sa poata fi aceeasi activitate (regulile 1-2).
    None pentru activitatile fara sala fizica: acolo nu putem lega nimic sigur."""
    if o.sala is None or o.sala.nume.lower() in _FARA_SALA or o.sala.tip != "fizica":
        return None
    return (
        o.perioada_id,
        o.zi_saptamana,
        o.ora_inceput,
        o.ora_sfarsit,
        o.sala_id,
        _tip(o),
        o.frecventa or "",
        _norm(o.saptamani),
        o.semigrupa or "",
    )


_RE_MARCAJ = re.compile(r"\b(si|sp)\b|sapt\.?\s*[\d\s,+\-]+", re.IGNORECASE)


def _marcaje(o: Ora) -> set[str]:
    """Saptamanile scrise in text, cand nu au ajuns in campurile lor: `(sem, SI)` ramas in
    materie. Doua citiri cu marcaje diferite se tin in saptamani diferite."""
    text = f"{o.materie.nume if o.materie else ''} {o.profesor.nume if o.profesor else ''}"
    return {re.sub(r"\s+", "", m.group(0).lower()) for m in _RE_MARCAJ.finditer(text)}


def aceeasi_activitate(a: Ora, b: Ora) -> bool:
    """Doua citiri din acelasi slot (vezi `_cheie_slot`) sunt aceeasi activitate? Regulile 3-4,
    plus una prinsa pe orarul real: `Manag&DezvCariera (sem, SI)` si `(sem, SP)` au textul
    aproape identic, dar paritatea, ramasa in materie, le face activitati diferite."""
    # Pe aceeasi pagina, doua celule din acelasi interval sunt mereu activitati diferite
    # (doua semigrupe, doua benzi): dublurile vin din pagini diferite, nu din aceeasi.
    if a.sursa_pagina and a.sursa_pagina == b.sursa_pagina:
        return False
    if _marcaje(a) != _marcaje(b):
        return False
    # Ce a recunoscut vocabularul si difera desparte -- afara de cazul in care diferenta e
    # doar o confuzie de OCR (`SimblA` / `SimbIA`): vocabularul e facut din orarele citite,
    # deci poate tine ambele scrieri ca termeni "cunoscuti".
    for camp in ("materie", "profesor"):
        if (
            _sigur(a, camp)
            and _sigur(b, camp)
            and getattr(a, camp).id != getattr(b, camp).id
            and _norm(getattr(a, camp).nume) != _norm(getattr(b, camp).nume)
        ):
            return False
    materie_a = _norm(a.materie.nume if a.materie else "")
    materie_b = _norm(b.materie.nume if b.materie else "")
    if materie_a and materie_b and fuzz.ratio(materie_a, materie_b) < PRAG_MATERIE:
        return False
    text_a = f"{materie_a} {_norm(a.profesor.nume if a.profesor else '')}".strip()
    text_b = f"{materie_b} {_norm(b.profesor.nume if b.profesor else '')}".strip()
    if not text_a or not text_b:
        return False
    return fuzz.token_set_ratio(text_a, text_b) >= PRAG_TEXT


@dataclass
class Dosar:
    """O activitate de verificat: una sau mai multe citiri ale ei, de pe pagini diferite."""

    #: Citirile din coada, care se confirma impreuna.
    membri: list[Ora]
    #: Citiri sigure ale aceleiasi activitati, de pe alte pagini: nu se modifica, doar ajuta.
    referinte: list[Ora] = field(default_factory=list)
    #: Alte activitati din aceeasi sala si ora, care NU par aceeasi activitate: indicii.
    vecini: list[Ora] = field(default_factory=list)

    @property
    def principal(self) -> Ora:
        return self.membri[0]

    @property
    def id(self) -> str:
        return "-".join(str(o.id) for o in self.membri)

    def propunere(self) -> dict[str, str]:
        """Valorile cu care porneste formularul: de la o citire sigura, daca exista; altfel,
        pe fiecare camp, valoarea recunoscuta cea mai frecventa, apoi cea mai frecventa."""
        toate = [*self.referinte, *self.membri]

        def alege(camp: str) -> str:
            sigure = Counter(getattr(o, camp).nume for o in toate if _sigur(o, camp))
            oricare = Counter(getattr(o, camp).nume for o in toate if getattr(o, camp) is not None)
            if not oricare:
                return ""
            # fara confuzia `l` in loc de `I` > recunoscuta de vocabular > mai des citita aici.
            # Confuzia vine prima: vocabularul e facut din orarele citite, deci poate sti si
            # scrierea gresita ca termen "sigur" (`SimblA` langa `SimbIA`).
            return max(
                oricare,
                key=lambda v: (-len(_RE_L_IN_LOC_DE_I.findall(v)), sigure[v], oricare[v]),
            )

        return {
            "materie": alege("materie"),
            "profesor": alege("profesor"),
            "sala": self.principal.sala.nume if self.principal.sala else "",
            "saptamani": self.principal.saptamani or "",
        }

    @property
    def unde(self) -> list[str]:
        """Pentru cine apare activitatea: formatiunile tuturor citirilor."""
        return sorted({o.grupa.nume for o in [*self.membri, *self.referinte]})


def grupeaza(s: Session, ore: list[Ora]) -> list[Dosar]:
    """Impartirea cozii in dosare. `ore` sunt activitatile de verificat (in ordinea in care
    se afiseaza); dosarele le pastreaza ordinea primului membru."""
    in_coada = {o.id for o in ore}
    # toate activitatile din sloturile atinse: si cele sigure, ca referinte si vecini
    sloturi: dict[tuple, list[Ora]] = {}
    perioade = {o.perioada_id for o in ore}
    if perioade:
        for o in s.scalars(
            select(Ora)
            .options(
                joinedload(Ora.materie),
                joinedload(Ora.profesor),
                joinedload(Ora.sala),
                joinedload(Ora.grupa),
            )
            .where(Ora.perioada_id.in_(perioade), Ora.sala_id.is_not(None))
        ).unique():
            if (k := _cheie_slot(o)) is not None:
                sloturi.setdefault(k, []).append(o)

    folosite: set[int] = set()
    dosare: list[Dosar] = []
    for o in ore:
        if o.id in folosite:
            continue
        folosite.add(o.id)
        k = _cheie_slot(o)
        frati = [x for x in sloturi.get(k, []) if x.id != o.id] if k else []
        # componenta conexa: o citire se leaga de grup daca seamana cu oricare din el
        grup, de_vazut = [o], [o]
        while de_vazut:
            curent = de_vazut.pop()
            for x in frati:
                if x not in grup and aceeasi_activitate(curent, x):
                    grup.append(x)
                    de_vazut.append(x)
        membri = [x for x in grup if x.id in in_coada]
        folosite.update(x.id for x in membri)
        # vecinii din acelasi loc si moment, dar care nu par aceeasi activitate
        acelasi_loc = (
            [
                x
                for x in s.scalars(
                    select(Ora).where(
                        Ora.perioada_id == o.perioada_id,
                        Ora.zi_saptamana == o.zi_saptamana,
                        Ora.ora_inceput < o.ora_sfarsit,
                        Ora.ora_sfarsit > o.ora_inceput,
                        Ora.sala_id == o.sala_id,
                    )
                )
                if x not in grup
            ]
            if k
            else []
        )
        dosare.append(
            Dosar(
                membri=membri,
                referinte=[x for x in grup if x.id not in in_coada],
                vecini=acelasi_loc,
            )
        )
    return dosare
