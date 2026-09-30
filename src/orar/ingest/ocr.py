"""Citirea textului dintr-o pagina deja segmentata.

Ce face si ce nu face detectorul neural
---------------------------------------
Am incercat intai rapidocr "din cutie", cu detectorul lui de text. Pe celulele astea se
descurca prost: rupe `Prunescu M` in `runescu`, taie `ESLA (curs) [sapt 3-4]` in patru
bucati si pierde litera din capul randului. Detectorul e antrenat pe fotografii si documente
scanate, nu pe dreptunghiuri mici de text vectorial pe fundal pastel.

Dar **noi nu avem nevoie de el.** Pagina e randare de PDF: textul e negru curat pe umplere
deschisa, randurile sunt separate de goluri albe perfecte. Deci decupam noi, geometric, si
folosim din rapidocr doar **recunoasterea** -- partea la care chiar e buna. Pe aceleasi
celule, iesirea devine intreaga si curata (`ESLA (Lab) [sapt 8-10]`, scor 0.91), si de ~25x
mai rapida, pentru ca recunoasterea merge pe loturi.

Unde taiem intre campuri
------------------------
In interiorul unui rand, distanta dintre litere si distanta dintre *campuri* (profesor |
`Gr_N` | sala) sunt doua populatii distincte. Masurat pe toate cele 98 de pagini, 34276 de
goluri: raportate la inaltimea randului, spatiile dintre cuvinte stau sub 0.8, separatorii
de camp peste 1.2, iar **intre 0.8 si 1.2 nu cade nimic**. Taiem la 1.0 -- la mijlocul unei
vai goale, deci pragul nu e reglat pe ochi si nu depinde de rezolutie.

Atribuirea campurilor
---------------------
Layout-ul e fix (vezi `docs/formatul-orarului.md` §3), dar se comprima in benzile dese, asa
ca nu ne bazam doar pe pozitie: intai recunoastem *forma* (partea structurata cu paranteze,
`Gr_N`), apoi ce ramane se imparte dupa pozitie si dupa vocabular.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from orar.ingest.lexicon import Lexicon

# Acelasi prag ca la segmentare: OCR-ul refoloseste masca de text calculata acolo.
from orar.ingest.segment import PRAG_NEGRU, Celula, PaginaSegmentata, masca_negru, segmenteaza

log = logging.getLogger(__name__)

__all__ = [
    "BucataText",
    "ActivitateCitita",
    "PaginaCitita",
    "MotorRecunoastere",
    "RapidOCR",
    "citeste_pagina",
    "citeste_fisier",
    "PRAG_GOL_CAMP",
    "PRAG_NEGRU",
]

#: Cat ignoram din marginea celulei, ca sa nu prindem chenarul.
MARJA_CELULA = 4
#: Cati pixeli de aer lasam in jurul unui decupaj dat la recunoastere.
PERNA = 6
#: Gol pe orizontala, raportat la inaltimea randului, de la care taiem intre campuri.
PRAG_GOL_CAMP = 1.0
#: Mai jos de atat, o fasie de pixeli nu e rand de text, ci coada unui glif -- practic
#: liniuta lui `_` din `Gr_1`. Masurat pe cele 98 de pagini: fasiile au 1-3 px, randurile
#: adevarate incep de la 18. Intre ele nu cade nimic, deci pragul nu e reglat pe ochi.
INALTIME_FASIE = 8
#: Cat de aproape trebuie sa fie fasia de randul ei. Masurat: 5-6 px pentru `_`, in timp ce
#: doua randuri de text vecine sunt la >= 7 px. Nu unim niciodata doua randuri adevarate:
#: aSc scrie `Geom&AlgLin` si `(seminar)` pe doua linii lipite, iar un decupaj cu doua
#: linii de text intra intr-un model de recunoastere *de o linie* si iese ilizibil.
GOL_FASIE = 8

_TIPURI = ("curs+seminar", "sem+Lab", "seminar", "proiect", "curs", "Lab", "sem")
#: Paranteza cu tipul activitatii, cautata **oriunde** in grup, nu ancorata la capete.
#: Cand celula e ingusta, decuparea lasa in acelasi grup si materia, si paranteza, si sala
#: (`ManagProd&Antrepr (Lab, SP)L-509`); o expresie ancorata la `$` ar rata tot.
_RE_PAREN = re.compile(r"\(\s*(?P<tip>[^(),]*?)\s*(?:,\s*(?P<frecventa>[^()]*?)\s*)?\)")
#: Grup care incepe cu paranteze drepte. De obicei e tot (`[sapt 1-7]`), dar la activitatile
#: cu date punctuale aSc scrie eticheta in paranteze si saptamanile dupa: `[SE] s25, 26`.
_RE_GRUP_SAPT = re.compile(r"^\[\s*(?P<tag>[^\]]*?)\s*\]\s*(?P<coada>.*)$")
_RE_SAPT = re.compile(r"\[\s*(?P<sapt>[^\]]*?)\s*\]")
#: Numarul formatiunii, scris in celula acolo unde nu e trecut niciun profesor
#: (laboratoarele tinute la Fizica/Magurele). Nu e nume de om, deci nu intra la profesor.
_RE_NUMAR = re.compile(r"^\d{2,4}$")
#: Una sau mai multe formatiuni: `407`, `251/252`, `231/232/233/234`.
_RE_FORMATIUNI = re.compile(r"^\d{3}(\s*/\s*\d{3})*$")
_RE_SEMIGRUPA = re.compile(r"^Gr\s*[_. ]?\s*(?P<n>[1-4])$", re.IGNORECASE)
_RE_SEMIGRUPE = re.compile(r"^Gr\s*[_. ]?\s*[1-4](\s*/\s*Gr\s*[_. ]?\s*[1-4])+$", re.IGNORECASE)


class EroareOCR(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Decuparea geometrica a textului
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BucataText:
    """Un grup de cuvinte dintr-o celula, cu locul lui."""

    #: (x0, y0, x1, y1) in coordonatele paginii.
    bbox: tuple[int, int, int, int]
    #: Al catelea rand de text din celula (0 = primul).
    rand: int
    #: Textul citit si increderea recunoasterii (0..1). Goale pana la recunoastere.
    text: str = ""
    scor: float = 0.0

    @property
    def x0(self) -> int:
        return self.bbox[0]

    @property
    def x1(self) -> int:
        return self.bbox[2]


def _intervale(prezent: np.ndarray, gol_maxim: int) -> list[tuple[int, int]]:
    """Intervalele [start, stop) de True, unind pauzele de cel mult `gol_maxim`."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    gol = 0
    for i, p in enumerate(prezent):
        if p:
            if start is None:
                start = i
            gol = 0
        elif start is not None:
            gol += 1
            if gol > gol_maxim:
                out.append((start, i - gol))
                start = None
                gol = 0
    if start is not None:
        out.append((start, len(prezent)))
    return out


