"""Opționale, facultative, limbi: cui se aplică fiecare pagină și ce se afișează."""

from __future__ import annotations

import copy
import re

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from orar.db.models import Base, Grupa
from orar.db.queries import gaseste_grupa, ore_pentru_grupa
from orar.db.versiuni import _semnatura
from orar.domain.hierarchy import (
    CategoriePachet,
    TipPagina,
    an_din_text,
    categorie_pachet,
    parse_titlu,
    specializari_vizate,
)
from orar.ingest.consolidate import consolideaza
from orar.ingest.load import incarca_pagini
from tests.conftest import _engine_memorie

TOATE = {"MATE", "MATE-INFO", "MATE-APL", "INFO", "CTI"}


# ---------------------------------------------------------------------------
# Decodarea permisiva
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("titlu", "disponibile", "asteptat"),
    [
        (
            "Facultative an II (Mate, Mate-Info, Mate Apl., Info, CTI)",
            TOATE,
            ["MATE", "MATE-INFO", "MATE-APL", "INFO", "CTI"],
        ),
        # OCR: `l` in loc de `I`
        ("Limbi straine - an Il (Mate, Mate-lnfo, CTl)", TOATE, ["MATE", "MATE-INFO", "CTI"]),
        # fara virgula, intr-un an fara MATE-INFO: sunt doua specializari
        ("Limbi straine - an I (Mate Info, CTI)", {"MATE", "INFO", "CTI"}, ["MATE", "INFO", "CTI"]),
        # ... iar intr-un an cu MATE-INFO, e una singura
        ("Limbi straine - an II (Mate Info, CTI)", TOATE, ["MATE-INFO", "CTI"]),
        # paranteza spune ce fel de optionale sunt, nu inca o specializare
        ("Optionale an III - MATE-INFO (Informatica)", TOATE, ["MATE-INFO"]),
        ("Optionale an II - MATE APLICATE (1)", TOATE, ["MATE-APL"]),
        ("Optionale an I Master INFO", {"INFO", "MATE"}, ["INFO"]),
        # specializare noua, cunoscuta doar din ierarhie
        ("Facultative an I (Info, Bio Info)", {"INFO", "BIO-INFO"}, ["INFO", "BIO-INFO"]),
        # nicio specializare numita -> lista goala; loader-ul cade pe tot anul
        ("Facultative an I", TOATE, []),
        ("Optionale an II - MATE-INFO", {"INFO", "CTI"}, []),
    ],
)
def test_specializari_vizate(titlu, disponibile, asteptat):
    assert specializari_vizate(titlu, disponibile) == asteptat


@pytest.mark.parametrize(
    ("text", "an"),
    [
        ("Optionale an Ill - MATE (1)", 3),
        ("Facultative an IV", 4),
        ("Pachet opțional, anul 2", 2),
        ("An. II - Limbi", 2),
        ("Conferinte si Seminarii", None),
    ],
)
def test_anul_se_gaseste_oriunde(text, an):
    assert an_din_text(text) == an


@pytest.mark.parametrize(
    ("titlu", "tip", "an"),
    [
        ("Pachet opțional anul 2 - INFO", TipPagina.OPTIONAL, 2),
        ("Cursuri facultative, an III", TipPagina.FACULTATIV, 3),
        ("Limba engleza - anul I", TipPagina.LIMBI, 1),
    ],
)
def test_titlurile_fara_sablon_se_recunosc_dupa_cuvinte(titlu, tip, an):
    t = parse_titlu(titlu)
    assert (t.tip, t.an) == (tip, an)


def test_specializare_noua_in_titlul_grupei():
    t = parse_titlu("BIO INFO Grupa 171")
    assert t.specializare == "BIO-INFO"
    assert t.note  # semnalata, ca sa se vada in avertismentele ingestului


