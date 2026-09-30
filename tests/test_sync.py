"""Sincronizarea: ce se salveaza, ce se reia si ce nu.

Testele nu ating reteaua si nu pornesc captura: `sincronizeaza` primeste HTML-ul direct, iar
partea scumpa (captura + OCR) se atinge doar cand exista o schimbare -- ceea ce e chiar
comportamentul verificat aici, cu `doar_verifica`.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from orar.db.models import AncoraSaptamana, Base, SursaOrar
from orar.worker.sync import calendar_din_baza, sincronizeaza

PAGINA = """
<h2>Semestrul II</h2>
<p>Saptamana 06.04.2026 - 09.04.2026 este saptamana impara (sapt 7).
   Saptamana 20.04.2026 - 24.04.2026 este saptamana para (sapt 8).</p>
<a href="https://bit.ly/g2">Orarul grupelor</a> (actualizat 26.04.2026, ora 19:30)
<a href="https://bit.ly/p2">Orarul profesorilor</a> (actualizat 26.04.2026, ora 19:30)
<h2>Semestrul I</h2>
<a href="https://bit.ly/g1">Orarul grupelor</a> (actualizat 02.12.2025, ora 21:00)
"""


@pytest.fixture
def s():
    """Baza goala, proprie fiecarui test.

    Nu folosim fixtura `db` comuna: sincronizarea *scrie* (surse, ancore), iar o baza
    impartita ar face testele sa depinda de ordinea in care ruleaza.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as sesiune:
        yield sesiune


def test_salveaza_ancorele_si_sursele(s):
    rap = sincronizeaza(s, html=PAGINA, doar_verifica=True)
    assert rap.verificat
    assert rap.ancore == 2
    assert [
        (a.numar, a.paritate)
        for a in s.scalars(select(AncoraSaptamana).order_by(AncoraSaptamana.inceput))
    ] == [
        (7, "SI"),
        (8, "SP"),
    ]
    surse = list(s.scalars(select(SursaOrar)))
    assert [x.url for x in surse] == ["https://bit.ly/g2"]


def test_alege_semestrul_pe_care_facultatea_il_tine_la_zi(s):
    """Pagina are si semestrul I; reluarea trebuie sa tinteasca semestrul II."""
    rap = sincronizeaza(s, html=PAGINA, doar_verifica=True)
    assert len(rap.schimbari) == 1
    assert "sem 2" in rap.schimbari[0]


def test_a_doua_verificare_nu_mai_are_ce_relua(s):
    sincronizeaza(s, html=PAGINA, doar_verifica=True)
    # Simulam un ingest reusit: retinem data publicata.
    sursa = s.scalar(select(SursaOrar))
    sursa.ingestat_la = sursa.actualizat
    s.flush()

    rap = sincronizeaza(s, html=PAGINA, doar_verifica=True)
    assert rap.schimbari == []


def test_o_publicare_mai_noua_declanseaza_reluarea(s):
    sincronizeaza(s, html=PAGINA, doar_verifica=True)
    sursa = s.scalar(select(SursaOrar))
    sursa.ingestat_la = datetime(2026, 3, 1, 8, 0)
    s.flush()

    rap = sincronizeaza(s, html=PAGINA, doar_verifica=True)
    assert len(rap.schimbari) == 1
    assert "26.04.2026" in rap.schimbari[0]


def test_verificarea_nu_atinge_captura(s, monkeypatch):
    """`--doar-verifica` trebuie sa fie ieftin: nicio captura, oricat de invechit ar fi."""

    def explodeaza(*a, **k):
        raise AssertionError("nu trebuia sa se captureze nimic")

    monkeypatch.setattr("orar.ingest.capture.captureaza_orar", explodeaza)
    sincronizeaza(s, html=PAGINA, doar_verifica=True)


def test_verificarea_actualizeaza_url_ul_chiar_daca_nu_reingesteaza(s):
    """Daca facultatea muta linkul fara sa schimbe data, vrem linkul nou in baza."""
    sincronizeaza(s, html=PAGINA, doar_verifica=True)
    sursa = s.scalar(select(SursaOrar))
    sursa.ingestat_la = sursa.actualizat
    s.flush()

    sincronizeaza(s, html=PAGINA.replace("bit.ly/g2", "bit.ly/ALTUL"), doar_verifica=True)
    assert s.scalar(select(SursaOrar)).url == "https://bit.ly/ALTUL"