def _randuri(negru: np.ndarray) -> list[tuple[int, int]]:
    """Randurile de text, cu cozile de glif lipite inapoi de randul lor.

    Taiem la gol zero si abia apoi reunim fasiile: asa `Gr_1` ramane intreg, dar doua
    randuri de text apropiate raman doua.
    """
    brute = _intervale(negru.any(axis=1), gol_maxim=0)
    randuri: list[list[int]] = []
    for y0, y1 in brute:
        fasie = y1 - y0 + 1 <= INALTIME_FASIE
        if randuri and fasie and y0 - randuri[-1][1] <= GOL_FASIE:
            randuri[-1][1] = y1
        else:
            randuri.append([y0, y1])
    return [(a, b) for a, b in randuri]


def decupeaza_text(negru: np.ndarray, origine: tuple[int, int]) -> Iterator[BucataText]:
    """Imparte masca de text a unei celule in grupuri de campuri.

    `negru` e decupajul din interiorul celulei (fara chenar), `origine` coltul lui in pagina.
    """
    ox, oy = origine
    for r, (y0, y1) in enumerate(_randuri(negru)):
        inaltime = y1 - y0 + 1
        banda = negru[y0 : y1 + 1]
        bucati = _intervale(banda.any(axis=0), gol_maxim=0)
        if not bucati:
            continue
        prag = PRAG_GOL_CAMP * inaltime
        curent = list(bucati[0])
        for a, b in bucati[1:]:
            if a - curent[1] >= prag:
                yield BucataText((ox + curent[0], oy + y0, ox + curent[1] + 1, oy + y1 + 1), r)
                curent = [a, b]
            else:
                curent[1] = b
        yield BucataText((ox + curent[0], oy + y0, ox + curent[1] + 1, oy + y1 + 1), r)


