"""Numarul si paritatea saptamanii academice.

Multe activitati se tin doar in saptamani impare (`SI`) sau doar in cele pare (`SP`),
iar altele doar intr-un interval (`[sapt 1-7]`). Ca sa stim ce se intampla *azi* avem
nevoie de numarul saptamanii curente.

Pagina FMI publica ancorele direct in text:

    Saptamana 06.04.2026 - 09.04.2026 este saptamana impara (sapt 7).
    Saptamana 20.04.2026 - 24.04.2026 este saptamana para (sapt 8).

Atentie: intre cele doua ancore sunt **doua** saptamani calendaristice, dar numai **una**
academica -- saptamana 13-17 aprilie e vacanta de Paste si nu se numara. Numerotarea FMI
sare peste vacante, deci nu se poate calcula prin simpla impartire la 7 de la o singura
ancora. De aceea pastram *toate* ancorele publicate si extrapolam de la cea mai apropiata,
marcand rezultatul ca aproximativ cand nu cade exact pe o ancora.

Paritatea nu se propaga separat: e chiar paritatea numarului academic (saptamana 7 e
impara, 8 e para), verificata pe ambele ancore publicate de FMI.

Structura anului universitar
----------------------------
Ancorele sunt o rezerva. Sursa sigura e **structura anului**, pusa de un admin: perioadele
de activitate didactica, vacantele, sesiunile. Din perioadele didactice ale unui semestru
numerotarea iese singura -- fiecare saptamana calendaristica cu zile didactice e urmatoarea
saptamana academica, iar vacantele nu se numara. Ramane un caz pe care nu-l poate deduce
nimic: cand semestrul incepe intr-o joi, zilele luni-miercuri care lipsesc saptamanii 1 se
recupereaza mai tarziu (de exemplu 21-23 decembrie, inainte de vacanta de iarna). Pentru
asta, o perioada didactica poate spune explicit "se numara ca saptamana N" si atunci nu
avanseaza numaratoarea. Vezi `numeroteaza`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

__all__ = [
    "Paritate",
    "AncoraSaptamana",
    "CalendarAcademic",
    "Saptamana",
    "parse_ancore",
    "parse_interval_saptamani",
    "PATTERN_ANCORA",
    "FELURI_PERIOADA",
    "PUBLIC_PERIOADA",
    "PerioadaAn",
    "an_universitar_al",
    "ce_lipseste",
    "numeroteaza",
]

#: Felurile de perioade din structura anului universitar, in ordinea in care se afiseaza.
FELURI_PERIOADA = {
    "didactica": "Activitate didactică",
    "vacanta": "Vacanță",
    "sesiune": "Sesiune",
    "restante": "Sesiune de restanțe și măriri",
    "licenta": "Licență / disertație",
    "liber": "Zi liberă",
}

#: Pentru cine e o perioada. Anii terminali (ultimul an de licenta, anul 2 de master) au alt
#: semestru 2: dupa Paste, elaborarea lucrarii si sesiunea mai devreme.
PUBLIC_PERIOADA = {
    "toti": "toți anii",
    "neterminali": "anii neterminali",
    "terminali": "anii terminali",
}


def an_universitar_al(zi: date) -> str:
    """Anul universitar in care cade o zi: de pe 30 septembrie incepe anul nou.

    Ziua de 30 septembrie e cea de la care adminul trebuie sa fi pus structura anului care
    incepe (semestrul porneste de obicei pe 1 octombrie)."""
    inceput = zi.year if (zi.month, zi.day) >= (9, 30) else zi.year - 1
    return f"{inceput}-{inceput + 1}"


@dataclass(frozen=True)
class PerioadaAn:
    """O perioada din structura anului universitar."""

    fel: str  # una din FELURI_PERIOADA
    inceput: date
    sfarsit: date
    semestru: int | None = None
    nume: str = ""
    #: Doar la activitatea didactica: zilele perioadei sunt saptamana N, fara sa avanseze
    #: numaratoarea -- completarea unei saptamani inceput la mijloc (vezi docstring-ul modulului).
    saptamana: int | None = None
    #: Anul universitar caruia ii apartine ("2026-2027"); gol = dedus din data de inceput.
    an: str = ""
    #: toti / neterminali / terminali -- vezi PUBLIC_PERIOADA.
    pentru: str = "toti"

    @property
    def eticheta(self) -> str:
        nume = self.nume or FELURI_PERIOADA.get(self.fel, self.fel)
        return f"{nume} (ani terminali)" if self.pentru == "terminali" else nume

    @property
    def generala(self) -> bool:
        """Se aplica majoritatii anilor -- din ea se numara saptamanile si se scrie bara de sus.
        Ce e doar pentru anii terminali apare doar in calendar."""
        return self.pentru != "terminali"

    def contine(self, zi: date) -> bool:
        return self.inceput <= zi <= self.sfarsit


def numeroteaza(perioade: list[PerioadaAn]) -> dict[date, int]:
    """Numarul saptamanii academice pentru fiecare zi de activitate didactica.

    Pe fiecare semestru, separat: fiecare saptamana calendaristica (luni-duminica) in care
    cad zile didactice primeste urmatorul numar. Doua exceptii:

    * **Completarea saptamanii 1.** Cand semestrul incepe la mijlocul saptamanii (joi, 1
      octombrie), zilele care lipsesc saptamanii 1 se recupereaza mai tarziu, intr-o
      saptamana care are exact acele zile (luni-miercuri, 21-23 decembrie). O astfel de
      saptamana e recunoscuta singura: e tot saptamana 1 si nu muta numaratoarea.
    * **Numar explicit.** O perioada cu `saptamana` da acel numar zilelor ei, pentru orice
      caz pe care regula de mai sus nu-l prinde.
    """
    zile: dict[date, int] = {}
    # numerotarea e a anilor obisnuiti; anii terminali au aceleasi saptamani cat timp au cursuri
    perioade = [p for p in perioade if p.generala and p.fel == "didactica"]
    for semestru in sorted({p.semestru for p in perioade}, key=str):
        din_semestru = [p for p in perioade if p.semestru == semestru]
        # saptamanile calendaristice cu zile didactice, in ordine: {(an ISO, saptamana): zile}
        saptamani: dict[tuple[int, int], list[date]] = {}
        for p in din_semestru:
            zi = p.inceput
            while zi <= p.sfarsit:
                if p.saptamana is not None:
                    zile[zi] = p.saptamana
                else:
                    saptamani.setdefault(zi.isocalendar()[:2], []).append(zi)
                zi += timedelta(days=1)
        ordonate = [saptamani[k] for k in sorted(saptamani)]
        if not ordonate:
            continue

        # saptamana 1 incomplete: care zile lucratoare ii lipsesc, si cine le recupereaza
        lucratoare = lambda z: {d.weekday() for d in z if d.weekday() < 5}  # noqa: E731
        lipsa = set(range(5)) - lucratoare(ordonate[0])
        completare = next(
            (i for i, z in enumerate(ordonate[1:], 1) if lipsa and lucratoare(z) == lipsa), None
        )
        numar = 0
        for i, z in enumerate(ordonate):
            if i == completare:
                zile.update(dict.fromkeys(z, 1))
                continue
            numar += 1
            zile.update(dict.fromkeys(z, numar))
    return zile


#: Ce trebuie sa aiba structura unui an ca sa fie "configurata": (fel, semestru sau None).
_CERUTE = (
    ("didactica", 1, "activitatea didactică din semestrul 1"),
    ("didactica", 2, "activitatea didactică din semestrul 2"),
    ("vacanta", None, "vacanțele"),
    ("sesiune", None, "sesiunea"),
    ("restante", None, "sesiunea de restanțe și măriri"),
    ("licenta", None, "susținerea lucrării de licență / disertație"),
)


def ce_lipseste(perioade: list[PerioadaAn]) -> list[str]:
    """Ce lipseste din structura unui an, in cuvinte; lista goala = configurata."""
    return [
        eticheta
        for fel, semestru, eticheta in _CERUTE
        if not any(
            p.fel == fel and (semestru is None or p.semestru == semestru) and p.generala
            for p in perioade
        )
    ]


class Paritate(StrEnum):
    IMPARA = "SI"
    PARA = "SP"

    @property
    def eticheta(self) -> str:
        return "impară" if self is Paritate.IMPARA else "pară"

    @classmethod
    def din_numar(cls, numar: int) -> Paritate:
        """Saptamana 7 e impara, 8 e para -- paritatea numarului academic."""
        return cls.IMPARA if numar % 2 else cls.PARA


def _luni(d: date) -> date:
    return d - timedelta(days=d.weekday())


@dataclass(frozen=True)
class AncoraSaptamana:
    """O corespondenta publicata intre o saptamana calendaristica si numarul ei academic."""

    inceput: date
    numar: int
    paritate: Paritate

    def __post_init__(self) -> None:
        object.__setattr__(self, "inceput", _luni(self.inceput))


@dataclass(frozen=True)
class Saptamana:
    """Rezultatul unei interogari de calendar."""

    numar: int
    paritate: Paritate
    #: False cand numarul a fost extrapolat peste vacante necunoscute.
    exact: bool

    @property
    def eticheta(self) -> str:
        return f"săptămâna {self.numar} ({self.paritate.eticheta})"


@dataclass
class CalendarAcademic:
    """Traduce date calendaristice in saptamani academice, pe baza ancorelor publicate.

    Cu o singura ancora extrapolarea presupune saptamani consecutive, ceea ce e gresit
    peste vacante -- de aceea `Saptamana.exact` devine False. Cu cat watcher-ul reimprospateaza
    mai des ancorele de pe site (Etapa 6), cu atat raspunsul e mai des exact.
    """

    ancore: list[AncoraSaptamana] = field(default_factory=list)
    #: Cate saptamani are semestrul; in afara lor nu mai raspundem.
    lungime_semestru: int = 14
    #: Structura anilor universitari configurati. Unde exista, are intaietate fata de ancore.
    perioade: list[PerioadaAn] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ancore = sorted(self.ancore, key=lambda a: a.inceput)
        self._zile = numeroteaza(self.perioade)
        # intervalele acoperite de o structura: un an universitar, de la prima la ultima perioada
        ani: dict[str, list[PerioadaAn]] = {}
        for p in self.perioade:
            ani.setdefault(p.an or an_universitar_al(p.inceput), []).append(p)
        self._acoperite = [
            (min(p.inceput for p in ps), max(p.sfarsit for p in ps)) for ps in ani.values()
        ]

    def din_structura(self, zi: date) -> bool:
        """Ziua cade intr-un an universitar a carui structura e configurata?"""
        return any(a <= zi <= b for a, b in self._acoperite)

    def perioada(self, zi: date) -> PerioadaAn | None:
        """Perioada din structura in care cade ziua, pentru anii obisnuiti. Cand se suprapun,
        cea mai anume castiga: o zi libera, apoi o vacanta sau o sesiune, apoi activitatea
        didactica."""
        potrivite = [p for p in self.perioade if p.contine(zi) and p.generala]
        return min(potrivite, key=lambda p: (p.fel != "liber", p.fel == "didactica"), default=None)

    def perioade_zilei(self, zi: date) -> list[PerioadaAn]:
        """Toate perioadele in care cade ziua, si cele doar pentru anii terminali (calendarul)."""
        return [p for p in self.perioade if p.contine(zi)]

    def saptamana(self, zi: date) -> Saptamana | None:
        """Saptamana academica a unei zile, sau None daca suntem in afara semestrului."""
        if self.din_structura(zi):
            numar = self._zile.get(zi)
            if numar is None:
                return None
            return Saptamana(numar, Paritate.din_numar(numar), exact=True)
        if not self.ancore:
            return None

        luni = _luni(zi)

        for a in self.ancore:
            if a.inceput == luni:
                return Saptamana(a.numar, Paritate.din_numar(a.numar), exact=True)

        # Interpolare intre doua ancore: daca numerotarea e contigua acolo, e sigura.
        for a, b in zip(self.ancore, self.ancore[1:], strict=False):
            if a.inceput < luni < b.inceput:
                delta_cal = (b.inceput - a.inceput).days // 7
                delta_acad = b.numar - a.numar
                if delta_cal == delta_acad:
                    numar = a.numar + (luni - a.inceput).days // 7
                    return self._valideaza(numar, exact=True)
                # Numerotarea sare: e vacanta undeva intre, nu stim exact unde.
                return None

        cea_mai_apropiata = min(self.ancore, key=lambda a: abs((luni - a.inceput).days))
        numar = cea_mai_apropiata.numar + (luni - cea_mai_apropiata.inceput).days // 7
        return self._valideaza(numar, exact=False)

    def _valideaza(self, numar: int, *, exact: bool) -> Saptamana | None:
        if not 1 <= numar <= self.lungime_semestru:
            return None
        return Saptamana(numar, Paritate.din_numar(numar), exact=exact)


# "Saptamana 06.04.2026 - 09.04.2026 este saptamana impara (sapt 7)"
PATTERN_ANCORA = re.compile(
    r"S[ăa]pt[ăa]m[âa]na\s+(?P<zi>\d{1,2})\.(?P<luna>\d{1,2})\.(?P<an>\d{4})"
    r".*?este\s+s[ăa]pt[ăa]m[âa]n[ăa]\s+(?P<par>impar[ăa]|par[ăa])"
    r"(?:\s*\(\s*sapt\.?\s*(?P<nr>\d+)\s*\))?",
    re.IGNORECASE | re.DOTALL,
)


def parse_ancore(text: str) -> list[AncoraSaptamana]:
    """Extrage ancorele de saptamana dintr-un text (pagina FMI).

    Ancorele fara numar explicit `(sapt N)` se ignora: fara numar nu putem fixa
    originea numerotarii, iar o paritate singura nu e suficienta.
    """
    ancore: list[AncoraSaptamana] = []
    for m in PATTERN_ANCORA.finditer(text):
        if not m.group("nr"):
            continue
        este_impara = m.group("par").lower().startswith("i")
        ancore.append(
            AncoraSaptamana(
                inceput=date(int(m.group("an")), int(m.group("luna")), int(m.group("zi"))),
                numar=int(m.group("nr")),
                paritate=Paritate.IMPARA if este_impara else Paritate.PARA,
            )
        )
    return ancore


# `sapt 1-7`, `sapt 2`, `sapt 8-14` -- numere de saptamana ACADEMICA (1..14)
_RE_ACADEMICE = re.compile(r"sapt\.?\s*(?P<corp>[\d\s,+\-]+)", re.IGNORECASE)
# `CU s23,24,25, 26`, `SE s20+21` -- numere de saptamana CALENDARISTICA (ISO), alta scara
_RE_CALENDARISTICE = re.compile(r"\bs\s?\d{1,2}\b", re.IGNORECASE)


def parse_interval_saptamani(text: str) -> set[int] | None:
    """Multimea saptamanilor ACADEMICE in care se tine o activitate.

    Intoarce None cand textul nu se poate raporta la numerotarea academica -- fie e liber
    ("25 mar, 29 apr..."), fie foloseste saptamani calendaristice ISO ("CU s23,24,25, 26",
    "SE s20+21"), care sunt alta scara. In ambele cazuri apelantul afiseaza activitatea
    mereu, ca sa nu o ascunda din greseala.

    >>> sorted(parse_interval_saptamani("sapt 1-7"))
    [1, 2, 3, 4, 5, 6, 7]
    >>> parse_interval_saptamani("CU s23,24,25, 26") is None
    True
    """
    if not text or not text.strip():
        return None

    m = _RE_ACADEMICE.search(text)
    if not m:
        return None

    corp = m.group("corp")
    numere: set[int] = set()
    for interval in re.finditer(r"(\d{1,2})\s*-\s*(\d{1,2})", corp):
        a, b = int(interval.group(1)), int(interval.group(2))
        if 1 <= a <= b <= 20:
            numere.update(range(a, b + 1))
    for singur in re.finditer(r"\d{1,2}", re.sub(r"\d{1,2}\s*-\s*\d{1,2}", " ", corp)):
        n = int(singur.group())
        if 1 <= n <= 20:
            numere.add(n)

    return numere or None