def test_calendarul_se_reconstruieste_din_baza(s):
    sincronizeaza(s, html=PAGINA, doar_verifica=True)
    cal = calendar_din_baza(s)
    assert cal is not None
    from datetime import date

    sapt = cal.saptamana(date(2026, 4, 6))
    assert sapt.numar == 7 and sapt.exact


def test_fara_ancore_in_baza_calendarul_e_None(s):
    assert calendar_din_baza(s) is None


def test_pagina_fara_orare_nu_sterge_nimic(s):
    sincronizeaza(s, html=PAGINA, doar_verifica=True)
    rap = sincronizeaza(s, html="<p>mentenanta</p>", doar_verifica=True)
    assert rap.avertismente
    assert s.scalar(select(SursaOrar)) is not None, "sursa cunoscuta nu trebuie pierduta"


def test_curatarea_orfanilor(db):
    """Dupa o reluare, entitatile la care nu mai trimite nicio ora dispar.

    Ingestul insereaza si valorile neconfirmate -- altfel coada de verificare n-ar avea ce
    arata. Daca ar ramane acolo dupa fiecare reluare, tabela ar creste la infinit.
    """
    from sqlalchemy import func

    from orar.db.models import Profesor
    from orar.worker.sync import curata_orfanii

    inainte = db.scalar(select(func.count()).select_from(Profesor))
    db.add(Profesor(nume="Fantoma X", slug="fantoma-x"))
    db.flush()
    assert db.scalar(select(func.count()).select_from(Profesor)) == inainte + 1

    assert curata_orfanii(db).get("PROFESOR") == 1
    assert db.scalar(select(func.count()).select_from(Profesor)) == inainte


def test_curatarea_nu_atinge_entitatile_folosite(db):
    """Un profesor cu ore ramane, evident -- altfel am sterge exact datele bune."""
    from sqlalchemy import func

    from orar.db.models import Ora, Profesor
    from orar.worker.sync import curata_orfanii

    curata_orfanii(db)
    folositi = db.scalar(
        select(func.count(func.distinct(Ora.profesor_id))).where(Ora.profesor_id.is_not(None))
    )
    assert db.scalar(select(func.count()).select_from(Profesor)) == folositi


def test_fara_orarul_profesorilor_nu_se_reia_nimic(s, monkeypatch):
    """Verificarea incrucisata e obligatorie: semestrul 1 n-are orarul profesorilor pe pagina,
    deci orarul lui nu se captureaza si nu se inlocuieste."""

    def explodeaza(*a, **k):
        raise AssertionError("nu trebuia sa se captureze nimic")

    monkeypatch.setattr("orar.ingest.capture.captureaza_orar", explodeaza)
    rap = sincronizeaza(s, html=PAGINA, semestru=1)
    assert rap.schimbari and not rap.ingestate
    assert any("Orarul profesorilor" in a for a in rap.avertismente)


def test_orarul_profesorilor_care_nu_se_poate_captura_anuleaza_tot(s, monkeypatch):
    from orar.worker import sync

    def ingest_fals(s, sursa, *, rap, **k):  # noqa: ANN001, ANN003, ANN202
        s.add(
            AncoraSaptamana(
                an_univ="x", semestru=9, inceput=date(2026, 1, 5), numar=1, paritate="SI"
            )
        )
        rap.ingestate.append("sem 2 / grupe")

    def crosscheck_picat(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise sync.EroareCrosscheck("captura orarului profesorilor: timeout")

    monkeypatch.setattr(sync, "_ingesteaza", ingest_fals)
    monkeypatch.setattr(sync, "_crosscheck", crosscheck_picat)
    rap = sincronizeaza(s, html=PAGINA)
    assert rap.ingestate == []
    assert any("a ramas cel de dinainte" in a for a in rap.avertismente)
    # ce scrisese ingestul a fost dat inapoi
    assert s.scalar(select(AncoraSaptamana).where(AncoraSaptamana.semestru == 9)) is None