# ---------------------------------------------------------------------------
# Motorul de recunoastere
# ---------------------------------------------------------------------------


class MotorRecunoastere:
    """Ce trebuie sa stie sa faca un motor: text + incredere pentru o lista de decupaje."""

    def recunoaste(self, decupaje: Sequence[np.ndarray]) -> list[tuple[str, float]]:
        raise NotImplementedError


class RapidOCR(MotorRecunoastere):
    """rapidocr-onnxruntime, local (ONNX pe CPU), fara niciun apel de retea."""

    def __init__(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR as _Motor
        except ImportError as e:  # pragma: no cover
            raise EroareOCR(
                "rapidocr-onnxruntime nu e instalat. Ruleaza: pip install -e '.[ingest]'"
            ) from e
        self._motor = _Motor()

    def recunoaste(self, decupaje: Sequence[np.ndarray]) -> list[tuple[str, float]]:
        if not decupaje:
            return []
        rezultat, _ = self._motor.text_recognizer(list(decupaje))
        return [(text, float(scor)) for text, scor in rezultat]


# ---------------------------------------------------------------------------
# Activitatea citita
# ---------------------------------------------------------------------------


@dataclass
class ActivitateCitita:
    """O celula, citita. Aceleasi campuri ca in `date.json`, plus provenienta."""

    zi: str
    ore: str
    profesor: str = ""
    materie: str = ""
    tip: str = ""
    frecventa: str = ""
    saptamani: str = ""
    semigrupa: str = ""
    sala: str = ""
    #: Formatiunile scrise in celula (orarul profesorilor le pune in mijloc).
    formatiuni: list[str] = field(default_factory=list)
    #: Increderea minima peste campurile citite (0..1).
    incredere: float = 0.0
    #: Campurile pe care nu le-am putut confirma din vocabular.
    nesigure: list[str] = field(default_factory=list)
    #: Textul brut al grupurilor, pentru coada de verificare.
    brut: list[str] = field(default_factory=list)
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)

    def ca_json(self) -> dict[str, str]:
        """Forma din `date.json`, pentru comparatie cu setul de referinta."""
        return {
            "ore": self.ore,
            "profesor": self.profesor,
            "materie": self.materie,
            "tip": self.tip,
            "frecventa": self.frecventa,
            "saptamani": self.saptamani,
            "semigrupa": self.semigrupa,
            "sala": self.sala,
        }

    def pentru_incarcare(self) -> dict[str, str]:
        """Ca `ca_json`, plus provenienta -- ce are nevoie `load.py` ca sa scrie in baza."""
        return {
            **self.ca_json(),
            "_confidence": self.incredere,
            "_bbox": ",".join(str(v) for v in self.bbox),
            "_nesigure": ",".join(self.nesigure),
        }


@dataclass
class PaginaCitita:
    titlu: str = ""
    scor_titlu: float = 0.0
    activitati: list[ActivitateCitita] = field(default_factory=list)
    avertismente: list[str] = field(default_factory=list)

    def ca_json(self, *, cu_provenienta: bool = False) -> dict:
        """Forma din `date.json`: titlu + cate o lista pe zi."""
        from orar.ingest.segment import ZILE

        camp = "pentru_incarcare" if cu_provenienta else "ca_json"
        out: dict = {"grupa": self.titlu}
        for zi in ZILE:
            out[zi] = [getattr(a, camp)() for a in self.activitati if a.zi == zi]
        return out


