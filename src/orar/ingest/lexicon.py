"""Vocabulare inchise, pentru corectarea iesirii OCR.

De ce merita
------------
Orarul foloseste un vocabular mic si repetitiv: 38 de sali, ~160 de abrevieri de materie,
~200 de profesori. Un OCR care ezita intre `Macovei B` si `Macovei E` are, in practica,
un singur raspuns valid -- daca stim lista. Deci nu ne intereseaza "ce scrie acolo" in
general, ci "care termen cunoscut seamana cel mai bine", iar scorul potrivirii devine
increderea pe care o punem in campul respectiv.

Vocabularul vine din baza de date (populata de ingestul anterior). La prima instalare baza
e goala; atunci `Lexicon` nu potriveste nimic, toate campurile pleaca cu incredere mica si
ajung in coada de verificare -- ceea ce e comportamentul corect, nu o defectiune.

Ce **nu** face
--------------
Nu inventeaza termeni. Sub `PRAG_ACCEPTARE` intoarce textul brut cu increderea data de
scorul OCR, marcat ca nepotrivit; decizia de a-l accepta sau nu apartine apelantului.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from orar.domain.names import potriveste, tokenuri
from orar.domain.rooms import normalizeaza_sala

log = logging.getLogger(__name__)

__all__ = ["Potrivire", "Lexicon", "PRAG_ACCEPTARE"]

#: Sub scorul asta nu inlocuim textul citit cu un termen din vocabular.
PRAG_ACCEPTARE = 0.80
#: Sirurile scurte se potrivesc "bine" cu orice; le cerem un scor mai mare.
LUNGIME_SCURTA = 5
PRAG_SCURT = 0.90

_RE_SPATII = re.compile(r"\s+")

#: Perechi de caractere pe care recunoasterea le confunda sistematic, la fontul asta.
#: Le colapsam la acelasi simbol **doar pentru comparatie** -- iesirea ramane grafia din
#: vocabular. Fara asta, `Ion L` citit `lon L` pica pragul si ajunge inutil in verificare.
_CONFUZII = str.maketrans(
    {
        "l": "i",
        "1": "i",
        "|": "i",
        "0": "o",
        "5": "s",
        "8": "b",
        "&": "e",
        "6": "b",
        "2": "z",
    }
)


def _normalizeaza(text: str) -> str:
    """Forma de comparatie: fara spatii in plus, minuscule."""
    return _RE_SPATII.sub(" ", text).strip().lower()


def _cheie_fuzzy(text: str) -> str:
    """Forma pe care o comparam fuzzy: fara distinctiile pe care OCR-ul nu le face."""
    return _normalizeaza(text).translate(_CONFUZII)


@dataclass(frozen=True)
class Potrivire:
    """Rezultatul cautarii unui text intr-un vocabular."""

    #: Termenul din vocabular, sau textul brut daca nu s-a potrivit nimic.
    valoare: str
    #: 0..1. Pentru o potrivire, scorul de similitudine; altfel 0.
    scor: float
    #: True cand valoarea vine din vocabular (deci e sigur scrisa corect).
    din_vocabular: bool

    @property
    def exacta(self) -> bool:
        return self.din_vocabular and self.scor >= 0.999


@dataclass
class Lexicon:
    """Vocabularele inchise ale orarului."""

    sali: list[str] = field(default_factory=list)
    materii: list[str] = field(default_factory=list)
    profesori: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Indexam pe forma normalizata, dar intoarcem mereu grafia canonica.
        self._index: dict[str, dict[str, str]] = {}
        for nume, termeni in (
            ("sali", self.sali),
            ("materii", self.materii),
            ("profesori", self.profesori),
        ):
            self._index[nume] = {}
            for t in termeni:
                if t:
                    self._index[nume].setdefault(_normalizeaza(t), t)
        # Salile se compara pe forma canonica (`L-507` == `L.507`), deci tin index separat.
        self._sali_canonice: dict[str, str] = {}
        for s in self._index["sali"].values():
            self._sali_canonice.setdefault(normalizeaza_sala(s).slug, s)

    # -- constructori ------------------------------------------------------

    @classmethod
    def din_baza(cls, sesiune) -> Lexicon:  # noqa: ANN001
        """Vocabularele din baza, **fara** termenii pe care tot noi i-am marcat nesiguri.

        Vocabularul se reincarca din baza la fiecare ingest, iar baza e umpluta tot de
        ingest. Fara filtrul asta, o citire gresita intra ca termen valid si de la a doua
        rulare devine "cuvant cunoscut": data viitoare aceeasi celula se potriveste perfect
        cu propria ei greseala, care nu mai ajunge in coada de verificare. Deci un termen
        conteaza doar daca apare in cel putin un rand in care campul **nu** e marcat nesigur.
        """
        from sqlalchemy import select

        from orar.db.models import Materie, Ora, Profesor, Sala

        def confirmate(model, camp_ora: str, eticheta: str) -> list[str]:  # noqa: ANN001
            nesigur = Ora.campuri_nesigure
            confirmat = (nesigur.is_(None)) | (~nesigur.contains(eticheta))
            return [
                v
                for (v,) in sesiune.execute(
                    select(model.nume)
                    .join(Ora, getattr(Ora, camp_ora) == model.id)
                    .where(confirmat)
                    .distinct()
                )
                if v
            ]

        # Salile nu au marcaj de nesiguranta: sunt un vocabular inchis si mic, iar
        # `normalizeaza_sala` le aduce oricum la o forma canonica.
        sali = [v for (v,) in sesiune.execute(select(Sala.nume)) if v]
        # PROFESOR.NUME e campul intreg al celulei, deci poate tine mai multi oameni
        # (`Iftimie S / Tazlaoanu C`). Vocabularul e de persoane, ca in `din_json`: altfel
        # `Iftimie S`, citit singur, s-ar potrivi cu randul celor doi.
        profesori = sorted(
            {
                p
                for camp in confirmate(Profesor, "profesor_id", "profesor")
                for p in _profesori_din(camp)
            }
        )
        return cls(
            sali=sali,
            materii=confirmate(Materie, "materie_id", "materie"),
            profesori=profesori,
        )

    @classmethod
    def din_json(cls, cale) -> Lexicon:  # noqa: ANN001
        """Vocabularele deduse dintr-un JSON de orar (bootstrap, fara baza)."""
        import json
        from pathlib import Path

        from orar.ingest.segment import ZILE as zile

        date = json.loads(Path(cale).read_text(encoding="utf-8"))
        sali: set[str] = set()
        materii: set[str] = set()
        profesori: set[str] = set()
        for pagina in date:
            for zi in zile:
                for a in pagina.get(zi, []):
                    if a.get("sala"):
                        sali.add(a["sala"])
                    if a.get("materie"):
                        materii.add(a["materie"])
                    for p in _profesori_din(a.get("profesor", "")):
                        profesori.add(p)
        return cls(sali=sorted(sali), materii=sorted(materii), profesori=sorted(profesori))

    # -- cautare -----------------------------------------------------------

    def sala(self, text: str) -> Potrivire:
        """Sala, comparata pe forma normalizata (`L-507` si `L.507` sunt aceeasi)."""
        brut = text.strip()
        if not brut:
            return Potrivire("", 0.0, False)
        tinta = normalizeaza_sala(brut).slug
        if tinta in self._sali_canonice:
            return Potrivire(self._sali_canonice[tinta], 1.0, True)
        gasit = _cel_mai_apropiat(tinta, list(self._sali_canonice))
        if gasit is None:
            return Potrivire(brut, 0.0, False)
        termen, scor = gasit
        return Potrivire(self._sali_canonice[termen], scor, True)

    def materie(self, text: str) -> Potrivire:
        return self._cauta(text, "materii")

    def profesor(self, text: str) -> Potrivire:
        """Un camp de profesor; poate contine mai multi, separati prin `/`."""
        rezultat = self._profesori(_profesori_din(text))
        if rezultat.din_vocabular:
            return rezultat
        # Bara dintre doi profesori iese uneori `I` sau `l`, si atunci tot campul ramane un
        # singur nume imposibil (`Baetica C I Mincu G`). Incercam si taietura acolo, dar o
        # pastram **numai** daca fiecare bucata devine un profesor cunoscut -- altfel am
        # rupe in doua un nume care contine legitim initiala `I`.
        alternativ = _RE_BARA_CITITA_GRESIT.split(text)
        if len(alternativ) > 1:
            incercare = self._profesori([p.strip() for p in alternativ if p.strip()])
            if incercare.din_vocabular:
                return incercare
        return rezultat

    def _profesori(self, parti: list[str]) -> Potrivire:
        if not parti:
            return Potrivire("", 0.0, False)
        potriviri = [self._cauta(p, "profesori") for p in parti]
        return Potrivire(
            valoare=" / ".join(p.valoare for p in potriviri),
            scor=min(p.scor for p in potriviri),
            din_vocabular=all(p.din_vocabular for p in potriviri),
        )

    def _cauta(self, text: str, vocabular: str) -> Potrivire:
        brut = text.strip()
        if not brut:
            return Potrivire("", 0.0, False)
        index = self._index[vocabular]
        cheie = _normalizeaza(brut)
        if cheie in index:
            return Potrivire(index[cheie], 1.0, True)
        if vocabular == "profesori" and (dupa_nume := _dupa_prescurtare(brut, index.values())):
            return dupa_nume
        gasit = _cel_mai_apropiat(cheie, list(index))
        if gasit is None:
            return Potrivire(brut, 0.0, False)
        termen, scor = gasit
        return Potrivire(index[termen], scor, True)


#: Increderea unei potriviri dupa regula de prescurtare: sigura ca persoana, dar nu litera
#: cu litera -- deci sub 1.0, ca `Potrivire.exacta` sa ramana doar pentru egalitate.
SCOR_PRESCURTARE = 0.95


def _dupa_prescurtare(brut: str, cunoscuti: Iterable[str]) -> Potrivire | None:
    """Profesorul a carui prescurtare aSc e textul citit: `Cheval H` -> `Cheval Andrei-Horatiu`.

    De ce e nevoie: dupa verificarea incrucisata, baza tine numele **intregi**, iar celulele
    orarului le scriu tot prescurtat. Comparate ca siruri, `Cheval H` si numele intreg nu
    seamana destul, deci la urmatorul ingest fiecare profesor ar iesi "necunoscut"; iar unde
    seamana (`Popescu A` / `Popescu Ana`), asemanarea alege la intamplare intre Ana si Adrian.
    Regula de prescurtare (`domain.names`) stie exact cine poate fi.

    Intoarce None cand nu se potriveste nimeni (apelantul incearca asemanarea de siruri) si o
    potrivire **neconfirmata** cand raman mai multi candidati: ambiguitatea e in sursa, iar a
    alege noi ar insemna sa inventam un profesor.
    """
    candidati = potriveste(brut, cunoscuti)
    if not candidati:
        return None
    if len(candidati) > 1:
        # `Ionescu R` se potriveste si cu `Ionescu RT`, dar e chiar unul dintre ei.
        la_fel = [c for c in candidati if tokenuri(c) == tokenuri(brut)]
        if len(la_fel) == 1:
            candidati = la_fel
    if len(candidati) == 1:
        return Potrivire(candidati[0], SCOR_PRESCURTARE, True)
    return Potrivire(brut, 0.0, False)


def _cel_mai_apropiat(tinta: str, candidati: Sequence[str]) -> tuple[str, float] | None:
    """Termenul cel mai apropiat, daca trece pragul. Altfel None.

    Comparam pe cheia fara confuzii de caractere, dar intoarcem indexul din `candidati`,
    ca apelantul sa poata regasi grafia originala.
    """
    if not candidati or not tinta:
        return None
    chei = [_cheie_fuzzy(c) for c in candidati]
    rezultat = process.extractOne(_cheie_fuzzy(tinta), chei, scorer=fuzz.ratio)
    if rezultat is None:
        return None
    _, scor, indice = rezultat
    scor /= 100.0
    # Pe siruri scurte, o singura litera gresita da inca ~0.8 similitudine, dar poate fi
    # o alta materie reala (`BD` vs `BD1`). Deci cerem mai mult acolo unde greseala costa.
    prag = PRAG_SCURT if len(tinta) <= LUNGIME_SCURTA else PRAG_ACCEPTARE
    return (candidati[indice], scor) if scor >= prag else None


#: Bara de separare citita ca litera: `I` sau `l` singur intre doua nume.
_RE_BARA_CITITA_GRESIT = re.compile(r"\s+[Il/]\s+")


def _profesori_din(text: str) -> list[str]:
    return [p.strip() for p in text.split("/") if p.strip()]


def imbina(*lexicoane: Lexicon) -> Lexicon:
    """Reuniunea mai multor vocabulare (ex. baza + o lista externa de profesori)."""

    def unic(alege) -> list[str]:  # noqa: ANN001
        vazute: dict[str, str] = {}
        for lex in lexicoane:
            for t in alege(lex):
                vazute.setdefault(_normalizeaza(t), t)
        return sorted(vazute.values())

    return Lexicon(
        sali=unic(lambda x: x.sali),
        materii=unic(lambda x: x.materii),
        profesori=unic(lambda x: x.profesori),
    )
