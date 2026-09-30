"""Segmentarea tabelelor cu linii trasate si citirea capacitatilor de sali.

Tabelele se construiesc aici din pixeli, nu se citesc de pe disc: ce poate sa se strice tacit
e **detectia liniilor**, si aceea se probeaza cel mai clar pe o imagine in care stim exact
unde am pus fiecare linie. Citirea reala a paginii 2 e verificata separat, marcata `slow`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select

from orar.db.models import Sala
from orar.ingest.capacities import Capacitate, citeste_capacitati, incarca_capacitati
from orar.ingest.tables import detecteaza_tabele

PAGINA_2 = (
    Path(__file__).resolve().parent.parent / "data" / "screenshots" / "sem2-grupe" / "pag_002.png"
)


def _deseneaza(
    randuri: list[int], coloane: list[int], *, latime: int = 1200, inaltime: int = 900
) -> Image.Image:
    """Un tabel alb cu linii negre exact la pozitiile cerute."""
    a = np.full((inaltime, latime, 3), 255, dtype=np.uint8)
    for y in randuri:
        a[y : y + 2, coloane[0] : coloane[-1]] = 0
    for x in coloane:
        a[randuri[0] : randuri[-1], x : x + 2] = 0
    return Image.fromarray(a)


def test_gaseste_randurile_si_coloanele():
    im = _deseneaza([100, 160, 220, 280], [50, 400, 700])
    (tabel,) = detecteaza_tabele(im)
    assert tabel.nr_randuri == 3
    assert tabel.nr_coloane == 2
    assert tabel.randuri == (100, 160, 220, 280)


def test_doua_tabele_alaturate_ies_separat():
    """Pe pagina 2 stau doua tabele unul langa altul; o proiectie pe toata latimea le-ar uni."""
    a = np.full((900, 1600, 3), 255, dtype=np.uint8)
    for x0, x1 in ((60, 700), (900, 1540)):
        for y in (100, 160, 220, 280):
            a[y : y + 2, x0:x1] = 0
        for x in (x0, (x0 + x1) // 2, x1):
            a[100:280, x : x + 2] = 0
    tabele = detecteaza_tabele(Image.fromarray(a))
    assert len(tabele) == 2
    assert [t.nr_coloane for t in tabele] == [2, 2]
    assert tabele[0].coloane[0] < tabele[1].coloane[0], "ordinea de pe pagina"


def test_linia_ingrosata_nu_devine_doua():
    """La randare o linie are cativa pixeli; ar iesi cate un rand gol intre fiecare doua."""
    a = np.full((600, 900, 3), 255, dtype=np.uint8)
    for y in (100, 200, 300):
        a[y : y + 5, 50:800] = 0  # cinci pixeli grosime
    for x in (50, 800):
        a[100:305, x : x + 5] = 0
    (tabel,) = detecteaza_tabele(Image.fromarray(a))
    assert tabel.nr_randuri == 2


def test_un_chenar_simplu_nu_e_tabel():
    """Doua linii orizontale fac un chenar, nu un tabel cu randuri."""
    a = np.full((600, 900, 3), 255, dtype=np.uint8)
    for y in (100, 300):
        a[y : y + 2, 50:800] = 0
    for x in (50, 800):
        a[100:300, x : x + 2] = 0
    assert detecteaza_tabele(Image.fromarray(a)) == []


def test_pagina_fara_linii_nu_da_tabele():
    assert detecteaza_tabele(Image.new("RGB", (800, 600), "white")) == []


# ------------------------------------------------------------- capacitatile


def _cap(brut: str, locuri: int | None) -> Capacitate:
    from orar.domain.rooms import normalizeaza_sala

    return Capacitate(
        brut=brut,
        slug=normalizeaza_sala(brut).slug,
        slug_scurt=normalizeaza_sala(brut.split("(")[0]).slug,
        locuri=locuri,
    )


@pytest.fixture(autouse=True)
def curata(db):
    yield
    db.rollback()


def test_capacitatea_se_scrie_pe_sala(db):
    rap = incarca_capacitati(db, [_cap("Amf. 501", 122)])
    assert rap.completate == 1
    assert db.scalar(select(Sala).where(Sala.slug == "amf-501")).nr_locuri == 122


def test_separatorul_nu_conteaza_la_legare(db):
    """Tabelul scrie `S-214`, orarul `S.214`; slug-ul le aduce la aceeasi forma."""
    sala = db.scalar(select(Sala).where(Sala.slug == "s-214"))
    assert sala is not None
    incarca_capacitati(db, [_cap("S-214", 70)])
    assert sala.nr_locuri == 70


def test_lamurirea_din_paranteza_nu_face_alta_sala(db):
    """Tabelul scrie `L-509 (iOS)`, orarul doar `L-509` -- aceeasi sala, alta descriere."""
    sala = db.scalar(select(Sala).where(Sala.slug == "l-509"))
    assert sala is not None
    rap = incarca_capacitati(db, [_cap("L-509 (iOS)", 30)])
    assert rap.completate == 1
    assert sala.nr_locuri == 30


def test_semnul_intrebarii_ramane_necunoscut(db):
    """`L.414 Robotica` are `?` in sursa; nu inventam un numar."""
    rap = incarca_capacitati(db, [_cap("L.414_Robotica", None)])
    assert rap.completate == 0
    assert rap.avertismente


def test_sala_din_tabel_fara_ore_se_adauga(db):
    """Tabelul e lista oficiala: o sala fara nicio ora e libera, nu inexistenta. Apare in
    lista de sali si poate fi aleasa cand un admin muta o activitate."""
    inainte = len(list(db.scalars(select(Sala))))
    rap = incarca_capacitati(db, [_cap("S-999 (noua)", 42)])
    assert rap.adaugate == ["S.999"]
    assert len(list(db.scalars(select(Sala)))) == inainte + 1
    sala = db.scalar(select(Sala).where(Sala.slug == "s-999"))
    assert (sala.nume, sala.tip, sala.nr_locuri) == ("S.999", "fizica", 42)


# ------------------------------------------------------- pe pagina reala


@pytest.mark.skipif(not PAGINA_2.is_file(), reason="captura paginii 2 nu e in repo")
@pytest.mark.slow
def test_citirea_paginii_reale(db):
    """Cele doua tabele de pe pagina 2, citite cap-coada."""
    from orar.ingest.ocr import RapidOCR

    capacitati = citeste_capacitati(PAGINA_2, RapidOCR())
    assert len(capacitati) >= 28, "pagina are ~30 de sali in cele doua tabele"

    dupa_slug = {c.slug: c for c in capacitati}
    assert dupa_slug["amf-701"].locuri == 150
    assert dupa_slug["amf-501"].locuri == 122
    assert dupa_slug["l-410"].locuri == 15

    rap = incarca_capacitati(db, capacitati)
    assert rap.completate >= 28
    assert rap.adaugate == [], rap.adaugate


# ------------------------------------------------- tabelul, citit inaintea orarului


def test_numele_canonic_al_salii_din_tabel():
    assert _cap("Amf. 501", 122).nume == "Amf.501"
    assert _cap("L-509 (iOS)", 30).nume == "L.509"
    assert _cap("S-214", 70).nume == "S.214"


def test_salile_din_tabel_sunt_vocabular_si_pe_o_baza_goala():
    """Fara tabel, pe o baza goala nicio sala n-ar fi recunoscuta; cu el, `L-410` din celula
    e `L.410` din lista oficiala."""
    from orar.ingest.lexicon import Lexicon, imbina

    gol = Lexicon()
    assert not gol.sala("L-410").din_vocabular
    cu_tabel = imbina(gol, Lexicon(sali=[_cap("L.410", 15).nume, _cap("Amf. 501", 122).nume]))
    potrivire = cu_tabel.sala("L-410")
    assert potrivire.din_vocabular and potrivire.valoare == "L.410"
    assert cu_tabel.sala("Amf.501").valoare == "Amf.501"


def test_tabelul_salilor_se_cauta_pe_primele_pagini(monkeypatch, tmp_path):
    """Pagina 1 (anunturi) n-are tabel, pagina 2 il are, de la 3 incolo sunt orare: ne oprim
    la prima pagina care raspunde si nu citim cu OCR-ul de tabele paginile de orar."""
    from types import SimpleNamespace

    from orar.ingest import capacities, segment
    from orar.worker import sync

    pagini = [SimpleNamespace(cale=tmp_path / f"pag_{i:03}.png") for i in range(1, 8)]
    incercate = []

    def segmenteaza(cale):  # noqa: ANN001, ANN202
        if cale.name in ("pag_001.png", "pag_002.png"):
            raise segment.EroareSegmentare("nu e orar")

    def citeste(cale, _motor):  # noqa: ANN001, ANN202
        incercate.append(cale.name)
        return [_cap("Amf. 501", 122)] if cale.name == "pag_002.png" else []

    monkeypatch.setattr(segment, "segmenteaza_fisier", segmenteaza)
    monkeypatch.setattr(capacities, "citeste_capacitati", citeste)

    randuri, pagina = sync._tabelul_salilor(pagini, motor=None)
    assert pagina == "pag_002.png" and [r.nume for r in randuri] == ["Amf.501"]
    assert incercate == ["pag_001.png", "pag_002.png"]

    # un document fara tabel: ingestul merge mai departe cu salile din baza
    monkeypatch.setattr(capacities, "citeste_capacitati", lambda *_: [])
    assert sync._tabelul_salilor(pagini, motor=None) == ([], "")