# ---------------------------------------------------------------------------
# Atribuirea campurilor
# ---------------------------------------------------------------------------


def _curata_tip(brut: str) -> str | None:
    """Tipul citit, adus la una dintre cele 7 valori posibile. None daca nu e niciuna.

    Distinctia conteaza: parantezele apar si in alte campuri (`IMAR (sala 412)`,
    `L-509 (iOS)`). Daca am accepta orice text intre paranteze ca tip, o sala ar deveni tip
    si materia ar disparea. Deci vocabularul inchis e chiar criteriul de recunoastere.
    """
    t = brut.strip().lower().replace(" ", "")
    for canonic in _TIPURI:
        if t == canonic.lower():
            return canonic
    for canonic in _TIPURI:
        if t.replace("+", "") == canonic.lower().replace("+", ""):
            return canonic
    return None


def _curata_frecventa(brut: str | None) -> str:
    if not brut:
        return ""
    t = brut.strip().upper().replace("1", "I").replace("L", "I")
    return t if t in ("SI", "SP") else ""


def _curata_semigrupa(brut: str) -> str:
    m = _RE_SEMIGRUPA.match(brut.strip())
    if m:
        return f"Gr_{m.group('n')}"
    if _RE_SEMIGRUPE.match(brut.strip()):
        return "/".join(f"Gr_{n}" for n in re.findall(r"[1-4]", brut))
    return ""


def _atribuie(celula: Celula, bucati: list[BucataText], lexicon: Lexicon) -> ActivitateCitita:
    """Imparte grupurile citite pe campuri.

    Nu putem presupune ca `Materie (tip) [sapt]` vine intr-un singur grup: aSc pune uneori
    intre materie si paranteza un gol mai mare decat cel dintre campuri, si atunci decuparea
    geometrica -- corect, dupa masuratori -- le desparte. Deci nu ne bazam pe grupare, ci pe
    **forma**: paranteza cu un tip cunoscut, parantezele drepte cu saptamanile, `Gr_N`. Sunt
    forme pe care nimic altceva din celula nu le imita, oriunde ar cadea taietura.

    Materia se afla apoi **structural**, nu pozitional: e grupul dinaintea parantezei de tip,
    pentru ca tipul urmeaza intotdeauna disciplina lui. Doar cand nu exista paranteza deloc
    (cursurile de limbi straine) cade decizia pe vocabular.
    """
    act = ActivitateCitita(
        zi=celula.zi, ore=celula.ore, bbox=celula.bbox, brut=[b.text for b in bucati]
    )
    scoruri: list[float] = []
    ramase = [b for b in bucati if b.text.strip()]

    ancora, resturi, prefix = _extrage_structura(act, ramase, scoruri, lexicon)
    ramase = [b for b in ramase if b is not ancora and not _consumat(act, b)] + resturi
    # Sala iese prima: are vocabularul cel mai scurt si mai distinctiv (38 de valori), deci
    # e cel mai putin probabil sa fie confundata. Daca am cauta-o la urma, ar ramane lipita
    # de materie in celulele fara paranteza de tip -- acolo materia se recunoaste dupa
    # indentare, iar sala e si ea indentata, fiind aliniata la dreapta.
    sala = _extrage_sala(act, ramase, lexicon, scoruri)
    ramase = [b for b in ramase if b is not sala]
    formatiuni = _extrage_formatiuni(act, ramase)
    ramase = [b for b in ramase if b not in formatiuni]
    materie = _extrage_materie(act, ramase, ancora, prefix, lexicon, scoruri, celula)
    ramase = [b for b in ramase if b not in materie and not _consumat(act, b)]
    _extrage_profesor(act, ramase, lexicon, scoruri)

    act.incredere = min(scoruri) if scoruri else 0.0
    return act