@pytest.mark.parametrize(
    ("nume", "categorie"),
    [
        ("Facultative an II (Mate, Info)", CategoriePachet.FACULTATIV),
        ("Optionale an III - INFO (Curs)", CategoriePachet.OPTIONAL),
        ("Pachet opțional", CategoriePachet.OPTIONAL),
        ("Limbi straine - an I", CategoriePachet.LIMBI),
        ("Conferinte si Seminarii", CategoriePachet.ALTELE),
        ("Fizică/Robotică 101ROB", CategoriePachet.ALTELE),
    ],
)
def test_categoria_pachetului(nume, categorie):
    assert categorie_pachet(nume) is categorie


# ---------------------------------------------------------------------------
# Legarea, pe datele reale
# ---------------------------------------------------------------------------


def _baza(pagini, *, consolidata: bool = True):  # noqa: ANN001, ANN202
    engine = _engine_memorie()
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    incarca_pagini(s, pagini)
    if consolidata:
        consolideaza(s, an_universitar="2025-2026")
    s.commit()
    return s


def _grupe_licenta(s):  # noqa: ANN001, ANN202
    return [
        g
        for g in s.scalars(select(Grupa).where(Grupa.tip == "grupa"))
        if g.parinte and g.parinte.tip == "serie"
    ]


def _categorii_vazute(s, g) -> set[CategoriePachet]:  # noqa: ANN001
    return {
        categorie_pachet(o.grupa.nume)
        for o in ore_pentru_grupa(s, g.id)
        if o.grupa.tip == "optional"
    }


def test_fiecare_grupa_de_licenta_isi_vede_facultativele_si_limbile(db):
    """Uniform pe toti anii: daca exista pagina de facultative / limbi pentru anul tau, o
    vezi, indiferent de specializare. Inainte, INFO si MATE anul 1 nu-si vedeau limbile
    (`Mate Info` fara virgula), iar MATE-INFO anul 2 nici atat (`Mate-lnfo` din OCR)."""
    ani_cu = {
        CategoriePachet.FACULTATIV: {1, 2, 3},
        CategoriePachet.LIMBI: {1, 2},
    }
    lipsa = [
        f"{g.nume} ({g.specializare} an {g.an_studiu}): {categorie.value}"
        for g in _grupe_licenta(db)
        for categorie, ani in ani_cu.items()
        if g.an_studiu in ani and categorie not in _categorii_vazute(db, g)
    ]
    assert lipsa == []


def test_consolidarea_nu_ascunde_nimic_niciunei_grupe(pagini_golden):
    """Consolidarea scoate duplicatele, dar fiecare grupa trebuie sa vada exact aceleasi
    activitati ca inainte. Inainte de reparatie se pierdeau 325 de perechi (grupa, activitate),
    intre care 29 de optionale pentru fiecare grupa INFO din anul 3."""
    s = _baza(pagini_golden, consolidata=False)
    grupe = list(s.scalars(select(Grupa)))
    inainte = {g.id: {_semnatura(o) for o in ore_pentru_grupa(s, g.id)} for g in grupe}
    consolideaza(s, an_universitar="2025-2026")
    pierdute = {
        g.nume: len(inainte[g.id] - {_semnatura(o) for o in ore_pentru_grupa(s, g.id)})
        for g in grupe
    }
    assert {k: v for k, v in pierdute.items() if v} == {}


def _pagina(titlu: str, materie: str) -> dict:
    act = {
        "ore": "8-10",
        "profesor": "Test P",
        "materie": materie,
        "tip": "curs",
        "frecventa": "",
        "saptamani": "",
        "semigrupa": "",
        "sala": "Amf.501",
    }
    return {"grupa": titlu, "Luni": [act], "Marti": [], "Miercuri": [], "Joi": [], "Vineri": []}


