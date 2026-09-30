"""Segmentarea unui tabel cu linii trasate: imagine -> celule.

Alt fel de tabel decat orarul
-----------------------------
Grila orarului n-are linii interioare vizibile -- de aceea `segment.py` deduce caroiajul din
riglele curate de pe margini. Tabelul de capacitati de pe pagina 2 e opusul: **toate** liniile
sunt trasate, dar nu se stie dinainte cate randuri sau coloane are, si pe aceeasi pagina stau
doua tabele alaturate:

    +----------------+-----------+        +------------------+-----------+
    | Sala PBT       | Nr. locuri|        | Laborator PBT    | Nr. locuri|
    +----------------+-----------+        +------------------+-----------+
    | Amf. 501       |    122    |        | L.410            |    15     |
    ...                                   ...

Deci nu putem folosi proiectii pe toata latimea: liniile unui tabel nu ajung la celalalt.
Cautam in schimb **segmente** -- rulaje lungi de pixeli intunecati pe un rand sau pe o
coloana -- si grupam segmentele orizontale dupa intinderea lor pe x. Fiecare grup e un tabel;
verticalele care il traverseaza ii dau coloanele.

Ce **nu** face: nu presupune nimic despre continut. Ce scrie in celule decide apelantul.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

__all__ = ["Tabel", "Celula", "detecteaza_tabele", "citeste_tabel", "EroareTabel"]

#: Cat de intunecat trebuie sa fie un pixel ca sa faca parte dintr-o linie trasata.
PRAG_NEGRU = 110
#: Sub atatia pixeli, un rulaj continuu de intuneric nu e linie de tabel. Textul nu poate
#: produce asa ceva -- are goluri intre litere -- deci pragul separa curat. Il tinem jos ca
#: sa mearga si tabelele mici: coloanele unui tabel de trei randuri au ~180 px.
LUNGIME_MINIMA = 120
#: Linii mai apropiate de atat sunt aceeasi linie, ingrosata la randare.
TOLERANTA = 5
#: Cat ignoram din marginea celulei, ca sa nu prindem chenarul in decupaj.
MARJA = 4


class EroareTabel(RuntimeError):
    """Pagina nu contine tabelul asteptat."""


@dataclass(frozen=True)
class Celula:
    rand: int
    coloana: int
    #: (x0, y0, x1, y1) in pixeli.
    bbox: tuple[int, int, int, int]
    text: str = ""


@dataclass(frozen=True)
class Tabel:
    """Un tabel cu linii trasate: pozitiile liniilor, in pixeli."""

    #: y-ul fiecarei linii orizontale; n linii => n-1 randuri.
    randuri: tuple[int, ...]
    #: x-ul fiecarei linii verticale.
    coloane: tuple[int, ...]

    @property
    def nr_randuri(self) -> int:
        return max(0, len(self.randuri) - 1)

    @property
    def nr_coloane(self) -> int:
        return max(0, len(self.coloane) - 1)

    def celule(self) -> Iterator[Celula]:
        for i in range(self.nr_randuri):
            for j in range(self.nr_coloane):
                yield Celula(
                    rand=i,
                    coloana=j,
                    bbox=(
                        self.coloane[j],
                        self.randuri[i],
                        self.coloane[j + 1],
                        self.randuri[i + 1],
                    ),
                )


# ---------------------------------------------------------------------------
# Detectia liniilor
# ---------------------------------------------------------------------------


def _segmente(masca: np.ndarray, minim: int) -> list[tuple[int, int, int]]:
    """(indice, start, stop) pentru fiecare rulaj de cel putin `minim` pixeli."""
    out: list[tuple[int, int, int]] = []
    for i in range(masca.shape[0]):
        d = np.diff(np.concatenate(([0], masca[i].view(np.int8), [0])))
        for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1), strict=True):
            if b - a >= minim:
                out.append((i, int(a), int(b)))
    return out


def _uneste(segmente: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """Linia trasata are cativa pixeli grosime; o reducem la una singura."""
    out: list[tuple[int, int, int]] = []
    for i, a, b in sorted(segmente):
        if (
            out
            and i - out[-1][0] <= TOLERANTA
            and abs(a - out[-1][1]) <= TOLERANTA
            and abs(b - out[-1][2]) <= TOLERANTA
        ):
            continue  # aceeasi linie, alt rand de pixeli
        out.append((i, a, b))
    return out


def detecteaza_tabele(
    imagine: Image.Image | np.ndarray, *, minim: int = LUNGIME_MINIMA
) -> list[Tabel]:
    """Toate tabelele cu linii trasate din imagine, in ordinea de pe pagina.

    Un tabel = un grup de linii orizontale cu aceeasi intindere pe x. Asa cele doua tabele
    alaturate de pe pagina 2 ies separat, fara sa presupunem cate sunt.
    """
    img = np.asarray(imagine.convert("RGB")) if isinstance(imagine, Image.Image) else imagine
    negru = (img <= PRAG_NEGRU).all(axis=2)

    orizontale = _uneste(_segmente(negru, minim))
    verticale = _uneste(_segmente(np.ascontiguousarray(negru.T), minim))

    tabele: list[Tabel] = []
    for (x0, x1), linii in _grupeaza_pe_intindere(orizontale).items():
        if len(linii) < 3:
            continue  # doua linii nu fac tabel, fac un chenar
        sus, jos = min(linii), max(linii)
        coloane = sorted(
            {
                x
                for x, y0, y1 in verticale
                # Verticala trebuie sa traverseze tabelul, nu doar sa-l atinga.
                if y0 <= sus + TOLERANTA
                and y1 >= jos - TOLERANTA
                and x0 - TOLERANTA <= x <= x1 + TOLERANTA
            }
        )
        if len(coloane) < 2:
            continue
        tabele.append(Tabel(randuri=tuple(sorted(linii)), coloane=tuple(coloane)))

    return sorted(tabele, key=lambda t: (t.randuri[0], t.coloane[0]))


def _grupeaza_pe_intindere(
    orizontale: list[tuple[int, int, int]],
) -> dict[tuple[int, int], list[int]]:
    """Liniile aceluiasi tabel au aceeasi intindere pe x; asta le separa de ale vecinului."""
    grupuri: dict[tuple[int, int], list[int]] = {}
    for y, a, b in orizontale:
        for (ga, gb), linii in grupuri.items():
            if abs(ga - a) <= TOLERANTA * 3 and abs(gb - b) <= TOLERANTA * 3:
                linii.append(y)
                break
        else:
            grupuri[(a, b)] = [y]
    return grupuri


# ---------------------------------------------------------------------------
# Citirea
# ---------------------------------------------------------------------------


def citeste_tabel(imagine: Image.Image, tabel: Tabel, motor) -> list[Celula]:  # noqa: ANN001
    """Textul fiecarei celule, citit cu acelasi motor ca orarul.

    Decupam geometric, ca la orar: taiem in interiorul chenarului, luam randurile de text si
    dam recunoasterii bucati de o singura linie -- singura forma pe care modelul o citeste
    bine. Celulele goale nu ajung la motor.
    """
    from orar.ingest.ocr import MARJA_CELULA, _decupaj, decupeaza_text
    from orar.ingest.ocr import PRAG_NEGRU as PRAG_TEXT

    im = imagine.convert("RGB")
    negru = (np.asarray(im) <= PRAG_TEXT).all(axis=2)

    plan: list[tuple[Celula, tuple[int, int, int, int]]] = []
    for celula in tabel.celule():
        x0, y0, x1, y1 = celula.bbox
        m = MARJA_CELULA
        sub = negru[y0 + m : y1 - m, x0 + m : x1 - m]
        if sub.size == 0 or not sub.any():
            continue
        for bucata in decupeaza_text(sub, (x0 + m, y0 + m)):
            plan.append((celula, bucata.bbox))

    if not plan:
        return []
    citite = motor.recunoaste([_decupaj(im, cutie) for _, cutie in plan])

    texte: dict[tuple[int, int], list[str]] = {}
    for (celula, _), (text, _scor) in zip(plan, citite, strict=True):
        if text.strip():
            texte.setdefault((celula.rand, celula.coloana), []).append(text.strip())

    return [
        Celula(
            rand=c.rand, coloana=c.coloana, bbox=c.bbox, text=" ".join(texte[(c.rand, c.coloana)])
        )
        for c in tabel.celule()
        if (c.rand, c.coloana) in texte
    ]