def _extrage_formatiuni(act: ActivitateCitita, ramase: list[BucataText]) -> list[BucataText]:
    """Numerele de formatiune scrise in celula: `407/411/412`, `251/252`, `407`.

    In orarul **profesorilor** ele stau in mijlocul celulei si spun cu cine se tine ora --
    acolo unde orarul grupelor scrie numele profesorului. Le recunoastem dupa forma, deci
    aceeasi functie merge pe amandoua sursele: pe paginile de grupe apar rar, la
    laboratoarele de la Fizica, unde tin tot locul profesorului.
    """
    luate = [b for b in ramase if _RE_FORMATIUNI.match(b.text.strip())]
    for b in luate:
        act.formatiuni += [n for n in re.findall(r"\d{3}", b.text)]
    return luate


def _consumat(act: ActivitateCitita, b: BucataText) -> bool:
    """Grupurile care s-au dus deja intr-un camp de forma fixa."""
    text = b.text.strip()
    return bool(
        (act.semigrupa and _curata_semigrupa(text) == act.semigrupa)
        or (act.saptamani and _RE_GRUP_SAPT.match(text))
    )


def _extrage_structura(
    act: ActivitateCitita, bucati: list[BucataText], scoruri: list[float], lexicon: Lexicon
) -> tuple[BucataText | None, list[BucataText], BucataText | None]:
    """Tipul, frecventa, saptamanile si semigrupa -- campurile cu forma inconfundabila.

    Intoarce trei lucruri: grupul cu paranteza de tip (ancora), fragmentele desprinse din el
    si -- separat -- bucata dinaintea parantezei. Aceea din urma e **sigur** materia, oriunde
    ar sta in celula: in orarul grupelor paranteza e pe randul al doilea, in cel al
    profesorilor pe primul, iar o regula bazata pe rand ar merge doar pe una dintre surse.
    """
    ancora: BucataText | None = None
    resturi: list[BucataText] = []
    prefix: BucataText | None = None
    for b in bucati:
        text = b.text.strip()
        potrivire = _paranteza_de_tip(text)
        if potrivire is not None and ancora is None:
            m, tip = potrivire
            ancora = b
            act.tip = tip
            act.frecventa = _curata_frecventa(m.group("frecventa"))
            bucati_noi = _fragmente(act, b, text[: m.start()], text[m.end() :])
            prefix = next((x for x in bucati_noi if x.x1 <= b.x0), None)
            resturi += bucati_noi
            scoruri.append(b.scor)
            continue
        m = _RE_GRUP_SAPT.match(text)
        if m and not act.saptamani:
            # Celula ingusta: aSc rupe `[sapt 1-7]` pe randul urmator.
            coada, sala = _desparte_sala(m.group("coada"), lexicon)
            act.saptamani = f"{m.group('tag')} {coada}".strip()
            if sala:
                resturi.append(BucataText(b.bbox, b.rand, sala, b.scor))
            scoruri.append(b.scor)
            continue
        semi = _curata_semigrupa(text)
        if semi and not act.semigrupa:
            act.semigrupa = semi
            scoruri.append(b.scor)
    return ancora, resturi, prefix


def _desparte_sala(coada: str, lexicon: Lexicon) -> tuple[str, str]:
    """Rupe sala din coada saptamanilor: `s23,24,25, 26 Amf.501`.

    Cand celula e larga, sala incape pe acelasi rand cu saptamanile si golul dintre ele
    ramane sub pragul de taiere. Ultimul cuvant e sala doar daca vocabularul o confirma.
    """
    cuvinte = coada.split()
    if len(cuvinte) >= 2 and lexicon.sala(cuvinte[-1]).din_vocabular:
        return " ".join(cuvinte[:-1]), cuvinte[-1]
    return coada, ""


def _paranteza_de_tip(text: str) -> tuple[re.Match[str], str] | None:
    """Prima paranteza al carei continut e un tip cunoscut."""
    for m in _RE_PAREN.finditer(text):
        tip = _curata_tip(m.group("tip"))
        if tip:
            return m, tip
    return None