def test_specializari_si_pachete_noi(pagini_golden):
    """O specializare care nu exista azi (`BIO INFO`) isi primeste nodul si pachetele ei;
    un pachet fara specializare in titlu ajunge la tot anul."""
    pagini = copy.deepcopy(pagini_golden) + [
        _pagina("BIO INFO Grupa 171", "BioChimie"),
        _pagina("Optionale an I - Bio Info", "GenomicaOpt"),
        _pagina("Pachet opțional anul I", "TransversalOpt"),
    ]
    s = _baza(pagini)

    bio = gaseste_grupa(s, "171")
    info = gaseste_grupa(s, "131")
    assert bio.parinte.parinte.specializare == "BIO-INFO"

    def materii(g):  # noqa: ANN001, ANN202
        return {o.materie.nume for o in ore_pentru_grupa(s, g.id) if o.materie}

    assert {"GenomicaOpt", "TransversalOpt"} <= materii(bio)
    assert "GenomicaOpt" not in materii(info)  # e doar a specializarii noi
    assert "TransversalOpt" in materii(info)  # fara specializare -> tot anul
    # Facultativele anului I numesc doar Mate, Info, CTI: specializarea noua nu e acolo.
    assert CategoriePachet.FACULTATIV not in _categorii_vazute(s, bio)


# ---------------------------------------------------------------------------
# Dropdown-ul si cookie-ul
# ---------------------------------------------------------------------------


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def _bifate(html: str) -> set[str]:
    return set(re.findall(r'name="vizibile" value="([^"]+)" checked', html))


def _oferite(html: str) -> list[str]:
    return re.findall(r'name="oferite" value="([^"]+)"', html)


def test_dropdownul_apare_pe_grupe_nu_si_pe_sali(client):
    r = client.get("/grupa/331")
    assert 'class="optiuni"' in r.text
    assert "Selectează tot" in r.text
    assert "Opționale" in r.text and "Facultative" in r.text
    assert set(_oferite(r.text)) == _bifate(r.text)  # implicit, totul e vizibil
    assert 'class="optiuni"' not in client.get("/sala/Amf.501").text


