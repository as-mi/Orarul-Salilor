"""Rutele web."""

from __future__ import annotations

import pytest


def test_pagina_principala(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Orarul Sălilor" in r.text
    assert "/grupa/244" in r.text


@pytest.mark.parametrize("cale", ["/grupa/244", "/grupa/161", "/grupa/405", "/grupa/seria-24"])
def test_rute_de_grupa(client, cale):
    assert client.get(cale).status_code == 200


def test_grupa_arata_orele_proprii_si_pe_cele_de_serie(client):
    r = client.get("/grupa/244")
    assert r.status_code == 200
    assert "Seria 24" in r.text
    assert "moștenește de la" in r.text
    # semigrupele se aleg cu butoane pe pagina grupei, nu mai au pagina lor
    assert 'href="?semigrupa=Gr_1' in r.text and 'href="?semigrupa=Gr_2' in r.text
    assert 'href="/grupa/244-1"' not in r.text


def test_pagina_seriei_listeaza_grupele_nu_semigrupele(client):
    r = client.get("/grupa/seria-24")
    assert 'href="/grupa/241"' in r.text and 'href="/grupa/244"' in r.text
    assert 'href="/grupa/244-1"' not in r.text


def test_pagina_veche_de_semigrupa_duce_la_grupa(client):
    r = client.get("/grupa/244-1?saptamana=true&zi=2026-04-22", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/grupa/244?saptamana=true&zi=2026-04-22&semigrupa=Gr_1"
    # urmata, adresa alege Gr_1 si o tine minte
    dupa = client.get("/grupa/244-2")
    assert dupa.status_code == 200
    assert 'class="filtru activ"\n             href="?semigrupa=Gr_2' in dupa.text


def test_grupa_accepta_si_slug_si_nume(client):
    assert client.get("/grupa/244").status_code == 200
    assert client.get("/grupa/Seria 24").status_code == 200


def test_filtrul_de_semigrupa(client):
    complet = client.get("/grupa/244").text
    filtrat = client.get("/grupa/244?semigrupa=Gr_1").text
    assert complet != filtrat


def test_sala_afiseaza_materie_profesor_grupa(client):
    r = client.get("/sala/Amf.701")
    assert r.status_code == 200
    # cerinta: sa se vada CE materie, CE profesor, CARE grupa
    assert "ocupare" in r.text
    assert "Seria 13" in r.text or "Seria 24" in r.text


@pytest.mark.parametrize("ident", ["Amf.701", "amf-701", "701", "L.410", "L-410"])
def test_sala_se_gaseste_dupa_mai_multe_forme(client, ident):
    """Normalizarea trebuie sa faca /sala/L-410 si /sala/L.410 acelasi lucru."""
    assert client.get(f"/sala/{ident}").status_code == 200


def test_lista_de_sali(client):
    r = client.get("/sala")
    assert r.status_code == 200
    assert "Amf.701" in r.text


def test_404_pe_identificatori_inexistenti(client):
    assert client.get("/grupa/nu-exista-asa-ceva").status_code == 404
    assert client.get("/sala/nu-exista-asa-ceva").status_code == 404


def test_cautarea_gaseste_grupe_si_sali(client):
    assert "244" in client.get("/cauta?q=244").text
    assert "Amf.701" in client.get("/cauta?q=Amf.70").text
    assert client.get("/cauta?q=").text.strip() == ""


def test_toate_grupele_se_randeaza(client, db):
    """Nicio formatiune din baza nu trebuie sa dea eroare la randare."""
    from sqlalchemy import select

    from orar.db.models import Grupa

    # semigrupele trimit la grupa lor (308), deci le urmam pana la pagina finala
    slugs = [g.slug for g in db.execute(select(Grupa)).scalars()]
    esecuri = [s for s in slugs if client.get(f"/grupa/{s}").status_code != 200]
    assert esecuri == []


def test_toate_salile_se_randeaza(client, db):
    from sqlalchemy import select

    from orar.db.models import Sala

    slugs = [s.slug for s in db.execute(select(Sala)).scalars()]
    esecuri = [s for s in slugs if client.get(f"/sala/{s}").status_code != 200]
    assert esecuri == []


def test_anul_fara_grupe_apare_pe_prima_pagina(client):
    """CTI anul 4 are in orar doar pachete de optionale, nicio grupa. Trebuie sa se ajunga
    totusi la pagina anului: de pe prima pagina si din cautare."""
    acasa = client.get("/").text
    assert 'href="/grupa/cti-an-4"' in acasa
    assert 'href="/grupa/optionale-an-iv-cti-1"' in acasa
    assert client.get("/grupa/cti-an-4").status_code == 200
    assert "/grupa/cti-an-4" in client.get("/cauta?q=anul 4").text


def test_pagina_principala_grupeaza_pe_nivel_domeniu_si_an(client):
    r = client.get("/")
    assert r.status_code == 200
    # selectorul in trepte, cu cele doua sectiuni
    assert 'id="tab-grupe"' in r.text and 'id="tab-sali"' in r.text
    for treapta in ("nivel", "domeniu", "an", "grupa", "sala"):
        assert f'id="alege-{treapta}"' in r.text
    # lista de dedesubt: licenta inaintea masterului, domeniul ca titlu, anii sub el
    assert r.text.index(">Licență<") < r.text.index(">Master<")
    assert "<h4>Informatică</h4>" in r.text
    assert 'href="/grupa/info-an-2"' in r.text and ">Anul 2<" in r.text
    # fara liniute lungi pe pagina
    assert "—" not in r.text and "–" not in r.text


def test_salile_sunt_grupate_pe_etaje(client):
    r = client.get("/")
    # etajul e prima cifra din numele salii: Amf.701 -> etajul 7
    assert r.text.index("<h4>Etajul 1</h4>") < r.text.index("<h4>Etajul 7</h4>")
    etajul_7 = r.text[r.text.index("<h4>Etajul 7</h4>") :]
    assert "Amfiteatre" in etajul_7 and 'href="/sala/amf-701"' in etajul_7
