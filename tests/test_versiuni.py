"""Versiunile anterioare: arhivarea la reingest si afisarea lor pe /grupa/{id}."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

import pytest
from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import sessionmaker

from orar.db.models import (
    Base,
    Grupa,
    Ora,
    OraArhivata,
    OraGrupa,
    OraGrupaArhivata,
    Profesor,
    Sala,
    SursaOrar,
    VersiuneOrar,
)
from orar.db.queries import gaseste_grupa, ore_pentru_grupa
from orar.db.versiuni import COLOANE_COPIATE, _semnatura, arhiveaza_semestrul
from orar.ingest.consolidate import consolideaza
from orar.ingest.load import incarca_pagini
from orar.worker.sync import _sterge_semestrul, curata_orfanii
from tests.conftest import _engine_memorie

PUBLICAT_VECHI = datetime(2026, 3, 1, 12, 0)
PUBLICAT_NOU = datetime(2026, 4, 26, 19, 30)


@pytest.fixture
def s(pagini_golden):
    """Baza proprie fiecarui test, cu cheile straine impuse ca in productie.

    Fara `foreign_keys=ON`, stergerea orelor n-ar sterge si legaturile ORA_GRUPA, iar
    un profesor folosit doar de arhiva s-ar putea sterge fara nicio eroare.
    """
    engine = _engine_memorie()

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as sesiune:
        incarca_pagini(sesiune, pagini_golden)
        consolideaza(sesiune, an_universitar="2025-2026")
        sesiune.add(
            SursaOrar(
                an_univ="2025-2026",
                semestru=2,
                fel="grupe",
                url="https://bit.ly/x",
                actualizat=PUBLICAT_VECHI,
                ingestat_la=PUBLICAT_VECHI,
            )
        )
        sesiune.commit()
        yield sesiune


def _arhiveaza(s) -> VersiuneOrar | None:  # noqa: ANN001
    return arhiveaza_semestrul(
        s,
        an_universitar="2025-2026",
        semestru=2,
        publicat=PUBLICAT_VECHI,
        inlocuit_de=PUBLICAT_NOU,
    )


def _numara(s, model) -> int:  # noqa: ANN001
    return s.scalar(select(func.count()).select_from(model))


def test_arhiva_copiaza_toate_coloanele_orei():
    """O coloana noua pe ORA trebuie adaugata si in arhiva, altfel s-ar pierde tacut."""
    coloane_ora = set(inspect(Ora).columns.keys()) - {"id", "corectie_id"}
    assert coloane_ora == set(COLOANE_COPIATE)
    assert set(COLOANE_COPIATE) <= set(inspect(OraArhivata).columns.keys())


def test_arhivarea_copiaza_orele_si_legaturile(s):
    ore, legaturi = _numara(s, Ora), _numara(s, OraGrupa)
    assert legaturi > 0  # altfel testul n-ar verifica nimic despre legaturi

    v = _arhiveaza(s)

    assert v is not None and v.nr_ore == ore
    assert v.publicat == PUBLICAT_VECHI
    assert _numara(s, OraArhivata) == ore
    assert _numara(s, OraGrupaArhivata) == legaturi


def test_aceeasi_publicare_nu_devine_versiune(s):
    """Reluarea aceleiasi publicari inlocuieste o citire, nu un orar."""
    assert (
        arhiveaza_semestrul(
            s,
            an_universitar="2025-2026",
            semestru=2,
            publicat=PUBLICAT_VECHI,
            inlocuit_de=PUBLICAT_VECHI,
        )
        is None
    )
    assert _arhiveaza(s) is not None
    assert _arhiveaza(s) is None  # a doua oara: versiunea exista deja
    assert _numara(s, VersiuneOrar) == 1


def test_semestrul_gol_nu_devine_versiune(s):
    _sterge_semestrul(s, an_universitar="2025-2026", semestru=2)
    assert _arhiveaza(s) is None


def test_orarul_arhivat_e_cel_de_atunci_pentru_fiecare_grupa(s):
    """Dupa arhivare si stergere, fiecare grupa isi vede in arhiva exact orarul vechi --
    inclusiv orele mostenite de la serie si optionalele legate prin ORA_GRUPA."""
    grupe = list(s.scalars(select(Grupa)))
    inainte = {g.id: Counter(_semnatura(o) for o in ore_pentru_grupa(s, g.id)) for g in grupe}

    v = _arhiveaza(s)
    _sterge_semestrul(s, an_universitar="2025-2026", semestru=2)
    assert _numara(s, Ora) == 0

    for g in grupe:
        arhivat = Counter(_semnatura(o) for o in ore_pentru_grupa(s, g.id, versiune_id=v.id))
        assert arhivat == inainte[g.id], g.nume


def test_curatarea_pastreaza_ce_foloseste_arhiva(s):
    profesori, sali = _numara(s, Profesor), _numara(s, Sala)

    _arhiveaza(s)
    _sterge_semestrul(s, an_universitar="2025-2026", semestru=2)
    sterse = curata_orfanii(s)

    assert sterse == {}
    assert (_numara(s, Profesor), _numara(s, Sala)) == (profesori, sali)


# ---------------------------------------------------------------------------
# Interfata
# ---------------------------------------------------------------------------


@pytest.fixture
def client(s):
    from fastapi.testclient import TestClient

    from orar.web import deps
    from orar.web.app import app

    app.dependency_overrides[deps.get_db] = lambda: s
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _muta_o_ora(s) -> None:  # noqa: ANN001
    """Simuleaza o publicare noua: o ora proprie a grupei 244 schimba sala."""
    grupa = gaseste_grupa(s, "244")
    ora = s.scalar(select(Ora).where(Ora.grupa_id == grupa.id).limit(1))
    ora.sala = s.scalar(select(Sala).where(Sala.id != ora.sala_id).limit(1))
    s.flush()


def test_fara_versiuni_panoul_spune_ca_e_doar_cea_actuala(client):
    r = client.get("/grupa/244")
    assert r.status_code == 200
    assert 'class="versiuni"' in r.text
    assert "un singur orar" in r.text


def test_panoul_listeaza_versiunea_si_diferentele(client, s):
    v = _arhiveaza(s)
    _muta_o_ora(s)

    r = client.get("/grupa/244")
    assert f"?versiune={v.id}" in r.text
    assert "01.03.2026" in r.text
    # sala schimbata = o activitate scoasa si una aparuta
    assert '<span class="plus">+1</span>' in r.text
    assert '<span class="minus">−1</span>' in r.text


def test_grupa_neschimbata_vede_versiunea_ca_identica(client, s):
    _arhiveaza(s)
    _muta_o_ora(s)
    r = client.get("/grupa/141")
    assert "identică" in r.text


def test_versiunea_aleasa_arata_orarul_de_atunci(client, s):
    v = _arhiveaza(s)
    grupa = gaseste_grupa(s, "244")
    vechi = len(ore_pentru_grupa(s, grupa.id))
    _sterge_semestrul(s, an_universitar="2025-2026", semestru=2)

    r = client.get(f"/grupa/244?versiune={v.id}")
    assert r.status_code == 200
    assert "versiune anterioară" in r.text
    assert f"<strong>{vechi}</strong> activități" in r.text
    # filtrele de semigrupa pastreaza versiunea aleasa (`&` iese escapat in atribut)
    assert f"?semigrupa=Gr_1&amp;versiune={v.id}" in r.text
    assert f"?semigrupa=Gr_2&amp;versiune={v.id}" in r.text


def test_versiune_inexistenta_da_404(client):
    assert client.get("/grupa/244?versiune=9999").status_code == 404


# ---------------------------------------------------------------------------
# Versiunile pe pagina principala si pe sali
# ---------------------------------------------------------------------------


def _ora_mutata(s) -> tuple[Sala, Sala]:  # noqa: ANN001
    """Ca `_muta_o_ora`, dar spune si din ce sala in ce sala."""
    grupa = gaseste_grupa(s, "244")
    ora = s.scalar(select(Ora).where(Ora.grupa_id == grupa.id).limit(1))
    veche = ora.sala
    noua = s.scalar(select(Sala).where(Sala.id != ora.sala_id, Sala.tip == "fizica").limit(1))
    ora.sala = noua
    s.flush()
    return veche, noua


def test_pagina_principala_are_panoul_de_versiuni(client, s):
    assert "un singur orar" in client.get("/").text

    v = _arhiveaza(s)
    _ora_mutata(s)
    acasa = client.get("/").text
    assert f'href="?versiune={v.id}"' in acasa
    assert "01.03.2026" in acasa
    # in tot orarul: o activitate scoasa (din sala veche) si una aparuta (in cea noua)
    assert '<span class="plus">+1</span>' in acasa and '<span class="minus">−1</span>' in acasa


def test_versiunea_aleasa_pe_prima_pagina_se_pastreaza_in_linkuri(client, s):
    v = _arhiveaza(s)
    acasa = client.get(f"/?versiune={v.id}").text
    assert "versiune anterioară" in acasa
    assert f'href="/grupa/244?versiune={v.id}"' in acasa
    assert f'href="/sala?versiune={v.id}"' in acasa
    assert re.search(rf'href="/sala/[^"?]+\?versiune={v.id}"', acasa)
    # fara versiune aleasa, linkurile sunt cele obisnuite
    assert 'href="/grupa/244"' in client.get("/").text


def test_sala_intr_o_versiune_anterioara(client, s):
    v = _arhiveaza(s)
    veche, noua = _ora_mutata(s)

    def activitati(cale: str) -> int:
        return int(re.search(r"<strong>(\d+)</strong> activități", client.get(cale).text)[1])

    # sala veche a pierdut o activitate fata de versiunea arhivata, cea noua a castigat una
    assert (
        activitati(f"/sala/{veche.slug}?versiune={v.id}") == activitati(f"/sala/{veche.slug}") + 1
    )
    assert activitati(f"/sala/{noua.slug}?versiune={v.id}") == activitati(f"/sala/{noua.slug}") - 1

    pagina = client.get(f"/sala/{veche.slug}").text
    assert f'href="?versiune={v.id}"' in pagina
    assert '<span class="minus">−1</span>' in pagina  # era in versiune, nu mai e acum

    arhivata = client.get(f"/sala/{veche.slug}?versiune={v.id}").text
    assert "versiune anterioară" in arhivata
    # blocurile din grila duc la grupa din aceeasi versiune
    assert re.search(rf'href="/grupa/[^"]+versiune={v.id}"', arhivata)


def test_pagina_grupei_duce_la_sala_din_aceeasi_versiune(client, s):
    v = _arhiveaza(s)
    pagina = client.get(f"/grupa/244?versiune={v.id}").text
    assert re.search(rf'href="/sala/[^"?]+\?versiune={v.id}"', pagina)
    assert not re.search(r'class="bloc[^>]*href="/sala/[^"?]+"', pagina)


def test_lista_de_sali_intr_o_versiune(client, s):
    v = _arhiveaza(s)
    _sterge_semestrul(s, an_universitar="2025-2026", semestru=2)
    gol = client.get("/sala").text
    arhivat = client.get(f"/sala?versiune={v.id}").text
    assert "versiune anterioară" in arhivat
    # fara ore actuale totul e 0; in versiune salile au ocuparea de atunci
    assert re.findall(r"<td>(\d+)/60</td>", gol) and set(re.findall(r"<td>(\d+)/60</td>", gol)) == {
        "0"
    }
    assert any(n != "0" for n in re.findall(r"<td>(\d+)/60</td>", arhivat))


@pytest.mark.parametrize("cale", ["/", "/sala", "/sala/Amf.501"])
def test_versiune_inexistenta_da_404_peste_tot(client, cale):
    assert client.get(f"{cale}?versiune=9999").status_code == 404
