"""Scriitorul ICS: structura RFC 5545, plierea liniilor, fusul orar si escape-ul.

Fara retea si fara baza: `web/ics.py` e o functie pura peste `IntrareCalendar`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from orar.web.ics import IntrareCalendar, escapeaza, pliaza, scrie_calendar

ACUM = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


def _intrare(**schimbari) -> IntrareCalendar:  # noqa: ANN003
    date_ = {
        "uid": "ora-1-20260302@orar.asmi.ro",
        "inceput": datetime(2026, 3, 2, 10, 0),
        "sfarsit": datetime(2026, 3, 2, 12, 0),
        "titlu": "Geometrie (curs)",
        "loc": "Amf.501",
        "descriere": "Stamate D",
    }
    date_.update(schimbari)
    return IntrareCalendar(**date_)


def _text(corp: bytes) -> str:
    return corp.decode("utf-8")


def _evenimente(text: str) -> list[list[str]]:
    """Liniile fiecarui VEVENT, cu plierile dezfacute."""
    linii = text.replace("\r\n ", "").split("\r\n")
    bucati: list[list[str]] = []
    curent: list[str] | None = None
    for linie in linii:
        if linie == "BEGIN:VEVENT":
            curent = []
        elif linie == "END:VEVENT":
            assert curent is not None
            bucati.append(curent)
            curent = None
        elif curent is not None:
            curent.append(linie)
    return bucati


def test_calendarul_are_structura_rfc_si_terminatoare_crlf():
    corp = scrie_calendar("Orar · 244", "Semestrul 2", [_intrare()], acum=ACUM)
    text = _text(corp)

    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    assert "\n" not in text.replace("\r\n", "")
    assert "VERSION:2.0" in text
    assert "CALSCALE:GREGORIAN" in text
    # Fara METHOD: ar transforma calendarul in invitatie la unii clienti.
    assert "METHOD:" not in text

    [eveniment] = _evenimente(text)
    proprietati = [linie.split(":", 1)[0] for linie in eveniment]
    for ceruta in ("UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY", "LOCATION", "DESCRIPTION"):
        assert ceruta in proprietati
    assert "STATUS:CONFIRMED" in eveniment


def test_ora_romanici_se_scrie_in_utc_iarna_si_vara():
    iarna = scrie_calendar(
        "x", "x", [_intrare(inceput=datetime(2026, 1, 12, 8), sfarsit=datetime(2026, 1, 12, 10))],
        acum=ACUM,
    ).decode()
    vara = scrie_calendar(
        "x", "x", [_intrare(inceput=datetime(2026, 7, 6, 8), sfarsit=datetime(2026, 7, 6, 10))],
        acum=ACUM,
    ).decode()

    assert "DTSTART:20260112T060000Z" in iarna  # EET, UTC+2
    assert "DTEND:20260112T080000Z" in iarna
    assert "DTSTART:20260706T050000Z" in vara  # EEST, UTC+3
    assert "DTEND:20260706T070000Z" in vara


def test_plicarea_nu_rupe_diacriticele_si_respecta_75_de_octeti():
    titlu = "Programare orientată pe obiecte — laborator șî țâ (Grupa 244/2, seria 24)"
    corp = scrie_calendar("Orar", "descriere", [_intrare(titlu=titlu)], acum=ACUM)
    text = corp.decode("utf-8")  # decodarea ar esua pe un caracter taiat

    for linie in text.split("\r\n"):
        assert len(linie.encode("utf-8")) <= 75, linie

    [eveniment] = _evenimente(text)
    summary = next(linie for linie in eveniment if linie.startswith("SUMMARY:"))
    assert summary == f"SUMMARY:{escapeaza(titlu)}"  # plierea s-a dezfacut exact


def test_plicarea_unei_linii_foarte_lungi_are_continuari_de_maxim_74_de_octeti():
    bucati = pliaza("X" * 200)
    assert len(bucati) > 1
    assert all(len(b.encode("utf-8")) <= 75 for b in bucati)
    # continuarile pierd un octet pentru spatiul de la inceputul liniei
    assert all(len(b.encode("utf-8")) <= 74 for b in bucati[1:])
    assert "".join(bucati) == "X" * 200


@pytest.mark.parametrize(
    ("brut", "escapat"),
    [
        ("a;b", "a\\;b"),
        ("a,b", "a\\,b"),
        ("a\\b", "a\\\\b"),
        ("a\nb", "a\\nb"),
        ("a\r\nb", "a\\nb"),
    ],
)
def test_escape_rfc(brut, escapat):
    assert escapeaza(brut) == escapat


def test_textul_e_escapat_in_proprietati():
    corp = scrie_calendar(
        "Orar, 244; seria 24",
        "linia 1\nlinia 2",
        [_intrare(titlu="Analiză; algebră, geometrie")],
        acum=ACUM,
    ).decode()
    assert "X-WR-CALNAME:Orar\\, 244\\; seria 24" in corp
    assert "X-WR-CALDESC:linia 1\\nlinia 2" in corp
    assert "SUMMARY:Analiză\\; algebră\\, geometrie" in corp


def test_eveniment_toata_ziua_foloseste_date_si_dtend_exclusiv():
    corp = scrie_calendar(
        "x",
        "x",
        [_intrare(inceput=date(2026, 3, 6), sfarsit=date(2026, 3, 6), toata_ziua=True)],
        acum=ACUM,
    ).decode()
    assert "DTSTART;VALUE=DATE:20260306" in corp
    assert "DTEND;VALUE=DATE:20260307" in corp


def test_un_sfarsit_inainte_de_inceput_devine_aceeasi_zi():
    corp = scrie_calendar(
        "x",
        "x",
        [_intrare(inceput=date(2026, 3, 6), sfarsit=date(2026, 3, 1), toata_ziua=True)],
        acum=ACUM,
    ).decode()
    assert "DTSTART;VALUE=DATE:20260306" in corp
    assert "DTEND;VALUE=DATE:20260307" in corp


def test_statusul_anulat_ajunge_in_vevent():
    corp = scrie_calendar("x", "x", [_intrare(status="CANCELLED")], acum=ACUM).decode()
    assert "STATUS:CANCELLED" in corp


def test_dtstamp_este_momentul_dat_si_nu_ora_locala():
    corp = scrie_calendar("x", "x", [_intrare()], acum=ACUM).decode()
    assert "DTSTAMP:20261026T090000Z" in corp


def test_calendarul_fara_evenimente_e_valid():
    text = scrie_calendar("Gol", "nimic", [], acum=ACUM).decode()
    assert text == (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//ASMI//Orarul Salilor//RO\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "X-WR-CALNAME:Gol\r\n"
        "X-WR-CALDESC:nimic\r\n"
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H\r\n"
        "X-PUBLISHED-TTL:PT1H\r\n"
        "END:VCALENDAR\r\n"
    )