def _fragmente(
    act: ActivitateCitita, ancora: BucataText, inainte: str, dupa: str
) -> list[BucataText]:
    """Bucatile din jurul parantezei, ca grupuri separate."""
    x0, y0, x1, y1 = ancora.bbox
    out: list[BucataText] = []
    inainte = inainte.strip()
    if inainte:
        # La stanga ancorei, ca sa fie gasita ca materie.
        out.append(BucataText((x0 - 1, y0, x0, y1), ancora.rand, inainte, ancora.scor))
    dupa = _RE_SAPT.sub(lambda m: _retine_sapt(act, m), dupa).strip(" -–")
    if dupa:
        out.append(BucataText((x1, y0, x1 + 1, y1), ancora.rand, dupa, ancora.scor))
    return out


def _retine_sapt(act: ActivitateCitita, m: re.Match[str]) -> str:
    if not act.saptamani:
        act.saptamani = m.group("sapt").strip()
    return " "


def _extrage_materie(
    act: ActivitateCitita,
    ramase: list[BucataText],
    ancora: BucataText | None,
    prefix: BucataText | None,
    lexicon: Lexicon,
    scoruri: list[float],
    celula: Celula,
) -> list[BucataText]:
    """Materia: grupurile dinaintea parantezei de tip; fara paranteza, blocul indentat.

    "Dinainte" in ordinea de citire, nu doar pe acelasi rand: cand materia e lunga, aSc o
    rupe si scrie `(seminar)` pe randul urmator. Nu coboram insa pana pe randul 0 -- acolo
    sta intotdeauna profesorul, si l-am lua pe el drept materie.

    Intoarce grupurile consumate, ca apelantul sa nu le mai dea si altui camp.
    """
    consumate: list[BucataText] = []
    if prefix is not None:
        # Ce statea in fata parantezei de tip e materia, indiferent pe ce rand cade.
        consumate = _cea_mai_buna_reunire(
            sorted(
                [b for b in ramase if b is prefix or (b.rand, b.x0) < (prefix.rand, prefix.x0)],
                key=lambda b: (b.rand, b.x0),
            ),
            act,
            lexicon,
        )
    elif ancora is not None and ancora.rand > 0:
        inainte = sorted(
            (b for b in ramase if (b.rand, b.x0) < (ancora.rand, ancora.x0) and b.rand > 0),
            key=lambda b: (b.rand, b.x0),
        )
        if inainte:
            consumate = _cea_mai_buna_reunire(inainte, act, lexicon)
    if not consumate and ancora is None:
        consumate = _blocul_centrat(ramase, celula)

    if not consumate:
        return []
    scoruri.append(min(b.scor for b in consumate))
    for text in _variante_text(consumate):
        act.materie = _fara_saptamani(act, text)
        if lexicon.materie(act.materie).din_vocabular:
            break

    p = lexicon.materie(act.materie)
    act.materie = p.valoare
    if not p.din_vocabular:
        act.nesigure.append("materie")
    return consumate


def _variante_text(bucati: list[BucataText]) -> list[str]:
    """Cum se reunesc bucatile: lipite, sau cu spatiu.

    aSc rupe randul si la mijloc de cuvant (`GrupFin&ElemTeorGal` + `ois`) si la un spatiu
    real (`SpecTopicsAI [sapt` + `1-5]`). Din pixeli nu se vede care din doua a fost, asa ca
    le incercam pe amandoua si pastram varianta care da un termen cunoscut.
    """
    texte = [b.text.strip() for b in bucati]
    if len(texte) == 1:
        return texte
    return ["".join(texte), " ".join(texte)]


def _cea_mai_buna_reunire(
    inainte: list[BucataText], act: ActivitateCitita, lexicon: Lexicon
) -> list[BucataText]:
    """Ultimul grup dinaintea ancorei, extins spre stanga cat timp asta ajuta.

    Extinderea se accepta **numai** daca produce un termen din vocabular: conditia se
    verifica singura, deci nu poate lipi bucati care n-au ce cauta impreuna.
    """
    ales = inainte[-1:]
    if lexicon.materie(_fara_saptamani(act, ales[0].text, doar_test=True)).din_vocabular:
        return ales
    for k in range(2, min(len(inainte), 4) + 1):
        candidat = inainte[-k:]
        for text in _variante_text(candidat):
            if lexicon.materie(_fara_saptamani(act, text, doar_test=True)).din_vocabular:
                return candidat
    return ales


