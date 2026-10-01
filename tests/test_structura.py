"""Structura anului universitar: numararea saptamanilor din perioade, completarea unei
saptamani inceput la mijloc, alerta pentru admini si calendarul ASMI."""

from __future__ import annotations

import re
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from orar.db.models import AncoraSaptamana, Base, PerioadaStructura
from orar.db.structura import alerta_an_curent
from orar.domain.weeks import (
    CalendarAcademic,
    PerioadaAn,
    an_universitar_al,
    ce_lipseste,
    numeroteaza,
)
from tests.conftest import _engine_memorie

ADMIN, PAROLA = "sefa", "o-parola-doar-pentru-teste"
AN = "2026-2027"

#: Exemplul facultatii: semestrul incepe joi, 1 octombrie; luni-miercuri 21-23 decembrie
#: completeaza saptamana 1; dupa vacanta de iarna urmeaza saptamanile 13 si 14.
SEMESTRUL_1 = [
    PerioadaAn("didactica", date(2026, 10, 1), date(2026, 12, 18), 1),
    PerioadaAn("didactica", date(2026, 12, 21), date(2026, 12, 23), 1, saptamana=1),
    PerioadaAn("vacanta", date(2026, 12, 24), date(2027, 1, 10), nume="Vacanța de iarnă"),
    PerioadaAn("didactica", date(2027, 1, 11), date(2027, 1, 22), 1),
    PerioadaAn("sesiune", date(2027, 1, 25), date(2027, 2, 14), 1),
]


@pytest.mark.parametrize(
    ("zi", "numar"),
    [
        (date(2026, 10, 1), 1),  # joi, prima zi
        (date(2026, 10, 2), 1),
        (date(2026, 10, 5), 2),
        (date(2026, 12, 14), 12),
        (date(2026, 12, 18), 12),
        (date(2026, 12, 21), 1),  # completarea saptamanii 1
        (date(2026, 12, 23), 1),
        (date(2027, 1, 11), 13),
        (date(2027, 1, 22), 14),
    ],
)
def test_numerotarea_din_exemplul_facultatii(zi, numar):
    sapt = CalendarAcademic(perioade=SEMESTRUL_1).saptamana(zi)
    assert sapt is not None and sapt.numar == numar
    assert sapt.paritate.value == ("SI" if numar % 2 else "SP")


def test_vacanta_si_sesiunea_nu_se_numara():
    cal = CalendarAcademic(perioade=SEMESTRUL_1)
    assert cal.saptamana(date(2026, 12, 28)) is None
    assert cal.perioada(date(2026, 12, 28)).eticheta == "Vacanța de iarnă"
    assert cal.saptamana(date(2027, 2, 1)) is None
    assert cal.perioada(date(2027, 2, 1)).eticheta == "Sesiune"


def test_structura_are_intaietate_fata_de_ancore():
    from orar.domain.weeks import AncoraSaptamana as Ancora
    from orar.domain.weeks import Paritate

    # o ancora veche, care ar da alt numar prin extrapolare
    ancora = Ancora(inceput=date(2026, 9, 28), numar=5, paritate=Paritate.IMPARA)
    cal = CalendarAcademic(ancore=[ancora], perioade=SEMESTRUL_1)
    assert cal.saptamana(date(2026, 10, 5)).numar == 2
    # in afara anului configurat, ancorele raman rezerva
    assert cal.saptamana(date(2026, 9, 28)).numar == 5


def test_semestrele_se_numara_separat():
    perioade = [
        PerioadaAn("didactica", date(2026, 10, 1), date(2026, 10, 16), 1),
        PerioadaAn("didactica", date(2027, 2, 22), date(2027, 3, 5), 2),
    ]
    zile = numeroteaza(perioade)
    assert zile[date(2026, 10, 15)] == 3
    assert zile[date(2027, 2, 22)] == 1


def test_anul_universitar_incepe_pentru_alerta_pe_30_septembrie():
    assert an_universitar_al(date(2026, 9, 29)) == "2025-2026"
    assert an_universitar_al(date(2026, 9, 30)) == "2026-2027"
    assert an_universitar_al(date(2027, 3, 1)) == "2026-2027"


