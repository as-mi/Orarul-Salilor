"""Nucleul planificatorului de tutoriate: acoperire, continuitate "Fara insule",
indulgenta si Set Cover peste agregatul "Orarul in cifre".

Testele sunt pure -- schedulerul primeste un dict `ocupare[(zi, ora)] -> persoane ocupate`
si nu atinge baza de date.
"""

from __future__ import annotations

from orar.domain.grid import ZILE
from orar.domain.tutoriate_scheduler import (
    acoperire_slot,
    lipit_si_curs,
    planifica,
    sloturi_candidate,
)

POP = 100


def _ocupare_zi(zi: str, *ore: tuple[int, int]) -> dict[tuple[str, int], int]:
    """Construieste ocuparea: perechi (ora_de_inceput, nr_persoane) care dureaza o ora."""
    return {(zi, h): n for h, n in ore}


def test_acoperire_slot_ia_varful_de_ocupatie():
    # Slot [10,12): ora 10 are 60 ocupati, ora 11 are 20 -> varful e 60.
    ocup = {("Luni", 10): 60, ("Luni", 11): 20}
    acoperire, liberi = acoperire_slot(ocup, POP, "Luni", 10, 2)
    assert liberi == 40
    assert acoperire == 40


def test_acoperire_slot_populatie_zero():
    assert acoperire_slot({}, 0, "Luni", 10, 2) == (0, 0)


def test_lipit_cand_ceasul_anterior_e_ocupat():
    ocup = _ocupare_zi("Joi", (13, 60))
    lipit, _ = lipit_si_curs(ocup, POP, "Joi", 14, 2)
    assert lipit is True


def test_lipit_cand_ceasul_urmator_e_ocupat():
    ocup = _ocupare_zi("Joi", (10, 60))
    lipit, _ = lipit_si_curs(ocup, POP, "Joi", 8, 2)  # [8,10), dupa=10 e ocupat
    assert lipit is True


def test_insula_cand_niciun_ceas_alaturat_nu_e_curs():
    # 20 ocupati < 50% prag => nu e "curs".
    ocup = _ocupare_zi("Joi", (13, 20))
    lipit, _ = lipit_si_curs(ocup, POP, "Joi", 14, 2)
    assert lipit is False


def test_lipit_numele_cursului_dominant():
    ocup = _ocupare_zi("Joi", (13, 60))
    dominante = {("Joi", 13): "Analiza Matematica"}
    lipit, curs = lipit_si_curs(ocup, POP, "Joi", 14, 2, dominante=dominante)
    assert lipit is True
    assert curs == "Analiza Matematica"


def test_sloturi_candidate_respecta_fezabile():
    fezabile = {("Luni", 10)}
    sloturi = sloturi_candidate({}, 2, fezabile=fezabile)
    assert [(s.zi, s.h) for s in sloturi] == [("Luni", 10)]


def test_sloturi_candidate_limitele_grila():
    # Nicio ora nu depaseste ORA_MAX - durata (ultimul slot incepe la 18:00 pentru durata 2).
    sloturi = sloturi_candidate({}, 2)
    assert all(s.h + 2 <= 20 for s in sloturi)
    assert len(sloturi) == 5 * (20 - 8 - 2 + 1)


def test_lipit_85_bate_insula_100():
    """Scenariul din plan: o solutie lipita la 85% e preferata uneia de 100% cu insula."""
    ocup = {}
    for h in range(8, 14):  # bloc de curs 08:00-14:00
        ocup[("Luni", h)] = 60
    ocup[("Luni", 15)] = 15  # o grupa tine pana la 18 => slotul 14:00 acopera 85%
    dominante = {("Luni", 13): "Programare"}

    rezultate = planifica(ocup, POP, durata=2, max_sesiuni=1, indulgenta=75, dominante=dominante)
    cel_mai_bun = rezultate[0].propuneri[0]
    assert cel_mai_bun.zi == "Luni"
    assert cel_mai_bun.h == 14
    assert cel_mai_bun.acoperire == 85
    assert cel_mai_bun.lipit is True


def test_set_cover_combinat_acopera_mai_mult_ca_o_singura_sesiune():
    # O singura sesiune lipita acopera 85%; doua sesiuni lipite ajung la 100%.
    ocup = {}
    for h in range(8, 14):
        ocup[("Luni", h)] = 60
    ocup[("Luni", 15)] = 15

    rezultate = planifica(ocup, POP, durata=2, indulgenta=75)
    cel_mai_bun = rezultate[0]
    assert cel_mai_bun.acoperire == 100
    assert cel_mai_bun.nr_insule == 0
    assert len(cel_mai_bun.propuneri) >= 2


def test_indulgenta_sub_prag_inca_intorce_ceva():
    """Chiar daca niciun slot nu atinge indulgenta, intoarcem cea mai buna solutie."""
    ocup = {}
    for zi in ZILE:
        for h in range(8, 20):
            ocup[(zi, h)] = 95  # doar 5% liberi in fiecare ora a fiecarei zile
    rezultate = planifica(ocup, POP, durata=2, indulgenta=75)
    assert rezultate  # nu e gol
    assert rezultate[0].meets_indulgenta is False  # sub prag
    assert rezultate[0].acoperire > rezultate[0].propuneri[0].acoperire  # Set Cover combina sesiunile


def test_fara_sloturi_feazibile_intorce_gol():
    assert planifica({}, POP, durata=2, fezabile=set()) == []


def test_explicatia_contine_ziua_ora_si_acoperirea():
    ocup = _ocupare_zi("Luni", (13, 60))
    rezultate = planifica(ocup, POP, durata=2, max_sesiuni=1)
    propunere = rezultate[0].propuneri[0]
    assert propunere.zi == "Luni"
    assert f"{propunere.h:02d}:00" in propunere.explicatie
    assert "acoperă" in propunere.explicatie
    assert propunere.lipit is True  # lipit de ora 13 ocupata