def test_ascunderea_se_tine_in_cookie_si_filtreaza_orarul(client):
    pagina = client.get("/grupa/331").text
    oferite = _oferite(pagina)
    ascunsa, pastrate = oferite[0], oferite[1:]
    total = int(re.search(r"<strong>(\d+)</strong> activități", pagina).group(1))

    r = client.post(
        "/grupa/331/afisare",
        data={
            "csrf": _csrf(pagina),
            "oferite": oferite,
            "vizibile": pastrate,
            "inapoi": "/grupa/331",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/grupa/331"
    assert "orar_afisare=" in r.headers["set-cookie"]

    dupa = client.get("/grupa/331").text
    assert ascunsa not in _bifate(dupa)
    assert set(pastrate) == _bifate(dupa)
    assert int(re.search(r"<strong>(\d+)</strong> activități", dupa).group(1)) < total
    # alegerea e a paginii: grupa vecina vede tot
    vecina = client.get("/grupa/332").text
    assert set(_oferite(vecina)) == _bifate(vecina)


def test_selecteaza_tot_readuce_tot(client):
    pagina = client.get("/grupa/331").text
    oferite = _oferite(pagina)
    date = {"csrf": _csrf(pagina), "oferite": oferite, "inapoi": "/grupa/331"}
    client.post("/grupa/331/afisare", data={**date, "vizibile": []})
    assert _bifate(client.get("/grupa/331").text) == set()
    client.post("/grupa/331/afisare", data={**date, "vizibile": oferite})
    assert "orar_afisare" not in client.cookies  # nimic ascuns -> cookie sters


def test_fara_csrf_nu_se_salveaza_nimic(client):
    r = client.post(
        "/grupa/331/afisare",
        data={"csrf": "gresit", "oferite": ["x"], "vizibile": []},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "set-cookie" not in r.headers or "orar_afisare" not in r.headers["set-cookie"]


def test_intoarcerea_ramane_pe_sit(client):
    pagina = client.get("/grupa/331").text
    for rau in ("https://evil.example/", "//evil.example/grupa/1", "/admin/review"):
        r = client.post(
            "/grupa/331/afisare",
            data={"csrf": _csrf(pagina), "inapoi": rau},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/grupa/331"


def test_cookie_stricat_e_ignorat(client):
    client.cookies.set("orar_afisare", "!!!nu-e-base64")
    assert client.get("/grupa/331").status_code == 200


# ---------------------------------------------------------------------------
# Formatiuni care dispar
# ---------------------------------------------------------------------------


def _reingest_fara_seria_35(s, pagini_golden, *, cu_arhiva: bool):  # noqa: ANN001, ANN202
    from datetime import datetime

    from orar.db.versiuni import arhiveaza_semestrul
    from orar.worker.sync import _sterge_semestrul, curata_grupele_disparute

    if cu_arhiva:
        arhiveaza_semestrul(
            s,
            an_universitar="2025-2026",
            semestru=2,
            publicat=datetime(2026, 3, 1),
            inlocuit_de=datetime(2026, 4, 1),
        )
    # O serie desfiintata dispare si din titlurile pachetelor care o numeau.
    ramase = [
        {**p, "grupa": p["grupa"].replace("Seriile 33,34,35", "Seriile 33,34")}
        for p in pagini_golden
        if not re.search(r"Grupa 35\d", p["grupa"])
    ]
    _sterge_semestrul(s, an_universitar="2025-2026", semestru=2)
    raport = incarca_pagini(s, ramase)
    consolideaza(s, an_universitar="2025-2026")
    return curata_grupele_disparute(s, an_universitar="2025-2026", pastrate=raport.noduri)


def test_seria_disparuta_dispare_si_din_site(pagini_golden):
    s = _baza(pagini_golden)
    sterse = _reingest_fara_seria_35(s, pagini_golden, cu_arhiva=False)

    assert sterse >= 3  # seria 35 si grupele ei
    for slug in ("seria-35", "351", "352"):
        assert gaseste_grupa(s, slug) is None, slug
    # restul raman, inclusiv grupele fara ore proprii (cursurile lor sunt pe serie)
    for slug in ("seria-34", "341", "info-an-3"):
        assert gaseste_grupa(s, slug) is not None, slug


def test_ce_foloseste_arhiva_ramane(pagini_golden):
    s = _baza(pagini_golden)
    _reingest_fara_seria_35(s, pagini_golden, cu_arhiva=True)
    # versiunea anterioara inca are ore pe seria 35, deci nodurile ei raman
    assert gaseste_grupa(s, "351") is not None


# ---------------------------------------------------------------------------
# Alegerea pe activitati
# ---------------------------------------------------------------------------


def _materie_cu_curs_si_aplicatie(db):  # noqa: ANN001, ANN202
    """O materie optionala a grupei 331 care are si curs, si seminar/laborator."""
    from orar.web.afisare import grupeaza_optiunile

    for grup in grupeaza_optiunile(ore_pentru_grupa(db, gaseste_grupa(db, "331").id), set()):
        for m in grup.materii:
            tipuri = {a.tip.lower() for a in m.activitati}
            if "curs" in tipuri and tipuri - {"curs"}:
                return m
    raise AssertionError("nicio materie cu curs si aplicatie pe 331")


def test_poti_pastra_doar_seminarul(client, db):
    m = _materie_cu_curs_si_aplicatie(db)
    curs = [a.cheie for a in m.activitati if a.tip.lower() == "curs"]
    aplicatii = [a.cheie for a in m.activitati if a.tip.lower() != "curs"]

    pagina = client.get("/grupa/331").text
    oferite = _oferite(pagina)
    assert set(curs + aplicatii) <= set(oferite)
    client.post(
        "/grupa/331/afisare",
        data={
            "csrf": _csrf(pagina),
            "oferite": oferite,
            "materii": [m.slug],
            "vizibile": [k for k in oferite if k not in curs],
        },
    )
    dupa = client.get("/grupa/331").text
    bifate = _bifate(dupa)
    assert not (set(curs) & bifate)
    assert set(aplicatii) <= bifate
    # materia apare aleasa pe jumatate, iar in grila se vede in continuare
    assert f"{len(aplicatii)}/{len(curs) + len(aplicatii)}</span>" in dupa
    assert 'data-partiala="1"' in dupa
    assert f'<span class="bloc-materie">{m.nume}</span>' in dupa


def test_cheia_activitatii_nu_depinde_de_sala():
    from datetime import time
    from types import SimpleNamespace

    from orar.web.afisare import cheie_activitate

    def act(**schimbat):  # noqa: ANN003, ANN202
        baza = {
            "materie": SimpleNamespace(slug="ia"),
            "tip_ora_materie": "seminar",
            "zi_saptamana": "Joi",
            "ora_inceput": time(10),
            "ora_sfarsit": time(12),
            "frecventa": None,
            "saptamani": None,
            "semigrupa": "Gr_1",
            "sala": SimpleNamespace(nume="S.108"),
        }
        return SimpleNamespace(**{**baza, **schimbat})

    assert cheie_activitate(act()) == cheie_activitate(act(sala=SimpleNamespace(nume="L.410")))
    assert cheie_activitate(act()) != cheie_activitate(act(zi_saptamana="Vineri"))
    assert cheie_activitate(act()) != cheie_activitate(act(tip_ora_materie="curs"))
    assert cheie_activitate(act()).startswith("@")


def test_cookie_vechi_pe_materie_intreaga_merge_in_continuare(client, db):
    """Alegerile salvate inainte de alegerea pe activitati (slug-ul materiei) ascund tot."""
    import base64
    import json

    m = _materie_cu_curs_si_aplicatie(db)
    vechi = base64.urlsafe_b64encode(json.dumps({"331": [m.slug]}).encode()).decode()
    client.cookies.set("orar_afisare", vechi.rstrip("="))
    pagina = client.get("/grupa/331").text
    assert not ({a.cheie for a in m.activitati} & _bifate(pagina))
    assert f'<span class="bloc-materie">{m.nume}</span>' not in pagina

    # La urmatoarea salvare, ascunderea pe materie e inlocuita de alegerea pe activitati: cu
    # tot bifat nu mai ramane nimic ascuns, deci serverul sterge cookie-ul. (Verificam
    # raspunsul, nu borcanul clientului: cookie-ul pus de mana mai sus n-are domeniu, deci
    # clientul de test nu-l asociaza cu cel sters de server.)
    oferite = _oferite(pagina)
    r = client.post(
        "/grupa/331/afisare",
        data={"csrf": _csrf(pagina), "oferite": oferite, "materii": [m.slug], "vizibile": oferite},
        follow_redirects=False,
    )
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("orar_afisare=") and "Max-Age=0" in cookie


def test_semigrupa_filtreaza_doar_orarul_de_baza(client, db):
    """La optionale/facultative/limbi alegerea e din dropdown; filtrul de semigrupa priveste
    doar orarul de baza. `Gr_3` de la un laborator optional e semigrupa pachetului, nu a
    grupei, deci nici nu apare printre butoane."""
    # 244: baza are Gr_1 si Gr_2, pachetele au Gr_1..Gr_4
    ore = ore_pentru_grupa(db, gaseste_grupa(db, "244").id)
    baza_gr2 = [o for o in ore if o.grupa.tip != "optional" and o.semigrupa == "Gr_2"]
    pachet_alta = [
        o for o in ore if o.grupa.tip == "optional" and o.semigrupa not in (None, "Gr_1")
    ]
    assert baza_gr2 and pachet_alta  # altfel testul n-ar deosebi nimic

    def total(html: str) -> int:
        return int(re.search(r"<strong>(\d+)</strong> activități", html).group(1))

    def butoane(html: str) -> set[str]:
        return set(re.findall(r'href="\?semigrupa=(Gr_\d)', html))

    toate = client.get("/grupa/244").text
    filtrat = client.get("/grupa/244?semigrupa=Gr_1").text
    # dispar exact activitatile de baza ale semigrupei 2; cele de pachet raman toate,
    # si in grila, si in dropdown
    assert total(toate) - total(filtrat) == len(baza_gr2)
    assert _oferite(toate) == _oferite(filtrat)
    # butoanele sunt semigrupele grupei, nu Gr_3/Gr_4 ale pachetelor
    assert butoane(toate) == {"Gr_1", "Gr_2"}

    # 331 n-are semigrupe in orarul de baza: toate `Gr_N` ale ei vin din pachete
    assert butoane(client.get("/grupa/331").text) == set()


# ---------------------------------------------------------------------------
# Semigrupa tinuta minte
# ---------------------------------------------------------------------------


def _activitati(html: str) -> int:
    return int(re.search(r"<strong>(\d+)</strong> activități", html).group(1))


def _semigrupa_activa(html: str) -> str | None:
    m = re.search(r'class="filtru activ"\s+href="\?semigrupa=(Gr_\d)', html)
    return m.group(1) if m else None


def test_semigrupa_aleasa_se_tine_minte_pe_pagina(client):
    tot = _activitati(client.get("/grupa/244").text)

    r = client.get("/grupa/244?semigrupa=Gr_1")
    assert "orar_semigrupa=" in r.headers["set-cookie"]
    doar_gr1 = _activitati(r.text)
    assert doar_gr1 < tot

    # revenind fara nimic in adresa, e tot Gr_1
    inapoi = client.get("/grupa/244").text
    assert _semigrupa_activa(inapoi) == "Gr_1"
    assert _activitati(inapoi) == doar_gr1

    # alegerea e a paginii: grupa vecina vede toate semigrupele
    assert _semigrupa_activa(client.get("/grupa/243").text) is None

    # "toate semigrupele" o uita
    assert 'href="?semigrupa=toate' in inapoi
    client.get("/grupa/244?semigrupa=toate")
    assert _activitati(client.get("/grupa/244").text) == tot


def test_semigrupa_salvata_care_nu_mai_exista_e_ignorata(client):
    import base64
    import json

    valoare = base64.urlsafe_b64encode(json.dumps({"244": "Gr_7", "331": "nu"}).encode())
    client.cookies.set("orar_semigrupa", valoare.decode().rstrip("="))
    assert _semigrupa_activa(client.get("/grupa/244").text) is None
    assert client.get("/grupa/331").status_code == 200


# ---------------------------------------------------------------------------
# Data aleasa
# ---------------------------------------------------------------------------


def test_selectorul_de_data(client):
    from datetime import date

    azi = client.get("/grupa/244").text
    assert f'name="zi" value="{date.today().isoformat()}"' in azi
    assert "Înapoi la data de azi" not in azi

    # o zi din semestru: se poate filtra pe saptamana, iar filtrele pastreaza ziua
    zi = client.get("/grupa/244?zi=2026-04-22").text
    assert 'name="zi" value="2026-04-22"' in zi
    assert "doar săptămâna curentă" in zi
    assert "Înapoi la data de azi" in zi
    assert "saptamana=true&amp;zi=2026-04-22" in zi
    assert "semigrupa=Gr_1&amp;zi=2026-04-22" in zi


def test_selectorul_de_data_pe_pagina_salii(client):
    from datetime import date

    azi = client.get("/sala/Amf.501").text
    assert f'name="zi" value="{date.today().isoformat()}"' in azi
    assert "Înapoi la data de azi" not in azi

    zi = client.get("/sala/Amf.501?zi=2026-04-22").text
    assert 'name="zi" value="2026-04-22"' in zi
    assert "doar săptămâna curentă" in zi
    assert "Înapoi la data de azi" in zi
    # filtrul de saptamana pastreaza ziua aleasa (`&` e literal in sablon, deci neescapat)
    assert "?saptamana=true&zi=2026-04-22" in zi
