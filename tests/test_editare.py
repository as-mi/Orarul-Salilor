"""Sesizarile (oricine semnaleaza, adminul trateaza) si editarea orarului de catre admin."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker

from orar.db import corectii
from orar.db.models import Base, Corectie, Ora, Sala, Sesizare, User
from orar.db.queries import gaseste_grupa, ore_pentru_grupa
from orar.ingest.consolidate import consolideaza
from orar.ingest.load import incarca_pagini
from orar.web.auth import hash_parola
from orar.web.routers import sesizari as ruta_sesizari
from orar.worker.sync import _sterge_semestrul
from tests.conftest import _engine_memorie

ADMIN, PAROLA_ADMIN = "sefu", "parola-adminului-de-test"
EMAIL, PAROLA = "ana@example.com", "parola-anei-lunga"


@pytest.fixture
def s(pagini_golden, monkeypatch):
    """Baza proprie fiecarui test: editarile schimba orarul, deci nu pot lucra pe cea comuna."""
    monkeypatch.setenv("ORAR_ADMIN_USER", ADMIN)
    monkeypatch.setenv("ORAR_ADMIN_PAROLA", PAROLA_ADMIN)
    monkeypatch.delenv("ORAR_ADMIN_PAROLA_HASH", raising=False)
    engine = _engine_memorie()

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as sesiune:
        incarca_pagini(sesiune, pagini_golden)
        consolideaza(sesiune, an_universitar="2025-2026")
        sesiune.add(User(nume="Ana Pop", email=EMAIL, parola_hash=hash_parola(PAROLA)))
        sesiune.commit()
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


def _csrf(client, cale: str = "/login") -> str:  # noqa: ANN001
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


def _intra(client, cine: str = ADMIN, parola: str = PAROLA_ADMIN) -> None:  # noqa: ANN001
    client.post("/login", data={"csrf": _csrf(client), "email": cine, "parola": parola})


def _o_ora(s, slug: str = "244") -> Ora:  # noqa: ANN001
    """O activitate proprie a grupei, cu profesor si sala."""
    grupa = gaseste_grupa(s, slug)
    return s.scalar(
        select(Ora)
        .where(Ora.grupa_id == grupa.id, Ora.profesor_id.is_not(None), Ora.semigrupa.is_(None))
        .order_by(Ora.id)
        .limit(1)
    )


def _alta_sala(s, o: Ora) -> Sala:  # noqa: ANN001
    return s.scalar(select(Sala).where(Sala.id != o.sala_id, Sala.tip == "fizica").limit(1))


def _numara(s, model, *conditii) -> int:  # noqa: ANN001, ANN002
    return s.scalar(select(func.count()).select_from(model).where(*conditii))


def _reingest(s, pagini) -> corectii.RezultatAplicare:  # noqa: ANN001
    """Ce face un orar nou publicat: sterge orele semestrului, le reincarca, pune corectiile."""
    _sterge_semestrul(s, an_universitar="2025-2026", semestru=2)
    incarca_pagini(s, pagini)
    consolideaza(s, an_universitar="2025-2026")
    return corectii.aplica_dupa_ingest(s, an_universitar="2025-2026", semestru=2)


# ---------------------------------------------------------------------------
# Corectiile, la nivel de date
# ---------------------------------------------------------------------------


def _valori(o: Ora, **schimbari) -> corectii.Valori:  # noqa: ANN003
    """Activitatea asa cum e, cu cateva campuri schimbate -- ce trimite formularul."""
    baza = {
        "grupa": corectii.grupa_afisata(o.grupa),
        "zi": o.zi_saptamana,
        "ora_inceput": o.ora_inceput.hour,
        "ora_sfarsit": o.ora_sfarsit.hour,
        "materie": o.materie.nume,
        "tip": o.tip_ora_materie,
        "profesor": o.profesor.nume if o.profesor else None,
        "sala": o.sala.nume if o.sala else None,
        "semigrupa": o.semigrupa,
        "frecventa": o.frecventa,
        "saptamani": o.saptamani,
    }
    return corectii.Valori(**{**baza, **schimbari})


def _noua(s, **campuri) -> Ora:  # noqa: ANN001, ANN003
    date = {
        "grupa": gaseste_grupa(s, "244"), "zi": "Vineri", "ora_inceput": 18, "ora_sfarsit": 20,
        "materie": "Consultatii", "tip": "seminar", "profesor": "Popescu Nou", "sala": "L-410",
        **campuri,
    }  # fmt: skip
    return corectii.adauga(s, corectii.Valori(**date), semestru=2, de_catre="sefu")


def test_modificarea_schimba_si_tine_minte_ce_era(s):
    o = _o_ora(s)
    profesor_vechi, sala_veche = o.profesor.nume, o.sala.nume
    sala_noua = _alta_sala(s, o).nume

    c = corectii.modifica(s, o, _valori(o, profesor="Popescu Nou", sala=sala_noua), de_catre="sefu")
    assert (o.profesor.nume, o.sala.nume) == ("Popescu Nou", sala_noua)
    publicat = corectii.original(c)
    assert (c.fel, publicat["profesor"], publicat["sala"]) == (
        "modificare", profesor_vechi, sala_veche,
    )  # fmt: skip
    assert o.corectie_id == c.id

    # a doua schimbare pe aceeasi activitate: aceeasi corectie, cu originalul pastrat
    c2 = corectii.modifica(s, o, _valori(o, profesor="Ionescu Altul"), de_catre="sefu")
    assert c2.id == c.id and corectii.original(c2)["profesor"] == profesor_vechi
    assert _numara(s, Corectie) == 1


def test_se_poate_schimba_orice(s):
    """Materia, tipul, ziua, orele, formatiunea, semigrupa, saptamanile -- tot."""
    o = _o_ora(s)
    alta_grupa = gaseste_grupa(s, "243")
    c = corectii.modifica(
        s,
        o,
        _valori(
            o,
            grupa=alta_grupa,
            zi="Vineri",
            ora_inceput=18,
            ora_sfarsit=20,
            materie="Materie Noua",
            tip="Lab",
            frecventa="SI",
            saptamani="sapt 1-7",
        ),  # fmt: skip
        de_catre="sefu",
    )
    assert (o.grupa.slug, o.zi_saptamana, o.ora_inceput.hour, o.ora_sfarsit.hour) == (
        "243", "Vineri", 18, 20,
    )  # fmt: skip
    assert (o.materie.nume, o.tip_ora_materie, o.frecventa, o.saptamani) == (
        "Materie Noua", "Lab", "SI", "sapt 1-7",
    )  # fmt: skip
    assert corectii.original(c)["grupa_slug"] == "244"
    # apare la grupa noua, nu mai apare la cea veche
    assert o in ore_pentru_grupa(s, alta_grupa.id)
    assert o not in ore_pentru_grupa(s, gaseste_grupa(s, "244").id)


def test_readusa_la_original_corectia_dispare(s):
    o = _o_ora(s)
    publicat = _valori(o)
    corectii.modifica(s, o, _valori(o, profesor="Altcineva", zi="Vineri"), de_catre="sefu")
    assert corectii.modifica(s, o, publicat, de_catre="sefu") is None
    assert o.corectie_id is None and _numara(s, Corectie) == 0


def test_fara_nicio_schimbare_nu_apare_nicio_corectie(s):
    """Inclusiv pentru o activitate de semigrupa, tinuta pe nodul `244/1`: formularul o
    arata la grupa 244, iar salvata neschimbata nu trebuie sa para mutata."""
    assert corectii.modifica(s, _o_ora(s), _valori(_o_ora(s)), de_catre="x") is None
    semigrupa = s.scalar(
        select(Ora).join(Ora.grupa).where(Ora.semigrupa == "Gr_1", Ora.profesor_id.is_not(None))
    )
    assert semigrupa.grupa.tip == "semigrupa"
    assert corectii.modifica(s, semigrupa, _valori(semigrupa), de_catre="x") is None
    assert _numara(s, Corectie) == 0


def test_ce_confirma_adminul_iese_din_coada_de_verificare(s):
    o = _o_ora(s)
    o.campuri_nesigure = "profesor,materie,sala"
    corectii.modifica(s, o, _valori(o, profesor="Popescu Nou"), de_catre="sefu")
    assert o.campuri_nesigure == "sala"


def test_adaugarea_apare_in_orarul_grupei(s):
    grupa = gaseste_grupa(s, "244")
    inainte = len(ore_pentru_grupa(s, grupa.id))
    o = _noua(s)
    assert len(ore_pentru_grupa(s, grupa.id)) == inainte + 1
    assert o.sala.nume == "L.410"  # sala trece prin aceeasi normalizare ca la ingest
    assert s.get(Corectie, o.corectie_id).fel == "adaugare"

    # o activitate adaugata se editeaza la fel; ramane "adaugata", fara original
    c = corectii.modifica(s, o, _valori(o, zi="Joi", materie="Tutoriat"), de_catre="sefu")
    assert (c.fel, c.zi, c.materie, corectii.original(c)) == ("adaugare", "Joi", "Tutoriat", None)


def test_anularea_readuce_orarul_publicat(s):
    o = _o_ora(s)
    publicat = (o.grupa.slug, o.zi_saptamana, o.materie.nume, o.profesor.nume, o.sala.nume)
    c = corectii.modifica(
        s,
        o,
        _valori(
            o,
            grupa=gaseste_grupa(s, "243"),
            zi="Vineri",
            materie="Alta",
            profesor="Altcineva",
            sala=_alta_sala(s, o).nume,
        ),  # fmt: skip
        de_catre="x",
    )
    corectii.anuleaza(s, c)
    assert (o.grupa.slug, o.zi_saptamana, o.materie.nume, o.profesor.nume, o.sala.nume) == publicat
    assert o.corectie_id is None

    n = _numara(s, Ora)
    adaugata = _noua(s)
    corectii.anuleaza(s, s.get(Corectie, adaugata.corectie_id))
    assert _numara(s, Ora) == n and _numara(s, Corectie) == 0


def test_mutata_la_alta_formatiune_nu_mai_e_partajata_si_anularea_reface_legaturile(s):
    """Un optional legat de mai multe serii, mutat la o grupa: e doar al ei. La anulare isi
    recapata seriile."""
    from orar.db.models import OraGrupa

    o = s.scalar(select(Ora).join(OraGrupa, OraGrupa.ora_id == Ora.id).limit(1))
    legaturi = _numara(s, OraGrupa, OraGrupa.ora_id == o.id)
    assert legaturi > 0

    c = corectii.modifica(s, o, _valori(o, grupa=gaseste_grupa(s, "244")), de_catre="x")
    assert _numara(s, OraGrupa, OraGrupa.ora_id == o.id) == 0
    corectii.anuleaza(s, c)
    assert _numara(s, OraGrupa, OraGrupa.ora_id == o.id) == legaturi


def test_corectiile_supravietuiesc_unui_orar_nou(s, pagini_golden):
    """Miezul: ingestul sterge si reincarca orele, iar schimbarile adminului revin singure --
    si cand s-a schimbat totul la activitate, nu doar sala."""
    o = _o_ora(s)
    sala_noua = _alta_sala(s, o).nume
    corectii.modifica(
        s,
        o,
        _valori(
            o,
            zi="Vineri",
            ora_inceput=18,
            ora_sfarsit=20,
            materie="Materie Noua",
            profesor="Popescu Nou",
            sala=sala_noua,
        ),  # fmt: skip
        de_catre="sefu",
    )
    _noua(s)
    total = _numara(s, Ora)

    rezultat = _reingest(s, pagini_golden)

    assert (rezultat.aplicate, rezultat.neaplicate) == (2, [])
    assert _numara(s, Ora) == total
    dupa = {x.materie.nume: x for x in ore_pentru_grupa(s, gaseste_grupa(s, "244").id) if x.materie}
    m = dupa["Materie Noua"]
    assert (m.zi_saptamana, m.ora_inceput.hour, m.profesor.nume, m.sala.nume) == (
        "Vineri", 18, "Popescu Nou", sala_noua,
    )  # fmt: skip
    assert m.corectie_id is not None and "Consultatii" in dupa


def test_corectia_fara_activitate_in_orarul_nou_ramane_neaplicata(s, pagini_golden):
    o = _o_ora(s)
    zi, ore, materie = o.zi_saptamana, f"{o.ora_inceput.hour}-{o.ora_sfarsit.hour}", o.materie.nume
    c = corectii.modifica(s, o, _valori(o, profesor="Popescu Nou"), de_catre="sefu")

    # orarul nou nu mai are activitatea
    fara = [
        {**p, zi: [a for a in p.get(zi, []) if not (a["ore"] == ore and a["materie"] == materie)]}
        if p["grupa"].endswith("Grupa 244")
        else p
        for p in pagini_golden
    ]
    rezultat = _reingest(s, fara)
    s.refresh(c)
    assert c.aplicata is False
    assert len(rezultat.neaplicate) == 1 and materie in rezultat.neaplicate[0]


# ---------------------------------------------------------------------------
# Sesizarile
# ---------------------------------------------------------------------------


def _trimite(client, *, pagina: str = "/grupa/244", **campuri):  # noqa: ANN001, ANN003, ANN202
    date = {"csrf": _csrf(client, pagina), "pagina": pagina, "mesaj": "Sala e greșită.", **campuri}
    return client.post("/sesizare", data=date, follow_redirects=False)


def test_butonul_apare_pentru_oricine_pe_grupe_si_pe_sali(client):
    for cale in ("/grupa/244", "/sala/Amf.501"):
        pagina = client.get(cale).text
        assert "Raportează o problemă" in pagina and 'action="/sesizare"' in pagina


def test_vizitatorul_trimite_o_sesizare(client, s):
    o = _o_ora(s)
    r = _trimite(client, ora_id=str(o.id), contact="ion@example.com")
    assert r.status_code == 303 and r.headers["location"] == "/grupa/244?raportat=1"
    assert "Mulțumim!" in client.get(r.headers["location"]).text

    (z,) = s.scalars(select(Sesizare)).all()
    assert (z.stare, z.pagina, z.ora_id, z.user_id) == ("noua", "/grupa/244", o.id, None)
    assert z.contact == "ion@example.com"
    # descrierea e in cuvinte, ca sa ramana de inteles si dupa ce randul dispare
    assert o.materie.nume in z.activitate and o.sala.nume in z.activitate


def test_utilizatorul_autentificat_e_retinut(client, s):
    _intra(client, EMAIL, PAROLA)
    _trimite(client)
    z = s.scalar(select(Sesizare))
    assert z.user.email == EMAIL and z.ora_id is None


@pytest.mark.parametrize(
    ("campuri", "mesaj"),
    [
        ({"mesaj": "abc"}, "Scrie"),
        ({"mesaj": "x" * 1001}, "prea lung"),
        ({"csrf": "gresit"}, "expirat"),
    ],
)
def test_sesizarile_nevalide_nu_se_salveaza(client, s, campuri, mesaj):
    r = _trimite(client, **campuri)
    assert "raport_eroare=" in r.headers["location"]
    assert mesaj in client.get(r.headers["location"]).text
    assert _numara(s, Sesizare) == 0


def test_robotii_si_paginile_straine_sunt_respinse(client, s):
    # campul-capcana completat: pare ca a mers, dar nu se salveaza nimic
    assert "raportat=1" in _trimite(client, website="http://spam.example").headers["location"]
    token = _csrf(client, "/grupa/244")
    for rau in ("https://evil.example/", "/admin", "//evil.example/grupa/1"):
        r = client.post(
            "/sesizare", data={"csrf": token, "pagina": rau, "mesaj": "un mesaj destul de lung"}
        )
        assert r.status_code == 400
    assert _numara(s, Sesizare) == 0


def test_cel_mult_cateva_sesizari_la_rand(client, s):
    for _ in range(ruta_sesizari.LIMITA):
        assert "raportat=1" in _trimite(client).headers["location"]
    r = _trimite(client)
    assert "raport_eroare=" in r.headers["location"]
    assert _numara(s, Sesizare) == ruta_sesizari.LIMITA


def test_sesizarea_ramane_dupa_ce_orarul_se_reincarca(client, s, pagini_golden):
    o = _o_ora(s)
    _trimite(client, ora_id=str(o.id))
    _reingest(s, pagini_golden)
    s.expire_all()
    z = s.scalar(select(Sesizare))
    assert z.ora_id is None and z.activitate  # legatura a disparut, descrierea nu


def test_coada_e_doar_pentru_admini(client, s):
    assert client.get("/admin/sesizari", follow_redirects=False).status_code == 303
    _intra(client, EMAIL, PAROLA)
    assert client.get("/admin/sesizari").status_code == 403


def test_adminul_trateaza_sesizarile(client, s):
    o = _o_ora(s)
    _trimite(client, ora_id=str(o.id))
    _intra(client)

    assert "<strong>1</strong>" in client.get("/admin").text  # cardul din panou
    coada = client.get("/admin/sesizari").text
    assert "Sala e greșită." in coada and o.materie.nume in coada
    assert f"/admin/activitate/{o.id}?sesizare=" in coada

    z = s.scalar(select(Sesizare))
    client.post(
        f"/admin/sesizari/{z.id}",
        data={"csrf": _csrf(client, "/admin/sesizari"), "stare": "rezolvata", "nota": "mutat"},
    )
    s.refresh(z)
    assert (z.stare, z.tratata_de, z.nota) == ("rezolvata", ADMIN, "mutat")
    assert "Nicio sesizare de tratat." in client.get("/admin/sesizari").text
    assert "Sala e greșită." in client.get("/admin/sesizari?arata=toate").text

    client.post(
        f"/admin/sesizari/{z.id}",
        data={"csrf": _csrf(client, "/admin/sesizari"), "stare": "noua"},
    )
    s.refresh(z)
    assert z.stare == "noua" and z.tratata_de is None


# ---------------------------------------------------------------------------
# Editarea din pagini
# ---------------------------------------------------------------------------


def test_modul_de_editare_e_doar_al_adminului(client, s):
    o = _o_ora(s)
    # vizitator: nici buton, nici linkuri de editare, chiar daca le cere
    pagina = client.get("/grupa/244?editare=1").text
    assert "Editează orarul" not in pagina and f"/admin/activitate/{o.id}" not in pagina

    _intra(client)
    normal = client.get("/grupa/244").text
    assert "Editează orarul" in normal and f"/admin/activitate/{o.id}" not in normal
    editare = client.get("/grupa/244?editare=1").text
    assert f'href="/admin/activitate/{o.id}?inapoi=' in editare
    assert "Adaugă activitate" in editare
    assert "Editează orarul" in client.get("/sala/Amf.501").text


def _formular(o: Ora, **schimbari) -> dict[str, str]:  # noqa: ANN003
    """Ce trimite formularul de editare: toata activitatea, cu cateva campuri schimbate."""
    date = {
        "grupa": corectii.grupa_afisata(o.grupa).slug,
        "zi": o.zi_saptamana,
        "ora_inceput": str(o.ora_inceput.hour),
        "ora_sfarsit": str(o.ora_sfarsit.hour),
        "materie": o.materie.nume,
        "tip": o.tip_ora_materie or "",
        "profesor": o.profesor.nume if o.profesor else "",
        "sala": o.sala.nume if o.sala else "",
        "semigrupa": o.semigrupa or "",
        "frecventa": o.frecventa or "",
        "saptamani": o.saptamani or "",
    }
    return {**date, **schimbari}


def _salveaza(client, o: Ora, **schimbari):  # noqa: ANN001, ANN003, ANN202
    return client.post(
        f"/admin/activitate/{o.id}",
        data={"csrf": _csrf(client, f"/admin/activitate/{o.id}"), **_formular(o, **schimbari)},
        follow_redirects=False,
    )


def test_formularul_de_editare_are_tot_ce_are_cel_de_adaugare(client, s):
    o = _o_ora(s)
    _intra(client)
    nou = client.get("/admin/activitate/noua").text
    editare = client.get(f"/admin/activitate/{o.id}?inapoi=/grupa/244").text
    campuri = ("grupa", "zi", "ora_inceput", "ora_sfarsit", "materie", "tip", "profesor", "sala",
               "semigrupa", "frecventa", "saptamani")  # fmt: skip
    for camp in campuri:
        assert f'name="{camp}"' in nou and f'name="{camp}"' in editare, camp
    # si e precompletat cu activitatea
    assert f'value="{o.materie.nume}"' in editare and f'value="{o.sala.nume}"' in editare
    assert '<option value="244" selected>' in editare
    assert f'<option value="{o.zi_saptamana}" selected>' in editare


def test_adminul_schimba_tot_din_formular(client, s):
    o = _o_ora(s)
    sala_noua = _alta_sala(s, o).nume
    _intra(client)

    r = _salveaza(
        client, o, grupa="243", zi="Vineri", ora_inceput="18", ora_sfarsit="20",
        materie="Materie Noua", tip="Lab", profesor="Popescu Nou", sala=sala_noua,
        inapoi="/grupa/243?editare=1",
    )  # fmt: skip
    assert r.status_code == 303 and r.headers["location"] == "/grupa/243?editare=1"
    s.refresh(o)
    assert (o.grupa.slug, o.zi_saptamana, o.ora_inceput.hour, o.materie.nume, o.tip_ora_materie) == (
        "243", "Vineri", 18, "Materie Noua", "Lab",
    )  # fmt: skip
    assert (o.profesor.nume, o.sala.nume) == ("Popescu Nou", sala_noua)
    assert '<span class="bloc-materie">Materie Noua</span>' in client.get("/grupa/243").text
    assert '<span class="bloc-materie">Materie Noua</span>' not in client.get("/grupa/244").text
    assert "Modificată manual" in client.get(f"/admin/activitate/{o.id}").text


def test_editarea_cu_date_gresite_explica_si_nu_schimba_nimic(client, s):
    o = _o_ora(s)
    zi = o.zi_saptamana
    _intra(client)
    r = _salveaza(client, o, zi="Vineri", ora_inceput="12", ora_sfarsit="10")
    assert r.status_code == 400 and "după cea de început" in r.text
    assert '<option value="Vineri" selected>' in r.text  # ce ai ales ramane in formular
    s.refresh(o)
    assert o.zi_saptamana == zi and _numara(s, Corectie) == 0


def test_corectarea_dintr_o_sesizare_o_si_rezolva(client, s):
    o = _o_ora(s)
    _trimite(client, ora_id=str(o.id))
    z = s.scalar(select(Sesizare))
    _intra(client)

    formular = client.get(f"/admin/activitate/{o.id}?sesizare={z.id}").text
    assert "Sala e greșită." in formular and "marchează sesizarea ca rezolvată" in formular
    _salveaza(client, o, sala=_alta_sala(s, o).nume, sesizare_id=str(z.id), rezolva="1")
    s.refresh(z)
    assert (z.stare, z.tratata_de) == ("rezolvata", ADMIN)


def _adauga(client, **campuri):  # noqa: ANN001, ANN003, ANN202
    date = {
        "csrf": _csrf(client, "/admin/activitate/noua"),
        "grupa": "244",
        "zi": "Vineri",
        "ora_inceput": "18",
        "ora_sfarsit": "20",
        "materie": "Consultatii",
        "tip": "seminar",
        "profesor": "Popescu Nou",
        "sala": "L.410",
        "inapoi": "/grupa/244",
        **campuri,
    }
    return client.post("/admin/activitate/noua", data=date, follow_redirects=False)


def test_adminul_adauga_o_activitate(client, s):
    _intra(client)
    formular = client.get("/admin/activitate/noua?grupa=244").text
    assert re.search(r'<option value="244" selected>', formular)

    r = _adauga(client)
    assert r.status_code == 303 and r.headers["location"] == "/grupa/244"
    pagina = client.get("/grupa/244").text
    assert '<span class="bloc-materie">Consultatii</span>' in pagina
    assert _numara(s, Corectie, Corectie.fel == "adaugare") == 1


@pytest.mark.parametrize(
    ("campuri", "mesaj"),
    [
        ({"ora_inceput": "12", "ora_sfarsit": "10"}, "după cea de început"),
        ({"materie": " "}, "Scrie materia"),
        ({"grupa": "nu-exista"}, "Alege formațiunea"),
        ({"zi": "Duminica"}, "Alege ziua"),
        ({"tip": "petrecere"}, "Tip de activitate"),
    ],
)
def test_adaugarea_cu_date_gresite_explica_ce_lipseste(client, s, campuri, mesaj):
    _intra(client)
    r = _adauga(client, **campuri)
    assert r.status_code == 400 and mesaj in r.text
    assert "Consultatii" in r.text or "materie" in campuri  # ce ai completat ramane in formular
    assert _numara(s, Corectie) == 0


def test_lista_de_modificari_si_anularea_din_ea(client, s):
    o = _o_ora(s)
    sala, zi = o.sala.nume, o.zi_saptamana
    _intra(client)
    _salveaza(client, o, sala=_alta_sala(s, o).nume, zi="Vineri")
    lista = client.get("/admin/corectii").text
    assert "modificată" in lista
    assert f"sală: <s>{sala}</s>" in lista and f"zi: <s>{zi}</s>" in lista
    assert "profesor: <s>" not in lista  # doar ce s-a schimbat

    c = s.scalar(select(Corectie))
    client.post(f"/admin/corectii/{c.id}/anuleaza", data={"csrf": _csrf(client, "/admin/corectii")})
    s.refresh(o)
    assert (o.sala.nume, o.zi_saptamana) == (sala, zi) and _numara(s, Corectie) == 0
    assert "Nicio modificare manuală" in client.get("/admin/corectii").text


def test_fara_rol_de_admin_nu_se_editeaza_nimic(client, s):
    o = _o_ora(s)
    sala = o.sala.nume
    _intra(client, EMAIL, PAROLA)
    token = _csrf(client, "/")
    assert client.get(f"/admin/activitate/{o.id}").status_code == 403
    assert client.get("/admin/corectii").status_code == 403
    r = client.post(f"/admin/activitate/{o.id}", data={"csrf": token, **_formular(o, sala="L.410")})
    assert r.status_code == 403
    assert client.post("/admin/activitate/noua", data={"csrf": token}).status_code == 403
    s.refresh(o)
    assert o.sala.nume == sala and _numara(s, Corectie) == 0


# ---------------------------------------------------------------------------
# Sala se alege din dropdown
# ---------------------------------------------------------------------------


def test_sala_e_un_dropdown_cu_salile_cunoscute(client, s):
    o = _o_ora(s)
    sala = s.scalar(select(Sala).where(Sala.tip == "fizica", Sala.id != o.sala_id))
    sala.nr_locuri = 42
    s.commit()
    _intra(client)

    formular = client.get(f"/admin/activitate/{o.id}").text
    assert '<select name="sala"' in formular
    assert 'optgroup label="Sălile facultății"' in formular
    assert f'<option value="{o.sala.nume}" selected>' in formular  # sala activitatii e aleasa
    assert f'<option value="{sala.nume}" >{sala.nume} · 42 locuri</option>' in formular
    assert "altă sală…" in formular
    # formularul de adaugare are acelasi dropdown
    assert '<select name="sala"' in client.get("/admin/activitate/noua").text


def test_alta_sala_se_scrie_de_mana_si_apare_apoi_in_lista(client, s):
    o = _o_ora(s)
    _intra(client)
    _salveaza(client, o, sala="__alta__", sala_noua="IMAR 500")
    s.refresh(o)
    assert o.sala.nume == "IMAR 500"
    assert '<option value="IMAR 500" selected>' in client.get(f"/admin/activitate/{o.id}").text


def test_sala_aleasa_din_lista(client, s):
    o = _o_ora(s)
    noua = _alta_sala(s, o).nume
    _intra(client)
    _salveaza(client, o, sala=noua, sala_noua="")
    s.refresh(o)
    assert o.sala.nume == noua


# ------------------------------------------------------ coada de verificare


def _nesigura(s) -> Ora:  # noqa: ANN001
    """O activitate din coada: cu decupaj si cu profesorul nerecunoscut."""
    o = ore_pentru_grupa(s, gaseste_grupa(s, "244").id, include_optionale=False)[0]
    o.campuri_nesigure, o.confidence = "profesor", 0.4
    o.sursa_bbox, o.sursa_pagina = "1,2,30,40", "pag_001.png"
    s.commit()
    return o


def _rezolva(client, o: Ora, **campuri):  # noqa: ANN001, ANN003, ANN202
    date = {
        "csrf": _csrf(client, "/admin/review"),
        "materie": o.materie.nume,
        "profesor": o.profesor.nume if o.profesor else "",
        "sala": o.sala.nume if o.sala else "",
        **campuri,
    }
    return client.post(f"/admin/review/{o.id}", data=date, follow_redirects=False)


def test_coada_are_formular_de_rezolvare(client, s):
    o = _nesigura(s)
    _intra(client)
    pagina = client.get("/admin/review").text
    assert f'action="/admin/review/{o.id}"' in pagina
    assert "✓ Confirmă" in pagina and f"/admin/activitate/{o.id}?inapoi=" in pagina


def test_confirmarea_scoate_activitatea_din_coada_si_tine_dupa_reincarcare(
    client, s, pagini_golden
):
    o = _nesigura(s)
    cheie = (o.grupa.slug, o.zi_saptamana, o.ora_inceput, o.materie.nume)
    _intra(client)

    assert _rezolva(client, o).status_code == 303
    s.expire_all()
    assert o.campuri_nesigure is None and o.confidence == 1.0
    c = s.get(Corectie, o.corectie_id)
    assert corectii.e_confirmare(c)
    assert f'action="/admin/review/{o.id}"' not in client.get("/admin/review").text

    # orarul nou aduce aceeasi citire; confirmarea se pune la loc
    assert _reingest(s, pagini_golden).aplicate == 1
    noua = s.scalar(select(Ora).where(Ora.corectie_id == c.id))
    assert (noua.grupa.slug, noua.zi_saptamana, noua.ora_inceput, noua.materie.nume) == cheie
    assert noua.confidence == 1.0 and noua.campuri_nesigure is None


def test_din_coada_se_poate_si_corecta(client, s):
    o = _nesigura(s)
    _intra(client)
    assert _rezolva(client, o, profesor="Popescu Corectat").status_code == 303
    s.expire_all()
    assert o.profesor.nume == "Popescu Corectat"
    assert o.campuri_nesigure is None
    assert not corectii.e_confirmare(s.get(Corectie, o.corectie_id))


def test_coada_se_rezolva_doar_de_admin_si_cu_csrf(client, s):
    o = _nesigura(s)
    r = client.post(f"/admin/review/{o.id}", data={"materie": "X"}, follow_redirects=False)
    assert r.status_code in (303, 401, 403)
    _intra(client)
    client.post(f"/admin/review/{o.id}", data={"csrf": "gresit", "materie": "X"})
    s.expire_all()
    assert o.campuri_nesigure == "profesor" and o.materie.nume != "X"


# ------------------------------------------------------ orarul profesorilor


def _index(o: Ora, *oameni: tuple[str, str]):  # noqa: ANN202
    """Un orar al profesorilor in care activitatea `o` e tinuta de `oameni` (nume, saptamani)."""
    from orar.ingest.crosscheck import IndexProfesori, _cheie_ora

    index = IndexProfesori(nume=[n for n, _ in oameni], pagini=len(oameni))
    k = _cheie_ora(o)
    for nume, saptamani in oameni:
        index.pe_slot[k].add(nume)
        index.detalii[k].add((nume, "", saptamani))
    return index


def test_activitatea_impartita_pe_saptamani_isi_ia_profesorul_ei(s):
    """Acelasi slot, doi oameni: unul in saptamanile 1-7, altul in 8-14."""
    o = _nesigura(s)
    index = _index(o, ("Ionescu Ana", "sapt1-7"), ("Popa Dan", "sapt8-14"))
    o.saptamani = "sapt 8-14"
    assert index.profesori(o) == {"Popa Dan"}
    # fara saptamani pe activitate nu avem cum alege: raman amandoi
    o.saptamani = None
    assert index.profesori(o) == {"Ionescu Ana", "Popa Dan"}
    # tinuta impreuna (aceleasi saptamani): amandoi
    impreuna = _index(o, ("Ionescu Ana", ""), ("Popa Dan", ""))
    assert impreuna.profesori(o) == {"Ionescu Ana", "Popa Dan"}


def test_orarul_profesorilor_pastrat_da_lista_si_propunerile(client, s):
    from orar.db.models import Profesor, SlotProfesor
    from orar.ingest.crosscheck import profesori_potriviti, salveaza_index

    o = _nesigura(s)
    index = _index(o, ("Ionescu Ana Maria", ""), ("Popa Dan", ""))
    assert salveaza_index(s, index, an_universitar="2025-2026", semestru=2) == 2
    s.commit()
    assert _numara(s, SlotProfesor) == 2
    assert _numara(s, Profesor, Profesor.din_orar) == 2
    # salvat a doua oara, inlocuieste, nu dubleaza
    salveaza_index(s, index, an_universitar="2025-2026", semestru=2)
    assert _numara(s, SlotProfesor) == 2

    assert profesori_potriviti(s, [o]) == {o.id: ["Ionescu Ana Maria", "Popa Dan"]}

    _intra(client)
    pagina = client.get("/admin/review").text
    assert 'data-foloseste="Ionescu Ana Maria|Popa Dan"' in pagina
    # dropdownul are doar oamenii din orarul profesorilor
    optiuni = pagina[pagina.index('id="optiuni-profesori"') :]
    optiuni = optiuni[: optiuni.index("</template>")]
    assert optiuni.count("<option") == 3  # cei doi + "alt nume"


def test_profesorii_din_orar_nu_se_sterg_ca_orfani(s):
    from orar.db.models import Profesor
    from orar.worker.sync import curata_orfanii

    s.add_all(
        [
            Profesor(nume="Din Orar Fara Ore", slug="din-orar-fara-ore", din_orar=True),
            Profesor(nume="Citit Gresit", slug="citit-gresit"),
        ]
    )
    s.flush()
    curata_orfanii(s)
    ramasi = set(s.scalars(select(Profesor.nume).where(Profesor.nume.like("%r%"))))
    assert "Din Orar Fara Ore" in ramasi and "Citit Gresit" not in ramasi


def test_mai_multi_profesori_se_aleg_separat(client, s):
    o = _nesigura(s)
    _intra(client)
    date = {
        "csrf": _csrf(client, "/admin/review"),
        "materie": o.materie.nume,
        "sala": o.sala.nume if o.sala else "",
        # doua dropdownuri, unul lasat pe "alt nume" cu numele scris alaturi, unul gol
        "profesor": ["Ionescu Ana", "__alt__", "Popa Dan", "", "Ionescu Ana"],
        "saptamani": "sapt 1-7",
    }
    assert (
        client.post(f"/admin/review/{o.id}", data=date, follow_redirects=False).status_code == 303
    )
    s.expire_all()
    assert o.profesor.nume == "Ionescu Ana / Popa Dan"
    assert o.saptamani == "sapt 1-7"

    # formularul complet arata cate un dropdown pentru fiecare
    formular = client.get(f"/admin/activitate/{o.id}").text
    assert formular.count('<div class="profesor-rand">') == 2


def test_alta_parte_porneste_de_la_activitatea_existenta(client, s):
    o = _nesigura(s)
    _intra(client)
    formular = client.get(f"/admin/activitate/noua?dupa={o.id}").text
    assert f'value="{o.materie.nume}"' in formular
    assert f'name="perioada" value="{o.perioada_id}"' in formular
    assert formular.count('<div class="profesor-rand">') == 1  # profesorul se alege din nou