def test_ce_lipseste_din_structura():
    assert len(ce_lipseste([])) == 6
    lipsa = ce_lipseste(SEMESTRUL_1)
    assert "activitatea didactică din semestrul 2" in lipsa and "vacanțele" not in lipsa
    complet = [
        *SEMESTRUL_1,
        PerioadaAn("didactica", date(2027, 2, 22), date(2027, 6, 4), 2),
        PerioadaAn("restante", date(2027, 9, 1), date(2027, 9, 12)),
        PerioadaAn("licenta", date(2027, 7, 1), date(2027, 7, 10)),
    ]
    assert ce_lipseste(complet) == []


# ------------------------------------------------------------------ in baza si pe pagini


@pytest.fixture
def s(monkeypatch):
    monkeypatch.setenv("ORAR_ADMIN_USER", ADMIN)
    monkeypatch.setenv("ORAR_ADMIN_PAROLA", PAROLA)
    monkeypatch.delenv("ORAR_ADMIN_PAROLA_HASH", raising=False)
    engine = _engine_memorie()
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as sesiune:
        yield sesiune


@pytest.fixture
def client(s):
    from fastapi.testclient import TestClient

    from orar.web import deps
    from orar.web.app import app

    app.dependency_overrides[deps.get_db] = lambda: s
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _csrf(client, cale: str) -> str:  # noqa: ANN001
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


def _intra(client) -> None:  # noqa: ANN001
    client.post(
        "/login",
        data={"csrf": _csrf(client, "/login"), "email": ADMIN, "parola": PAROLA},
        follow_redirects=False,
    )


def _pune(s, perioade: list[PerioadaAn]) -> None:  # noqa: ANN001
    for p in perioade:
        s.add(
            PerioadaStructura(
                an_univ=AN, fel=p.fel, semestru=p.semestru, nume=p.nume or None,
                data_inceput=p.inceput, data_sfarsit=p.sfarsit, saptamana=p.saptamana,
            )
        )  # fmt: skip
    s.commit()


def test_alerta_pana_se_configureaza_anul(s):
    assert alerta_an_curent(s, date(2026, 9, 29))[0] == "2025-2026"
    an, lipsuri = alerta_an_curent(s, date(2026, 9, 30))
    assert an == AN and len(lipsuri) == 6
    _pune(s, SEMESTRUL_1)
    assert len(alerta_an_curent(s, date(2026, 10, 1))[1]) == 3


def test_alerta_apare_doar_adminilor(client, s, monkeypatch):
    from orar.db import structura

    monkeypatch.setattr(structura, "an_universitar_al", lambda _zi: AN)
    assert "nu e configurată" not in client.get("/").text
    _intra(client)
    pagina = client.get("/").text
    assert f"Structura anului universitar {AN} nu e configurată" in pagina
    assert f'href="/admin/an-universitar?an={AN}"' in pagina


def test_adminul_configureaza_anul(client, s):
    _intra(client)
    assert client.get(f"/admin/an-universitar?an={AN}").status_code == 200

    def adauga(**campuri) -> object:  # noqa: ANN003
        date_formular = {
            "csrf": _csrf(client, f"/admin/an-universitar?an={AN}"),
            "an": AN,
            **campuri,
        }
        return client.post(
            "/admin/an-universitar/perioada", data=date_formular, follow_redirects=False
        )

    assert (
        adauga(fel="didactica", semestru="1", de_la="2026-10-01", pana_la="2026-12-18").status_code
        == 303
    )
    r = adauga(
        fel="didactica", semestru="1", de_la="2026-12-21", pana_la="2026-12-23", saptamana="1"
    )
    assert r.status_code == 303
    # ce nu are sens e refuzat
    assert (
        adauga(fel="vacanta", de_la="2026-12-24", pana_la="2027-01-10", saptamana="3").status_code
        == 400
    )
    assert (
        adauga(fel="didactica", semestru="", de_la="2027-02-22", pana_la="2027-06-04").status_code
        == 400
    )
    assert (
        adauga(fel="sesiune", de_la="2028-01-01", pana_la="2028-01-20").status_code == 400
    )  # alt an
    assert adauga(fel="sesiune", de_la="2027-02-01", pana_la="2027-01-20").status_code == 400

    pagina = client.get(f"/admin/an-universitar?an={AN}").text
    assert "Săptămâna 12" in pagina and "joi 01.10 - vin 02.10, lun 21.12 - mie 23.12" in pagina

    # numarul saptamanii de pe site vine acum din structura
    from orar.web.deps import calendar

    assert calendar(s).saptamana(date(2026, 12, 22)).numar == 1

    p = s.scalar(select(PerioadaStructura).where(PerioadaStructura.saptamana == 1))
    client.post(
        f"/admin/an-universitar/perioada/{p.id}/sterge", data={"csrf": _csrf(client, "/admin")}
    )
    assert s.get(PerioadaStructura, p.id) is None


