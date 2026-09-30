"""Segmentarea geometrica a unei pagini de orar: imagine -> dreptunghiuri de activitate.

Etapa asta nu citeste niciun text. Raspunde doar la "unde sunt celulele si ce interval
orar acopera fiecare" -- si o face din pura geometrie, deci **exact**, nu aproximativ.
OCR-ul (Etapa 5) primeste apoi decupaje deja delimitate.

Ideea centrala: pagina are doua **rigle mereu curate**, pe care nicio activitate nu le
acopera niciodata:

    rigla orizontala = randul de antet (8, 9, 10 ... 19), sus
    rigla verticala  = coloana cu numele zilelor (Lu/Mon, Ma/Tue ...), stanga

Liniile despartitoare din interiorul grilei sunt adesea acoperite de celule colorate, deci
nedetectabile; cele din rigle nu sunt. Masuram deci caroiajul pe rigle si il extindem peste
toata grila. Asa segmentarea merge la orice rezolutie, fara pixeli hardcodati.

    +----+---------------------------------------+
    | ⇕  |  8 | 9 | 10 | ... | 19    <- rigla orizontala
    +----+---------------------------------------+
    | Lu |                                       |
    +----+   celulele colorate = activitati      |
    | Ma |                                       |
    | ⇑ rigla verticala                          |
    +----+---------------------------------------+

Vezi `docs/formatul-orarului.md` pentru semantica paginii.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

__all__ = [
    "Caroiaj",
    "Celula",
    "PaginaSegmentata",
    "EroareSegmentare",
    "detecteaza_caroiaj",
    "segmenteaza",
    "segmenteaza_fisier",
    "ZILE",
    "ORA_MIN",
]

ZILE = ("Luni", "Marti", "Miercuri", "Joi", "Vineri")
ORA_MIN = 8
NR_ZILE = len(ZILE)
NR_ORE = 12

#: Praguri de clasificare a unui pixel. Pagina e sintetica (PDF vectorial randat), deci
#: fundalul e alb curat si textul negru curat -- pragurile nu sunt critice.
PRAG_ALB = 235
PRAG_NEGRU = 110


class EroareSegmentare(RuntimeError):
    """Pagina nu arata a orar (cuprins, coperta, layout schimbat)."""


@dataclass(frozen=True)
class Caroiaj:
    """Caroiajul masurat pe o pagina, in pixeli."""

    #: Marginile x ale celor 12 coloane orare: 13 valori.
    coloane: tuple[int, ...]
    #: Marginile y ale celor 5 randuri de zi: 6 valori.
    randuri: tuple[int, ...]
    #: Regiunea antetului (deasupra primului rand de zi).
    antet: tuple[int, int]
    #: Coloana cu numele zilelor.
    eticheta_x: tuple[int, int]

    @property
    def latime_coloana(self) -> float:
        return (self.coloane[-1] - self.coloane[0]) / NR_ORE

    def ora_din_coloana(self, index: int) -> int:
        return ORA_MIN + index


@dataclass(frozen=True)
class Celula:
    """O activitate, delimitata geometric. Fara text -- acela vine din OCR."""

    zi: str
    #: Indexul primei coloane orare acoperite (0 = ora 8).
    col_start: int
    col_span: int
    #: Banda pe verticala in cadrul zilei (0 = cea de sus).
    banda: int
    #: (x0, y0, x1, y1) in pixeli, pentru decupaj.
    bbox: tuple[int, int, int, int]
    #: Culoarea de umplere, folosita la separarea celulelor vecine.
    culoare: tuple[int, int, int]

    @property
    def ora_inceput(self) -> int:
        return ORA_MIN + self.col_start

    @property
    def ora_sfarsit(self) -> int:
        return ORA_MIN + self.col_start + self.col_span

    @property
    def ore(self) -> str:
        """Formatul din `date.json`, ex. "14-17"."""
        return f"{self.ora_inceput}-{self.ora_sfarsit}"


@dataclass
class PaginaSegmentata:
    caroiaj: Caroiaj
    celule: list[Celula] = field(default_factory=list)
    #: Regiunea titlului, deasupra tabelului (de dat la OCR pentru numele formatiunii).
    bbox_titlu: tuple[int, int, int, int] | None = None
    avertismente: list[str] = field(default_factory=list)
    #: Masca de text a paginii, calculata deja la segmentare; OCR-ul o refoloseste.
    negru: np.ndarray | None = field(default=None, repr=False, compare=False)

    def pe_zi(self, zi: str) -> list[Celula]:
        return [c for c in self.celule if c.zi == zi]


# ---------------------------------------------------------------------------
# Clasificarea pixelilor
# ---------------------------------------------------------------------------


def masca_negru(img: np.ndarray) -> np.ndarray:
    """Pixelii de text/chenar: toate cele trei canale sub `PRAG_NEGRU`.

    Canal cu canal, nu cu `.all(axis=2)`: reducerea pe ultima axa, de lungime 3, merge
    element cu element si e de ~10x mai lenta pe o pagina de 7 milioane de pixeli.
    """
    return (img[..., 0] <= PRAG_NEGRU) & (img[..., 1] <= PRAG_NEGRU) & (img[..., 2] <= PRAG_NEGRU)


def _linii(profil: np.ndarray, prag: float, toleranta: int = 3) -> list[int]:
    """Indicii unde profilul depaseste pragul, grupati in linii unice."""
    indici = np.flatnonzero(profil > prag)
    if indici.size == 0:
        return []
    grupuri: list[int] = []
    curent = [int(indici[0])]
    for i in indici[1:]:
        if i - curent[-1] <= toleranta:
            curent.append(int(i))
        else:
            grupuri.append(sum(curent) // len(curent))
            curent = [int(i)]
    grupuri.append(sum(curent) // len(curent))
    return grupuri


# ---------------------------------------------------------------------------
# Caroiajul
# ---------------------------------------------------------------------------


def _intindere_linie(negru: np.ndarray, y: int, gol_maxim: int = 6) -> tuple[int, int]:
    """(x0, x1) al celui mai lung segment negru continuu de pe randul y."""
    rand = negru[y]
    cel_mai_bun = (0, 0)
    start = None
    gol = 0
    for x in range(rand.size):
        if rand[x]:
            if start is None:
                start = x
            gol = 0
        elif start is not None:
            gol += 1
            if gol > gol_maxim:
                if x - gol - start > cel_mai_bun[1] - cel_mai_bun[0]:
                    cel_mai_bun = (start, x - gol)
                start = None
                gol = 0
    if start is not None and rand.size - start > cel_mai_bun[1] - cel_mai_bun[0]:
        cel_mai_bun = (start, rand.size)
    return cel_mai_bun


def detecteaza_caroiaj(img: np.ndarray, negru: np.ndarray | None = None) -> Caroiaj:
    """Masoara caroiajul, folosind riglele curate ale paginii.

    Arunca `EroareSegmentare` daca pagina nu are structura asteptata -- mai bine oprim
    ingestul decat sa producem date inventate dintr-o pagina de cuprins.
    """
    inaltime, latime = img.shape[:2]
    if negru is None:
        negru = masca_negru(img)

    profil_h = negru.sum(axis=1).astype(float) / latime
    orizontale = _linii(profil_h, prag=0.55)
    if len(orizontale) < 2:
        raise EroareSegmentare("nu am gasit chenarul tabelului")

    # Marginile tabelului nu se ghicesc din pozitie, ci din *intinderea* liniilor lui.
    # Unele randari au si un chenar de pagina, iar unele pagini sunt decupate strambe;
    # in schimb liniile tabelului (chenar sus, separator de antet, chenar jos) au toate
    # exact aceeasi intindere pe orizontala, deci formeaza cel mai numeros grup.
    grupuri: dict[tuple[int, int], list[int]] = {}
    for y in orizontale:
        x0, x1 = _intindere_linie(negru, y)
        if x1 - x0 < latime * 0.5:
            continue
        for (gx0, gx1), membri in grupuri.items():
            if abs(gx0 - x0) <= 12 and abs(gx1 - x1) <= 12:
                membri.append(y)
                break
        else:
            grupuri[(x0, x1)] = [y]

    if not grupuri:
        raise EroareSegmentare("nu am gasit liniile tabelului")

    (stanga, dreapta), linii_tabel = max(grupuri.items(), key=lambda kv: (len(kv[1]), kv[0][0]))
    linii_tabel = sorted(linii_tabel)
    if len(linii_tabel) < 2:
        raise EroareSegmentare("tabelul are prea putine linii orizontale")

    sus, jos = linii_tabel[0], linii_tabel[-1]
    antet_jos = linii_tabel[1]
    if antet_jos >= jos:
        raise EroareSegmentare("nu am gasit separatorul de antet")

    banda_antet = negru[sus + 2 : antet_jos - 2, :]
    if banda_antet.size == 0:
        raise EroareSegmentare("antet gol")
    profil_antet = banda_antet.sum(axis=0).astype(float) / banda_antet.shape[0]
    margini = [x for x in _linii(profil_antet, prag=0.5) if stanga <= x <= dreapta]

    coloane = _alege_coloane(margini, stanga, dreapta)
    if coloane[0] - stanga < 8:
        raise EroareSegmentare("nu am separat coloana zilelor de grila")

    # --- 3. rigla verticala (coloana zilelor): da marginile celor 5 randuri ---
    eticheta_x = (stanga, coloane[0])
    banda_eticheta = negru[:, stanga + 2 : coloane[0] - 2]
    if banda_eticheta.size == 0:
        raise EroareSegmentare("coloana de zile goala")
    profil_eticheta = banda_eticheta.sum(axis=1).astype(float) / banda_eticheta.shape[1]
    linii_zi = [y for y in _linii(profil_eticheta, prag=0.5) if antet_jos - 4 <= y <= jos + 4]

    randuri = _alege_randuri(linii_zi, antet_jos, jos)

    return Caroiaj(
        coloane=tuple(coloane),
        randuri=tuple(randuri),
        antet=(sus, antet_jos),
        eticheta_x=eticheta_x,
    )


def _alege_coloane(margini: list[int], stanga: int, dreapta: int) -> list[int]:
    """13 margini pentru 12 coloane orare.

    Printre marginile gasite in antet e si chenarul din stanga al tabelului, care **nu** e
    granita de coloana orara: intre el si prima ora e coloana cu numele zilelor. Il scoatem
    dupa distanta fata de `stanga`, nu dupa egalitate stricta -- la randare mare linia are
    doi-trei pixeli si centrul ei cade langa chenar, nu pe el, iar `m > stanga` o lasa sa
    treaca si strive coloana zilelor la latime zero.

    Ce ramane, daca detectia a gasit tot, sunt exact cele 13 margini. Cand aSc deseneaza o
    linie peste care cade text de antet si nu o prindem, cadem pe pas uniform intre prima
    margine si chenarul din dreapta: coloanele sunt perfect egale (verificat: pas exact de
    120 px la 1610 px latime de pagina, 240 px la 3200).
    """
    marja = max(4, int((dreapta - stanga) * 0.02))
    candidati = [m for m in margini if m > stanga + marja]
    if len(candidati) >= NR_ORE + 1:
        alese = candidati[-(NR_ORE + 1) :]
        pasi = np.diff(alese)
        if pasi.std() <= max(1.5, pasi.mean() * 0.06):
            return [int(x) for x in alese]

    grila_stanga = candidati[0] if candidati else stanga
    pas = (dreapta - grila_stanga) / NR_ORE
    return [int(round(grila_stanga + i * pas)) for i in range(NR_ORE + 1)]


def _alege_randuri(linii: list[int], sus: int, jos: int) -> list[int]:
    """6 margini pentru 5 randuri de zi."""
    candidati = sorted({sus, *[x for x in linii if sus < x < jos], jos})
    if len(candidati) == NR_ZILE + 1:
        return candidati
    pas = (jos - sus) / NR_ZILE
    return [int(round(sus + i * pas)) for i in range(NR_ZILE + 1)]


# ---------------------------------------------------------------------------
# Celulele
# ---------------------------------------------------------------------------

#: Cat din latimea unei coloane ignoram la margini, ca sa nu prindem chenarul.
INSET = 0.15
#: Cat de mult trebuie sa difere doua culori de umplere ca sa fie celule diferite.
TOLERANTA_CULOARE = 24
#: Cat de intunecata fata de vecini trebuie sa fie o linie ca sa fie chenar desenat.
#: Chenarele nu sunt negre curate: la rezolutia de randare o linie de 1 px se amesteca
#: cu umplerile din jur si iese, de exemplu, (87,142,87) intre doua verzuri.
ADANCIME_CHENAR = 26
#: Sub atata text, banda e slot gol.
PRAG_TEXT = 0.02


@dataclass
class _Banda:
    """O bucata verticala dintr-o coloana orara, cu umplere uniforma."""

    col: int
    y0: int
    y1: int
    culoare: tuple[int, int, int]
    plina: bool


def _apropiate(
    a: tuple[int, int, int], b: tuple[int, int, int], toleranta: int = TOLERANTA_CULOARE
) -> bool:
    return all(abs(x - y) <= toleranta for x, y in zip(a, b, strict=True))


def _este_alb(c: tuple[int, int, int]) -> bool:
    return min(c) >= PRAG_ALB


def _culori_pe_linii(
    img: np.ndarray, negru: np.ndarray, y0: int, y1: int, x0: int, x1: int
) -> list[tuple[int, int, int] | None]:
    """Culoarea de umplere a fiecarei scanlinii din [y0, y1), ignorand pixelii de text.

    None pentru liniile aproape numai text/chenar, care nu spun nimic despre umplere.

    Culoarea e moda pe linie a pixelilor cuantizati la 8 niveluri. O calculam pentru tot
    blocul deodata -- sortare pe randuri, apoi lungimea fiecarei serii de valori egale --
    in loc de un `np.unique` pe fiecare linie: o pagina are ~20000 de linii de coloana.
    La egalitate castiga codul cel mai mic, exact ca `np.unique` + `argmax`.
    """
    bloc = img[y0:y1, x0:x1] // 8 * 8
    cod = (
        (bloc[..., 0].astype(np.int32) << 16)
        | (bloc[..., 1].astype(np.int32) << 8)
        | bloc[..., 2].astype(np.int32)
    )
    text = negru[y0:y1, x0:x1]
    cod[text] = -1  # sub orice culoare, deci ajunge la inceputul randului sortat
    cod.sort(axis=1)

    idx = np.arange(cod.shape[1])
    inceput_serie = np.ones(cod.shape, dtype=bool)
    inceput_serie[:, 1:] = cod[:, 1:] != cod[:, :-1]
    start = np.maximum.accumulate(np.where(inceput_serie, idx, 0), axis=1)
    lungime = idx - start + 1
    lungime[cod < 0] = 0
    # Prima pozitie cu lungimea maxima e capatul primei serii (deci a celui mai mic cod)
    # care atinge maximul.
    moda = cod[np.arange(cod.shape[0]), lungime.argmax(axis=1)]
    destul = (~text).sum(axis=1) >= max(4, (x1 - x0) * 0.2)

    return [
        (int(c) >> 16 & 0xFF, int(c) >> 8 & 0xFF, int(c) & 0xFF) if ok else None
        for c, ok in zip(moda, destul, strict=True)
    ]


def _seam_orizontal(lum: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> np.ndarray:
    """Pentru fiecare rand din [y0, y1): e o linie desenata, sau doar un rand de text?

    Un rand de text e si el mai intunecat decat vecinii lui pe medie, deci media nu
    separa nimic. Ce le deosebeste e **continuitatea**: o linie trasa intuneca *fiecare*
    pixel de pe latime, pe cand textul lasa umplerea la vedere intre litere.
    """
    inaltime = lum.shape[0]
    ys = np.arange(y0, y1)
    in_pagina = (ys - 3 >= 0) & (ys + 3 < inaltime)
    aici = lum[y0:y1, x0:x1]
    vecini = np.minimum(
        lum[np.clip(ys - 3, 0, inaltime - 1), x0:x1],
        lum[np.clip(ys + 3, 0, inaltime - 1), x0:x1],
    )
    return in_pagina & ((aici < vecini - ADANCIME_CHENAR).mean(axis=1) >= 0.90)


def _tranzitie_orizontala(
    img: np.ndarray, negru: np.ndarray, x0: int, x1: int, y: int, delta: int = 4
) -> bool:
    """Schimbarea de umplere de la randul y e o granita dreapta, sau o diagonala?

    La o granita adevarata, umplerea difera deasupra fata de dedesubt pe **toata** latimea.
    La fundalul in diagonala pe care il deseneaza aSc, la orice y doar o mica parte din
    latime se schimba -- restul e aceeasi culoare si sus si jos. Asa deosebim sfarsitul
    unei activitati de decorul din interiorul ei.

    Pixelii de text se exclud din comparatie: doua randuri la distanta de cateva linii cad
    des unul pe litere si celalalt pe fundal, iar diferenta ar parea o granita.
    """
    if y - delta < 0 or y + delta >= img.shape[0]:
        return False
    valid = ~negru[y - delta, x0:x1] & ~negru[y + delta, x0:x1]
    if int(valid.sum()) < max(6, (x1 - x0) * 0.4):
        return False
    sus = img[y - delta, x0:x1][valid].astype(np.int16)
    jos = img[y + delta, x0:x1][valid].astype(np.int16)
    difera = np.abs(sus - jos).max(axis=1) > TOLERANTA_CULOARE
    return bool(difera.mean() >= 0.80)


def _exista_separator(lum: np.ndarray, negru: np.ndarray, x: int, y0: int, y1: int) -> bool:
    """E un chenar vertical desenat la x, pe intervalul [y0, y1)?

    Ca si la orizontala, chenarul e un minim local pe latime, nu neaparat negru. Verificam
    si continuitatea pe verticala: doua randuri de text care trec peste granita produc
    pixeli intunecati, dar imprastiati, nu o linie.
    """
    if y1 - y0 < 6 or x - 4 < 0 or x + 4 >= lum.shape[1]:
        return False
    coloana = lum[y0:y1, x - 1 : x + 2].min(axis=1).astype(float)
    stanga = lum[y0:y1, x - 5 : x - 2].mean(axis=1).astype(float)
    dreapta = lum[y0:y1, x + 3 : x + 6].mean(axis=1).astype(float)
    referinta = np.minimum(stanga, dreapta)
    intunecat = coloana < referinta - ADANCIME_CHENAR
    return bool(intunecat.mean() >= 0.75)


def _benzi_din_coloana(
    img: np.ndarray,
    lum: np.ndarray,
    negru: np.ndarray,
    col: int,
    x0: int,
    x1: int,
    y0: int,
    y1: int,
) -> Iterator[_Banda]:
    """Imparte o coloana orara in benzi de umplere uniforma.

    Granita dintre doua activitati suprapuse e fie o schimbare de culoare, fie -- cand se
    nimeresc de aceeasi culoare -- doar chenarul subtire dintre ele. Le tratam pe amandoua.

    O banda conteaza ca ocupata daca are umplere colorata **sau** text: aSc deseneaza unele
    activitati cu fundal in diagonala, jumatate alb, iar dupa culoare ar parea slot gol
    desi are profesor, materie si sala scrise in ea.
    """
    culori = _culori_pe_linii(img, negru, y0 + 1, y1, x0, x1)

    # Taiem unde e chenar desenat, sau unde umplerea se schimba pe toata latimea. A doua
    # conditie prinde celulele vecine al caror chenar comun s-a pierdut la randare; testul
    # de "pe toata latimea" o impiedica sa taie in interiorul fundalurilor in diagonala.
    granite: list[int] = [0]
    for i in np.flatnonzero(_seam_orizontal(lum, x0, x1, y0 + 1, y1)):
        if i - granite[-1] >= 6:
            granite.append(int(i))
    granite.append(len(culori))

    for a, b in zip(granite, granite[1:], strict=False):
        if b - a < 8:
            continue
        ya, yb = y0 + 1 + a, y0 + 1 + b
        interior = [c for c in culori[a:b] if c is not None]
        if not interior:
            continue
        culoare = max(set(interior), key=interior.count)
        # Textul se masoara *fara* marginile benzii: chenarele celulelor vecine se scurg
        # cateva randuri inauntru si ar face un slot gol sa para ca are continut.
        # Masurat: gol => 0.000, celula cu fundal in diagonala => 0.08+.
        marja = 3
        frac_text = (
            float(negru[ya + marja : yb - marja, x0:x1].mean()) if yb - ya > 2 * marja else 0.0
        )
        plina = not _este_alb(culoare) or frac_text > PRAG_TEXT
        yield _Banda(col=col, y0=ya, y1=yb, culoare=culoare, plina=plina)


def _grupeaza_in_celule(
    benzi: list[_Banda], caroiaj: Caroiaj, lum: np.ndarray, negru: np.ndarray, zi: str
) -> list[Celula]:
    """Uneste benzile alaturate care fac parte din aceeasi celula.

    Doua coloane vecine apartin aceleiasi activitati daca au aceleasi granite pe verticala,
    umplere compatibila si **niciun** chenar desenat intre ele. "Compatibila" include si
    cazul in care una dintre jumatati e alba: asa arata celulele cu fundal in diagonala.
    """
    # Ordinea conteaza: crestem celulele numai spre dreapta, deci trebuie sa pornim
    # intotdeauna din coloana cea mai din stanga. Sortate dupa y, doua coloane ale
    # aceleiasi celule ale caror margini difera cu un pixel ar veni in ordine inversa,
    # iar cea din dreapta -- consumata prima -- nu s-ar mai putea uni cu vecina din stanga.
    pline = sorted((b for b in benzi if b.plina), key=lambda b: (b.col, b.y0))
    folosite: set[int] = set()
    celule: list[Celula] = []

    for i, b in enumerate(pline):
        if i in folosite:
            continue
        folosite.add(i)
        col_start = col_end = b.col
        y0, y1 = b.y0, b.y1
        culoare = b.culoare

        avansat = True
        while avansat:
            avansat = False
            for j, t in enumerate(pline):
                if j in folosite or t.col != col_end + 1:
                    continue
                if abs(t.y0 - y0) > 4 or abs(t.y1 - y1) > 4:
                    continue
                # Culoarea nu intra in decizie: aSc umple unele celule cu un fundal in
                # doua tonuri, taiat in diagonala, deci doua jumatati ale *aceleiasi*
                # celule pot avea culori dominante complet diferite. Singurul semn ca s-a
                # terminat o activitate si a inceput alta e chenarul desenat intre ele.
                if _exista_separator(lum, negru, caroiaj.coloane[col_end + 1], y0 + 3, y1 - 2):
                    continue
                folosite.add(j)
                col_end += 1
                y0, y1 = min(y0, t.y0), max(y1, t.y1)
                if _este_alb(culoare):
                    culoare = t.culoare
                avansat = True

        celule.append(
            Celula(
                zi=zi,
                col_start=col_start,
                col_span=col_end - col_start + 1,
                banda=0,  # se completeaza dupa ce stim toate celulele zilei
                bbox=(caroiaj.coloane[col_start], y0, caroiaj.coloane[col_end + 1], y1),
                culoare=culoare,
            )
        )

    return celule


def _atribuie_benzi(celule: list[Celula]) -> list[Celula]:
    """Numeroteaza benzile pe verticala, in ordinea aparitiei de sus in jos."""
    if not celule:
        return []
    praguri = sorted({c.bbox[1] for c in celule})
    grupuri: list[int] = []
    for p in praguri:
        if grupuri and p - grupuri[-1] <= 6:
            continue
        grupuri.append(p)

    rezultat = []
    for c in celule:
        banda = max(i for i, g in enumerate(grupuri) if g <= c.bbox[1] + 6)
        rezultat.append(
            Celula(
                zi=c.zi,
                col_start=c.col_start,
                col_span=c.col_span,
                banda=banda,
                bbox=c.bbox,
                culoare=c.culoare,
            )
        )
    return sorted(rezultat, key=lambda c: (c.banda, c.col_start))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def segmenteaza(imagine: Image.Image | np.ndarray) -> PaginaSegmentata:
    """Segmenteaza o pagina de orar in celule de activitate."""
    if isinstance(imagine, Image.Image):
        img = np.asarray(imagine if imagine.mode == "RGB" else imagine.convert("RGB"))
    else:
        img = imagine
    if img.ndim != 3 or img.shape[2] != 3:
        raise EroareSegmentare("astept o imagine RGB")

    negru = masca_negru(img)
    caroiaj = detecteaza_caroiaj(img, negru)
    # = img.mean(axis=2), bit cu bit (suma a trei uint8 e exacta), dar fara reducerea
    # lenta pe axa culorilor.
    lum = (img[..., 0].astype(np.uint16) + img[..., 1] + img[..., 2]) / 3.0

    celule: list[Celula] = []
    avertismente: list[str] = []

    for d, zi in enumerate(ZILE):
        y0, y1 = caroiaj.randuri[d], caroiaj.randuri[d + 1]
        benzi: list[_Banda] = []
        for col in range(NR_ORE):
            cx0, cx1 = caroiaj.coloane[col], caroiaj.coloane[col + 1]
            inset = max(2, int((cx1 - cx0) * INSET))
            benzi.extend(_benzi_din_coloana(img, lum, negru, col, cx0 + inset, cx1 - inset, y0, y1))
        celule.extend(_atribuie_benzi(_grupeaza_in_celule(benzi, caroiaj, lum, negru, zi)))

    return PaginaSegmentata(
        caroiaj=caroiaj,
        celule=celule,
        bbox_titlu=(0, 0, img.shape[1], caroiaj.antet[0]),
        avertismente=avertismente,
        negru=negru,
    )


def segmenteaza_fisier(cale: Path | str) -> PaginaSegmentata:
    with Image.open(cale) as im:
        return segmenteaza(im)
