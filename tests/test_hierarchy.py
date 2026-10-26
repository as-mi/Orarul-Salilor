"""Decodarea titlurilor de pagina."""

from __future__ import annotations

import pytest

from orar.domain.hierarchy import (
    TipPagina,
    normalizeaza_semigrupa,
    normalizeaza_specializare,
    parse_titlu,
)


@pytest.mark.parametrize(
    ("titlu", "spec", "an", "serie", "grupa"),
    [
        ("INFO Grupa 144", "INFO", 1, "14", "144"),
        ("INFO Grupa 244", "INFO", 2, "24", "244"),
        ("INFO Grupa 352", "INFO", 3, "35", "352"),
        ("CTI Grupa 161", "CTI", 1, "16", "161"),
        ("MATE Grupa 101", "MATE", 1, "10", "101"),
        ("MATE APL. Grupa 221", "MATE-APL", 2, "22", "221"),
        ("MATE-INFO Grupa 311", "MATE-INFO", 3, "31", "311"),
    ],
)
def test_grupa_decodeaza_an_serie_grupa(titlu, spec, an, serie, grupa):
    t = parse_titlu(titlu)
    assert t.tip is TipPagina.GRUPA
    assert (t.specializare, t.an, t.serie, t.grupa) == (spec, an, serie, grupa)


@pytest.mark.parametrize(
    ("titlu", "an", "program"),
    [
        ("INFO Master 405 (BDTS - Baze de date si tehnologii software)", 1, "BDTS"),
        ("INFO Master 512 (NLP - Natural Language Processing)", 2, "NLP"),
        ("MATE Master 403 (PSFS - Probabilitati si statistica in finante si stiinte)", 1, "PSFS"),
    ],
)
def test_master(titlu, an, program):
    t = parse_titlu(titlu)
    assert t.tip is TipPagina.MASTER
    assert t.an == an
    assert t.program == program
    assert t.denumire_program  # denumirea desfasurata nu ajunge in `note`
    assert not t.note


def test_pagina_de_serii_retine_toate_seriile():
    t = parse_titlu("INFO Seriile 33,34,35: Optionale an III - INFO (Curs)")
    assert t.tip is TipPagina.OPTIONAL_SERII
    assert t.serii_tinta == ("33", "34", "35")
    assert t.an == 3


def test_facultativ_retine_specializarile_tinta():
    t = parse_titlu("Facultative an II (Mate, Mate-Info, Mate Apl., Info, CTI)")
    assert t.tip is TipPagina.FACULTATIV
    assert t.an == 2
    assert set(t.specializari_tinta) == {"MATE", "MATE-INFO", "MATE-APL", "INFO", "CTI"}


def test_limbi_straine():
    t = parse_titlu("Limbi straine - an I (Mate Info, CTI)")
    assert t.tip is TipPagina.LIMBI
    assert t.an == 1
    assert set(t.specializari_tinta) == {"MATE-INFO", "CTI"}


def test_optional_master():
    t = parse_titlu("Optionale an I Master INFO")
    assert t.tip is TipPagina.OPTIONAL
    assert t.specializare == "INFO"
    assert t.program == "master"


def test_robotica():
    t = parse_titlu("(studenti Fizica/Robotica, an 2, grupa 201ROB)")
    assert t.tip is TipPagina.SPECIAL
    assert t.grupa == "201ROB"
    assert t.an == 2


def test_tutoriat_o_serie():
    t = parse_titlu("Tutoriat (MATE seria 10)")
    assert t.tip is TipPagina.TUTORIAT
    assert t.specializare == "MATE"
    assert t.serii_tinta == ("10",)
    assert t.an == 1


def test_tutoriat_mai_multe_serii():
    t = parse_titlu("Tutoriat (INFO serile 13, 14, 15)")
    assert t.tip is TipPagina.TUTORIAT
    assert t.specializare == "INFO"
    assert t.serii_tinta == ("13", "14", "15")


def test_titlu_nerecunoscut_nu_arunca_si_e_marcat():
    t = parse_titlu("Conferinte si Seminarii")
    assert t.tip is TipPagina.NECUNOSCUT
    assert t.note  # semnalat, dar nu pierdut
    assert t.eticheta == "Conferinte si Seminarii"


def test_titlu_gol():
    t = parse_titlu("")
    assert t.tip is TipPagina.NECUNOSCUT
    assert t.raw == ""


def test_toate_titlurile_din_golden_se_parseaza(pagini_golden):
    """Nicio pagina reala nu trebuie sa ramana neclasificata pe langa cea cunoscuta."""
    necunoscute = [
        p["grupa"] for p in pagini_golden if parse_titlu(p["grupa"]).tip is TipPagina.NECUNOSCUT
    ]
    assert set(necunoscute) == {"Conferinte si Seminarii"}


@pytest.mark.parametrize(
    ("brut", "asteptat"),
    [
        ("Gr 1", "Gr_1"),
        ("Gr_2", "Gr_2"),
        ("Gr3", "Gr_3"),
        ("gr. 4", "Gr_4"),
        ("", None),
        ("x", None),
    ],
)
def test_normalizare_semigrupa(brut, asteptat):
    assert normalizeaza_semigrupa(brut) == asteptat


def test_normalizare_specializare_tolereaza_scrierile():
    assert normalizeaza_specializare("MATE APL.") == "MATE-APL"
    assert normalizeaza_specializare("mate aplicate") == "MATE-APL"
    assert normalizeaza_specializare("Mate-Info") == "MATE-INFO"
    assert normalizeaza_specializare("ceva") is None