def _blocul_centrat(ramase: list[BucataText], celula: Celula) -> list[BucataText]:
    """Grupurile centrate in celula.

    Fara paranteza de tip nu exista semn structural, dar exista unul de asezare: aSc scrie
    profesorul aliniat la stanga, sala la dreapta si **materia centrata**. Criteriul e deci
    egalitatea marginilor, nu simpla indentare: cand materia e o propozitie lunga
    (`Seminar .hidden [25 mar, 29 apr,` pe pagina de conferinte) umple aproape toata latimea
    si incepe la 6 px de margine -- indentata n-ar parea, centrata este.
    """
    cx0, _, cx1, _ = celula.bbox
    centrate = []
    for b in ramase:
        if b.rand == 0:
            continue  # randul 0 e al profesorului
        inaltime = max(1, b.bbox[3] - b.bbox[1])
        stanga, dreapta = b.x0 - cx0, cx1 - b.x1
        if abs(stanga - dreapta) <= max(10, inaltime * 0.6):
            centrate.append(b)
    return sorted(centrate, key=lambda b: (b.rand, b.x0))


def _fara_saptamani(act: ActivitateCitita, text: str, *, doar_test: bool = False) -> str:
    """Scoate `[...]` din materie si, daca nu doar testam, il retine ca saptamani."""
    m = _RE_SAPT.search(text)
    if not m:
        return text.strip()
    if not doar_test and not act.saptamani:
        act.saptamani = _curata_saptamani(m.group("sapt"))
    return (text[: m.start()] + text[m.end() :]).strip()


def _curata_saptamani(text: str) -> str:
    """`sapt1-5` -> `sapt 1-5`.

    Cand `[sapt 1-5]` se rupe la capat de rand exact la spatiu, reunirea fara spatiu e cea
    care da materia corecta, dar lipeste eticheta de numar. Forma canonica e cunoscuta.
    """
    return re.sub(r"\bsapt(?=\d)", "sapt ", text.strip(), flags=re.IGNORECASE)


def _extrage_sala(
    act: ActivitateCitita, ramase: list[BucataText], lexicon: Lexicon, scoruri: list[float]
) -> BucataText | None:
    """Grupul care se potriveste cel mai bine cu o sala cunoscuta.

    Intoarce grupul consumat, sau None cand niciunul nu trece pragul vocabularului.
    """
    potriviri = [(b, lexicon.sala(b.text)) for b in ramase]
    # Sala sta la dreapta si jos; la scor egal, alegem grupul cel mai din dreapta-jos.
    candidat = max(
        (bp for bp in potriviri if bp[1].din_vocabular),
        key=lambda bp: (bp[1].scor, bp[0].rand, bp[0].x0),
        default=None,
    )
    if candidat is None:
        return None
    bucata, potrivire = candidat
    act.sala = potrivire.valoare
    scoruri.append(bucata.scor)
    return bucata


