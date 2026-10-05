"""Citirea celulelor: decupare geometrica, vocabular, atribuire pe campuri.

Testele nu au nevoie de modelul de recunoastere. Partea care poate sa se strice tacut e
**impartirea pe campuri**, si aceea se poate proba dand direct textele: un motor fals
intoarce ce ii spunem, iar restul lantului merge la fel ca in productie. Asa testele raman
rapide si nu depind de versiunea modelului ONNX.

Acuratetea reala fata de setul de referinta se masoara separat, cu
`orar evalueaza` (vezi `ingest/evaluate.py`).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from orar.domain.hierarchy import curata_titlu, parse_titlu
from orar.ingest.lexicon import Lexicon
from orar.ingest.ocr import (
    ActivitateCitita,
    BucataText,
    MotorRecunoastere,
    _atribuie,
    _randuri,
    decupeaza_text,
)
from orar.ingest.segment import Celula

GOLDEN = Path(__file__).resolve().parent / "golden" / "date.json"


@pytest.fixture(scope="module")
def lexicon() -> Lexicon:
    return Lexicon.din_json(GOLDEN)


class MotorFals(MotorRecunoastere):
    """Intoarce textele date, in ordine -- ca sa probam ce se intampla *dupa* recunoastere."""

    def __init__(self, texte: list[str]) -> None:
        self.texte = texte

    def recunoaste(self, decupaje):  # noqa: ANN001, ANN201
        return [(t, 0.9) for t in self.texte[: len(decupaje)]]


def _citeste(texte: list[tuple[int, int, str]], lexicon: Lexicon) -> ActivitateCitita:
    """Construieste o celula din (rand, x0, text) si o trece prin atribuire."""
    celula = Celula(
        zi="Luni", col_start=2, col_span=2, banda=0, bbox=(0, 0, 480, 180), culoare=(1, 2, 3)
    )
    bucati = [
        BucataText((x0, rand * 40, x0 + 100, rand * 40 + 30), rand, text, 0.9)
        for rand, x0, text in texte
    ]
    return _atribuie(celula, bucati, lexicon)


# ------------------------------------------------------------ decupare geometrica


def _masca(randuri: list[tuple[int, int, int]], inaltime: int = 120, latime: int = 400):
    """Masca de text sintetica: fiecare rand e (y0, x0, x1)."""
    m = np.zeros((inaltime, latime), dtype=bool)
    for y0, x0, x1 in randuri:
        m[y0 : y0 + 20, x0:x1] = True
    return m


def test_randurile_de_text_raman_separate():
    """Doua linii apropiate sunt doua randuri: un decupaj cu doua linii iese ilizibil."""
    m = _masca([(0, 10, 200), (28, 10, 250)])
    assert len(_randuri(m)) == 2


def test_coada_de_glif_ramane_lipita_de_randul_ei():
    """Liniuta lui `_` din `Gr_1` cade sub rand; daca ar deveni rand propriu, `Gr_1` s-ar rupe."""
    m = np.zeros((120, 400), dtype=bool)
    m[0:20, 10:200] = True  # randul de text
    m[23:26, 60:80] = True  # underscore-ul, 3 px, la 3 px sub rand
    assert len(_randuri(m)) == 1


def test_taiem_intre_campuri_dar_nu_intre_cuvinte():
    """Pragul e o inaltime de rand: sub el e spatiu intre cuvinte, peste el separator."""
    inaltime = 20
    m = _masca([(0, 10, 100), (0, 100 + inaltime // 2, 160), (0, 300, 380)])
    bucati = list(decupeaza_text(m, (0, 0)))
    assert len(bucati) == 2, [b.bbox for b in bucati]
    assert bucati[0].bbox[0] == 10 and bucati[0].bbox[2] >= 160


# --------------------------------------------------------------- vocabularul


def test_lexiconul_corecteaza_confuziile_de_glife(lexicon):
    """`I` majuscul si `l` mic sunt aproape identici in Arial; vocabularul le impaca."""
    assert lexicon.profesor("lon L").valoare == "Ion L"
    assert lexicon.profesor("lon L").din_vocabular


def test_lexiconul_nu_inventeaza(lexicon):
    """Un text care nu seamana cu nimic ramane brut si marcat, nu devine cel mai apropiat termen."""
    p = lexicon.materie("YainMoheoPesy")
    assert not p.din_vocabular
    assert p.valoare == "YainMoheoPesy"


def test_salile_se_compara_pe_forma_canonica(lexicon):
    """`L-507` si `L.507` sunt aceeasi sala; separatorul nu poarta informatie."""
    assert lexicon.sala("L.507").valoare == lexicon.sala("L-507").valoare


def test_bara_dintre_profesori_citita_ca_litera(lexicon):
    """`Baetica C / Mincu G` iese `Baetica C I Mincu G`; taietura se accepta doar daca

    fiecare bucata devine un profesor cunoscut."""
    p = lexicon.profesor("Baetica C I Mincu G")
    assert p.valoare == "Baetica C / Mincu G"
    assert p.din_vocabular


def test_nu_taiem_un_nume_care_contine_initiala_I(lexicon):
    """Regula de mai sus nu are voie sa rupa un nume valid."""
    p = lexicon.profesor("Ion L")
    assert p.valoare == "Ion L"


# ------------------------------------------------------- atribuirea pe campuri


def test_celula_obisnuita(lexicon):
    act = _citeste(
        [
            (0, 20, "Dumitran M"),
            (0, 380, "Gr_1"),
            (1, 20, "LbForm&Autom (Lab, SI)"),
            (2, 370, "L-507"),
        ],
        lexicon,
    )
    assert (act.profesor, act.materie, act.tip) == ("Dumitran M", "LbForm&Autom", "Lab")
    assert (act.frecventa, act.semigrupa, act.sala) == ("SI", "Gr_1", "L-507")


def test_materia_si_tipul_in_grupuri_separate(lexicon):
    """aSc lasa uneori intre materie si paranteza un gol mai mare decat cel dintre campuri."""
    act = _citeste(
        [(0, 20, "Muresan C"), (1, 20, "LogMat&Comp"), (2, 100, "(sem+Lab)"), (3, 370, "L-404")],
        lexicon,
    )
    assert (act.profesor, act.materie, act.tip) == ("Muresan C", "LogMat&Comp", "sem+Lab")
    assert act.sala == "L-404"


def test_materie_tip_si_sala_in_acelasi_grup(lexicon):
    """In celulele inguste decuparea nu mai separa sala; paranteza se cauta oriunde."""
    act = _citeste(
        [(0, 20, "Stefanescu A"), (0, 380, "Gr_2"), (1, 20, "ManagProd&Antrepr (Lab, SP)L-509")],
        lexicon,
    )
    assert (act.materie, act.tip, act.frecventa) == ("ManagProd&Antrepr", "Lab", "SP")
    assert (act.sala, act.semigrupa) == ("L-509", "Gr_2")
    assert act.profesor == "Stefanescu A"


def test_materia_rupta_la_capat_de_rand(lexicon):
    """Se reunesc **numai** daca asa iese un termen cunoscut -- conditia se verifica singura."""
    act = _citeste(
        [
            (0, 20, "Baetica C / Mincu G"),
            (1, 20, "GrupFin&ElemTeorGal"),
            (2, 20, "ois"),
            (2, 200, "(curs)"),
        ],
        lexicon,
    )
    assert act.materie == "GrupFin&ElemTeorGalois"
    assert act.profesor == "Baetica C / Mincu G"


def test_saptamanile_cu_eticheta(lexicon):
    """`[SE] s25, 26` -- eticheta in paranteze, saptamanile dupa."""
    act = _citeste(
        [(0, 20, "Tataram M"), (1, 20, "DidacticaInfo (seminar)"), (2, 20, "[SE] s25, 26")], lexicon
    )
    assert act.saptamani == "SE s25, 26"
    assert act.tip == "seminar"


def test_paranteza_care_nu_e_tip_nu_fura_materia(lexicon):
    """`IMAR (sala 412)` e o sala, nu o activitate de tip "sala 412"."""
    act = _citeste(
        [(0, 20, "Beznea L"), (1, 20, "TeorPotential (curs+seminar)"), (2, 200, "IMAR (sala 412)")],
        lexicon,
    )
    assert act.tip == "curs+seminar"
    assert act.sala == "IMAR (sala 412)"


def test_numarul_grupei_nu_e_profesor(lexicon):
    """La laboratoarele de la Fizica, in locul profesorului e scris numarul formatiunii."""
    act = _citeste([(0, 20, "361"), (1, 20, "ArhitSistPar (Lab)")], lexicon)
    assert act.profesor == ""
    assert act.materie == "ArhitSistPar"


def test_campurile_neconfirmate_sunt_marcate(lexicon):
    """Ce nu trece prin vocabular ajunge in coada de verificare, nu tacut in baza."""
    act = _citeste([(0, 20, "Gherghe C YainMoheoPesy"), (1, 20, "Combinatorica (curs)")], lexicon)
    assert "profesor" in act.nesigure


# ------------------------------------------------------------------- titluri


@pytest.mark.parametrize(
    ("brut", "asteptat"),
    [
        ("Optionale an Ill - MATE (1)", "Optionale an III - MATE (1)"),
        ("Optionale an IlI - MATE (2)", "Optionale an III - MATE (2)"),
        ("Optionale an Il - MATE APLICATE (2)", "Optionale an II - MATE APLICATE (2)"),
        ("MATE Master 4O3 (PSFS - x)", "MATE Master 403 (PSFS - x)"),
        (
            "INFO Serile 33,34,35: Optionale an Ill - INFO (Curs)",
            "INFO Seriile 33,34,35: Optionale an III - INFO (Curs)",
        ),
    ],
)
def test_titlul_se_repara_inainte_de_parsare(brut, asteptat):
    """Confuziile de glife cad exact pe partile care duc ierarhia."""
    assert curata_titlu(brut) == asteptat


@pytest.mark.parametrize(
    "titlu", ["INFO Grupa 144", "Limbi straine - an I (Mate Info, CTI)", "MATE APL. Grupa 222"]
)
def test_repararea_nu_strica_titlurile_bune(titlu):
    assert curata_titlu(titlu) == titlu


def test_titlurile_reparate_isi_pastreaza_anul():
    assert parse_titlu("Optionale an Ill - MATE (1)").an == 3
    assert parse_titlu("Optionale an Il - MATE APLICATE (2)").an == 2


# -------------------------------------------------- integrare, pe capturi reale


def _capturi_grupe() -> Path:
    """Capturile orarului **grupelor**.

    `data/screenshots/` tine acum si orarul profesorilor, in subdirectorul lui: 191 de pagini
    care nu au corespondent in setul de referinta. Comparate cu el, ar iesi tot atatea
    "pagini nepotrivite" -- fara ca extragerea sa aiba vreo vina.

    Capturile stau pe sesiuni de incarcare (`2025-2026-sem2-grupe-<moment>/`, vezi
    `ingest/capturi.py`); setul de referinta e orarul grupelor din semestrul 2, 2025-2026.
    Luam cea mai recenta sesiune a lui, sau directorul vechi, dinainte de sesiuni.
    """
    radacina = Path(__file__).resolve().parent.parent / "data" / "screenshots"
    sesiuni = sorted(radacina.glob("2025-2026-sem2-grupe-*"))
    return sesiuni[-1] if sesiuni else radacina / "sem2-grupe"


CAPTURI = _capturi_grupe()

#: Praguri de regresie, masurate cu `orar evalueaza` pe capturile la rezolutie nativa.
#: Sunt cu ~0.5 puncte sub valorile obtinute (sala 100.00, frecventa 99.91, saptamani si
#: semigrupa 99.82, tip 99.74, materie si profesor 99.56), ca sa prinda o stricare reala
#: fara sa pice la o zecime de procent. Cerintele planului (Etapa 5) erau mai blande:
#: sala/tip/frecventa/semigrupa >= 99%, materie >= 97%, profesor >= 95%.
PRAGURI = {
    "sala": 0.995,
    "tip": 0.99,
    "frecventa": 0.99,
    "semigrupa": 0.99,
    "saptamani": 0.99,
    "materie": 0.99,
    "profesor": 0.99,
}


@pytest.mark.skipif(not CAPTURI.is_dir(), reason="data/screenshots/ nu e in repo")
@pytest.mark.slow
def test_acuratetea_pe_capturile_reale(lexicon):
    """Citirea completa a celor 98 de pagini, comparata cu setul de referinta.

    `ore` nu e in praguri: acolo referinta greseste sistematic (imparte benzile suprapuse
    in celule alaturate), iar dezacordul se raporteaza separat de `evalueaza`. Vezi
    `docs/formatul-orarului.md` §7 pentru cazurile verificate pe pixeli.
    """
    from orar.ingest.evaluate import evalueaza
    from orar.ingest.ocr import RapidOCR

    imagini = sorted(CAPTURI.rglob("*.png"))
    if len(imagini) < 90:
        pytest.skip("prea putine capturi pentru o masuratoare relevanta")

    raport = evalueaza(imagini, GOLDEN, RapidOCR(), lexicon)
    assert raport.pagini_citite >= 95
    assert not raport.pagini_nepotrivite, raport.pagini_nepotrivite
    for camp, prag in PRAGURI.items():
        assert raport.acuratete(camp) >= prag, f"{camp}: {raport.acuratete(camp):.4f} < {prag}"

    # Dezacordurile pe `ore` trebuie sa fie ale referintei, nu ale noastre: intervalul
    # nostru il *contine* pe cel citit gresit, pentru ca benzile suprapuse au fost taiate.
    forme = raport.forme_ore
    assert forme["contine"] > 5 * (forme["continut"] + forme["decalat"]), dict(forme)


@pytest.mark.skipif(not CAPTURI.is_dir(), reason="data/screenshots/ nu e in repo")
def test_paginile_de_coperta_nu_produc_activitati():
    """Primele doua pagini sunt anunturi si tabelul de capacitati, nu orare."""
    from orar.ingest.segment import EroareSegmentare, segmenteaza_fisier

    for nume in ("pag_001.png", "pag_002.png"):
        cale = next((p for p in CAPTURI.rglob(nume) if p.is_file()), None)
        if cale is None:
            pytest.skip(f"{nume} lipseste")
        with pytest.raises(EroareSegmentare):
            segmenteaza_fisier(cale)


def test_golden_are_toate_paginile():
    date = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(date) == 98
    assert len({p["grupa"] for p in date}) == 98, "titlurile trebuie sa fie unice, sunt cheia"
