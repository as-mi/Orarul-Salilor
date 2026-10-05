"""Segmentarea geometrica, validata pe paginile reale.

De ce nu comparam pur si simplu cu golden-ul
--------------------------------------------
Setul golden a fost extras cu un model si **greseste sistematic `ore`**: da adesea celula
cu o coloana mai ingusta decat e. Verificat pixel cu pixel pe imagini, in cazuri ca:

    pag_05 Marti  "AnalizaMate II (seminar)" ocupa coloanele 11,12,13 => 11-14
                  golden zice 11-13
    pag_16 Joi    "LbForm&Autom" si "StructDate" ocupa amandoua 12-13, suprapuse pe verticala
                  golden le pune una langa alta, 12-13 si 13-14
    pag_14 Mie    sunt 3 celule pe 10-12 si 3 pe 12-14
                  golden vede 6 activitati, cu alte intervale, si pierde doua

Segmentarea nu citeste text: `ore` iese din aritmetica pe caroiaj, deci nu are cum sa
"aproximeze" o latime. Testele de mai jos verifica deci:

  1. invariantii geometrici (caroiaj uniform, celule in grila, intervale valide);
  2. adevarul **verificat vizual** pe cazurile de mai sus;
  3. gradul de acord cu golden-ul, ca metrica de regresie -- nu ca adevar absolut.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from orar.ingest.segment import (
    NR_ORE,
    ORA_MIN,
    ZILE,
    EroareSegmentare,
    segmenteaza_fisier,
)

RADACINA = Path(__file__).resolve().parent
FIXTURI = RADACINA / "fixtures"
PROCESATE = RADACINA.parent / "data" / "processed_ss"
GOLDEN = RADACINA / "golden" / "date.json"


@pytest.fixture(scope="module")
def golden_pe_sursa() -> dict[str, dict]:
    return {p["_source"]: p for p in json.loads(GOLDEN.read_text(encoding="utf-8"))}


def _fixturi() -> list[Path]:
    return sorted(FIXTURI.glob("pag_*.png"))


def _pagina(nume: str) -> Path:
    cale = FIXTURI / nume
    return cale if cale.exists() else PROCESATE / nume


# --------------------------------------------------------------------- invarianti


@pytest.mark.parametrize("cale", _fixturi(), ids=lambda p: p.name)
def test_caroiajul_e_uniform(cale):
    """Coloanele orare trebuie sa iasa egale -- aSc le deseneaza perfect regulate."""
    pag = segmenteaza_fisier(cale)
    pasi = [b - a for a, b in zip(pag.caroiaj.coloane, pag.caroiaj.coloane[1:], strict=False)]
    assert len(pasi) == NR_ORE
    assert max(pasi) - min(pasi) <= 2, f"coloane inegale: {pasi}"

    inaltimi = [b - a for a, b in zip(pag.caroiaj.randuri, pag.caroiaj.randuri[1:], strict=False)]
    assert len(inaltimi) == len(ZILE)
    assert max(inaltimi) - min(inaltimi) <= 3, f"randuri inegale: {inaltimi}"


@pytest.mark.parametrize("cale", _fixturi(), ids=lambda p: p.name)
def test_celulele_stau_in_grila(cale):
    pag = segmenteaza_fisier(cale)
    for c in pag.celule:
        assert c.zi in ZILE
        assert 0 <= c.col_start < NR_ORE
        assert 1 <= c.col_span <= NR_ORE - c.col_start
        assert ORA_MIN <= c.ora_inceput < c.ora_sfarsit <= ORA_MIN + NR_ORE
        x0, y0, x1, y1 = c.bbox
        assert x0 < x1 and y0 < y1


def test_pagina_fara_tabel_e_respinsa(tmp_path):
    """O coperta sau un cuprins nu trebuie sa produca celule inventate."""
    from PIL import Image

    goala = tmp_path / "goala.png"
    Image.new("RGB", (1610, 1120), "white").save(goala)
    with pytest.raises(EroareSegmentare):
        segmenteaza_fisier(goala)


# ------------------------------------------------------- adevar verificat vizual

#: (pagina, zi, intervalele corecte) -- confirmate uitandu-ne la pixeli, nu luate din golden.
ZILE_VERIFICATE_COMPLET = [
    # Doua activitati suprapuse pe verticala, amandoua pe coloanele 12-13.
    # Golden le pune una langa alta: 12-13 si 13-14.
    ("pag_16.png", "Joi", {"10-12": 1, "12-14": 2}),
    # Trei celule pe 10-12 si trei pe 12-14. Golden vede 6 activitati cu alte intervale
    # si pierde doua dintre ele.
    ("pag_14.png", "Miercuri", {"10-12": 3, "12-14": 3, "14-16": 1, "16-18": 1}),
]

#: Cazuri in care am verificat vizual o singura celula, nu toata ziua.
CELULE_VERIFICATE = [
    # "AnalizaMate II (seminar)" acopera coloanele 11, 12 si 13. Golden zice 11-13.
    ("pag_05.png", "Marti", "11-14", 1),
]


@pytest.mark.parametrize(("nume", "zi", "asteptat"), ZILE_VERIFICATE_COMPLET)
def test_ziua_verificata_vizual(nume, zi, asteptat):
    pag = segmenteaza_fisier(_pagina(nume))
    assert Counter(c.ore for c in pag.pe_zi(zi)) == Counter(asteptat)


@pytest.mark.parametrize(("nume", "zi", "interval", "cate"), CELULE_VERIFICATE)
def test_celula_verificata_vizual(nume, zi, interval, cate):
    pag = segmenteaza_fisier(_pagina(nume))
    assert Counter(c.ore for c in pag.pe_zi(zi))[interval] == cate


def test_celule_cu_fundal_in_diagonala_raman_intregi():
    """aSc coloreaza unele celule in doua tonuri, taiate in diagonala.

    Cele doua jumatati au culori complet diferite, dar sunt aceeasi activitate: pe
    pag_73 fiecare laborator de FizicaComp e portocaliu-albastru si acopera doua coloane.
    Daca segmentarea s-ar lua dupa culoare, le-ar rupe in doua.
    """
    pag = segmenteaza_fisier(_pagina("pag_73.png"))
    joi = pag.pe_zi("Joi")
    assert len(joi) == 4, [c.ore for c in joi]
    assert Counter(c.ore for c in joi) == Counter({"8-10": 1, "10-12": 2, "12-14": 1})


# ----------------------------------------------------------- metrica de regresie

#: Praguri de regresie fata de golden. Nu sunt 100% fiindca golden-ul insusi greseste
#: (vezi docstring-ul modulului); scaderea sub ele inseamna insa ca segmentarea s-a stricat.
#: acord pe *numarul* de activitati dintr-o pagina / zi
PRAG_PAGINI = 0.94
PRAG_ZILE = 0.98
#: acord pe *intervalul orar*. Mai jos, fiindca exact aici greseste golden-ul cel mai des:
#: da constant celula cu o coloana mai ingusta. Segmentarea deduce latimea din caroiaj.
PRAG_ORE = 0.85


@pytest.mark.skipif(not PROCESATE.exists(), reason="data/processed_ss/ nu e in repo")
def test_acord_global_cu_golden(golden_pe_sursa):
    pagini = [n for n in golden_pe_sursa if (PROCESATE / n).exists()]
    assert len(pagini) >= 90, "prea putine pagini pentru o masuratoare relevanta"

    pagini_ok = zile_ok = zile_tot = 0
    ore_ok = ore_tot = 0
    for nume in pagini:
        pag = segmenteaza_fisier(PROCESATE / nume)  # nu trebuie sa arunce pe nicio pagina
        ref = golden_pe_sursa[nume]
        if len(pag.celule) == sum(len(ref.get(z, [])) for z in ZILE):
            pagini_ok += 1
        for z in ZILE:
            zile_tot += 1
            det = Counter(c.ore for c in pag.pe_zi(z))
            gol = Counter(a["ore"] for a in ref.get(z, []))
            ore_tot += sum(gol.values())
            ore_ok += sum((det & gol).values())
            if sum(det.values()) == sum(gol.values()):
                zile_ok += 1

    assert pagini_ok / len(pagini) >= PRAG_PAGINI, f"pagini exacte: {pagini_ok}/{len(pagini)}"
    assert zile_ok / zile_tot >= PRAG_ZILE, f"zile cu acelasi numar: {zile_ok}/{zile_tot}"
    assert ore_ok / ore_tot >= PRAG_ORE, f"`ore` potrivit: {ore_ok}/{ore_tot}"


def test_celulele_albe_pe_doua_ore_raman_intregi():
    """O celula alba pe doua ore, cu textul pe mijloc: jumatatea din stanga are doar o parte
    din cuvant ("Fundam" din "FundamIngSoft") si putin text. Nu e un slot gol: celula trebuie
    sa inceapa la 14, nu la 15 -- altfel si decupajul, si ora ar fi taiate la jumatate.
    Pagina e din orarul 2026-2027, semestrul 1 (grupa 262)."""
    pag = segmenteaza_fisier(_pagina("pag_2026_043.png"))
    assert "14-16" in [c.ore for c in pag.pe_zi("Marti")]
    assert "8-10" in [c.ore for c in pag.pe_zi("Joi")]  # TeorSist (Lab)