def test_calendarul_asmi_arata_saptamanile_si_vacantele(client, s):
    _pune(s, SEMESTRUL_1)
    r = client.get("/calendar-asmi?luna=2026-12")
    assert r.status_code == 200
    assert 'title="Săptămâna 12, pară"' in r.text and 'title="Săptămâna 1, impară"' in r.text
    assert "Vacanța de iarnă" in r.text and "perioada-vacanta" in r.text
    assert f"Anul universitar {AN}" in r.text
    assert '<details class="mai-multe structura-an">' in r.text  # inchis implicit


def test_ancorele_isi_iau_anul_si_semestrul_din_data(s):
    from orar.domain.weeks import AncoraSaptamana as Ancora
    from orar.domain.weeks import Paritate
    from orar.worker.sync import salveaza_ancore

    salveaza_ancore(
        s,
        [
            Ancora(inceput=date(2026, 10, 12), numar=3, paritate=Paritate.IMPARA),
            Ancora(inceput=date(2027, 4, 5), numar=7, paritate=Paritate.IMPARA),
        ],
    )
    randuri = {(r.an_univ, r.semestru, r.numar) for r in s.scalars(select(AncoraSaptamana))}
    assert randuri == {("2026-2027", 1, 3), ("2026-2027", 2, 7)}


def test_completarea_saptamanii_1_se_recunoaste_singura():
    """Structura scrisa exact ca in PDF-ul facultatii (01.10-23.12 activitate didactica),
    fara nicio completare pusa de mana, da numerotarea anuntata."""
    perioade = [
        PerioadaAn("didactica", date(2026, 10, 1), date(2026, 12, 23), 1),
        PerioadaAn("vacanta", date(2026, 12, 24), date(2027, 1, 10)),
        PerioadaAn("didactica", date(2027, 1, 11), date(2027, 1, 24), 1),
    ]
    cal = CalendarAcademic(perioade=perioade)
    assert [
        cal.saptamana(d).numar
        for d in (date(2026, 12, 14), date(2026, 12, 21), date(2027, 1, 11), date(2027, 1, 18))
    ] == [12, 1, 13, 14]


def test_zilele_libere_si_anii_terminali():
    perioade = [
        PerioadaAn("didactica", date(2026, 10, 1), date(2026, 12, 23), 1),
        PerioadaAn("liber", date(2026, 11, 30), date(2026, 12, 1), nume="Sfântul Andrei"),
        PerioadaAn("didactica", date(2027, 2, 22), date(2027, 4, 30), 2),
        PerioadaAn("didactica", date(2027, 5, 10), date(2027, 6, 6), 2, pentru="neterminali"),
        PerioadaAn(
            "licenta",
            date(2027, 5, 10),
            date(2027, 5, 23),
            pentru="terminali",
            nume="Elaborarea lucrării",
        ),
    ]
    cal = CalendarAcademic(perioade=perioade)
    # ziua libera nu muta numaratoarea, dar apare ca perioada a zilei
    assert cal.saptamana(date(2026, 11, 30)).numar == 10
    assert cal.perioada(date(2026, 11, 30)).eticheta == "Sfântul Andrei"
    # saptamanile se numara dupa anii obisnuiti; ce e doar al terminalilor ramane in calendar
    assert cal.saptamana(date(2027, 5, 10)).numar == 11
    assert cal.perioada(date(2027, 5, 10)).fel == "didactica"
    assert any(p.pentru == "terminali" for p in cal.perioade_zilei(date(2027, 5, 10)))


def test_un_an_scris_din_doua_cifre_e_explicat(client, s):
    _intra(client)
    r = client.post(
        "/admin/an-universitar/perioada",
        data={"csrf": _csrf(client, f"/admin/an-universitar?an={AN}"), "an": AN, "fel": "didactica",
              "semestru": "1", "de_la": "0027-01-11", "pana_la": "2027-02-14"},
    )  # fmt: skip
    assert r.status_code == 400 and "11.01.0027 are anul greșit" in r.text