def _extrage_profesor(
    act: ActivitateCitita, ramase: list[BucataText], lexicon: Lexicon, scoruri: list[float]
) -> None:
    """Ce a mai ramas e profesorul -- pe mai multe randuri cand sunt mai multi."""
    ramase = [b for b in ramase if not _RE_NUMAR.match(b.text.strip())]
    if not ramase:
        return
    ramase.sort(key=lambda b: (b.rand, b.x0))
    # Lipim cu spatiu, nu cu `/`: separatorul dintre profesori e deja scris in orar, iar
    # grupurile de aici sunt bucati ale aceluiasi text, rupte de latimea celulei
    # (`Iftimie S / Tazlaoanu` + `C`). Impartirea pe profesori o face lexiconul, pe `/`.
    p = lexicon.profesor(" ".join(b.text.strip() for b in ramase))
    act.profesor = p.valoare
    scoruri.append(min(b.scor for b in ramase))
    if not p.din_vocabular:
        act.nesigure.append("profesor")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def citeste_pagina(
    imagine: Image.Image | np.ndarray,
    pagina: PaginaSegmentata,
    motor: MotorRecunoastere,
    lexicon: Lexicon,
) -> PaginaCitita:
    """Citeste textul unei pagini deja segmentate."""
    im = imagine if isinstance(imagine, Image.Image) else Image.fromarray(imagine)
    if im.mode != "RGB":
        im = im.convert("RGB")
    # Segmentarea a calculat deja masca de text, cu acelasi prag; o refolosim.
    negru = pagina.negru
    if negru is None or negru.shape != (im.height, im.width):
        negru = masca_negru(np.asarray(im))

    # Un singur lot pentru toata pagina: recunoasterea e de departe partea scumpa, iar
    # rapidocr o face pe loturi de decupaje de latime asemanatoare.
    plan: list[tuple[Celula, BucataText]] = []
    for celula in pagina.celule:
        x0, y0, x1, y1 = celula.bbox
        m = MARJA_CELULA
        sub = negru[y0 + m : y1 - m, x0 + m : x1 - m]
        if sub.size == 0:
            continue
        for bucata in decupeaza_text(sub, (x0 + m, y0 + m)):
            plan.append((celula, bucata))

    decupaje = [_decupaj(im, b.bbox) for _, b in plan]
    citite = motor.recunoaste(decupaje)

    pe_celula: dict[int, list[BucataText]] = {}
    for (celula, bucata), (text, scor) in zip(plan, citite, strict=True):
        pe_celula.setdefault(id(celula), []).append(
            BucataText(bucata.bbox, bucata.rand, text, scor)
        )

    rezultat = PaginaCitita()
    for celula in pagina.celule:
        bucati = pe_celula.get(id(celula), [])
        if not bucati:
            rezultat.avertismente.append(f"{celula.zi} {celula.ore}: celula fara text")
            continue
        rezultat.activitati.append(_atribuie(celula, bucati, lexicon))

    if pagina.bbox_titlu:
        rezultat.titlu, rezultat.scor_titlu = _citeste_titlu(im, negru, pagina, motor)
    return rezultat


def _citeste_titlu(
    im: Image.Image, negru: np.ndarray, pagina: PaginaSegmentata, motor: MotorRecunoastere
) -> tuple[str, float]:
    """Titlul de deasupra tabelului, pe un singur rand."""
    x0, y0, x1, y1 = pagina.bbox_titlu or (0, 0, 0, 0)
    if y1 - y0 < 8:
        return "", 0.0
    sub = negru[y0:y1, x0:x1]
    randuri = _randuri(sub)
    if not randuri:
        return "", 0.0
    # Titlul e randul cel mai inalt din zona; deasupra lui aSc pune uneori antetul paginii.
    ry0, ry1 = max(randuri, key=lambda r: r[1] - r[0])
    coloane = _intervale(sub[ry0 : ry1 + 1].any(axis=0), gol_maxim=0)
    if not coloane:
        return "", 0.0
    cutie = (x0 + coloane[0][0], y0 + ry0, x0 + coloane[-1][1] + 1, y0 + ry1 + 1)
    text, scor = motor.recunoaste([_decupaj(im, cutie)])[0]
    return text.strip(), scor


def _decupaj(im: Image.Image, bbox: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = bbox
    p = PERNA
    cutie = (max(0, x0 - p), max(0, y0 - p), min(im.width, x1 + p), min(im.height, y1 + p))
    return np.asarray(im.crop(cutie))


def citeste_fisier(cale: Path | str, motor: MotorRecunoastere, lexicon: Lexicon) -> PaginaCitita:
    """Segmenteaza si citeste o pagina de pe disc."""
    with Image.open(cale) as im:
        rgb = im.convert("RGB")
        return citeste_pagina(rgb, segmenteaza(rgb), motor, lexicon)
