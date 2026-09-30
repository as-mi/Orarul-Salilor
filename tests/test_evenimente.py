"""Evenimentele: activitati ASMI si alte activitati cu data -- sali libere, vizibilitate pe
orare, weekend si calendarul public."""

from __future__ import annotations

import re
from datetime import date, datetime, time

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from orar.db.evenimente import evenimente_in_grila, sali_libere
from orar.db.models import Base, Eveniment, Ora, Sala
from orar.db.queries import gaseste_grupa
from orar.domain.grid import construieste_grila
from orar.ingest.consolidate import consolideaza
from orar.ingest.load import incarca_pagini
from tests.conftest import _engine_memorie

ADMIN, PAROLA = "sefa", "o-parola-doar-pentru-teste"
#: O sambata si lunea dinaintea ei, in afara oricarui semestru din datele de test.
SAMBATA, LUNI = date(2026, 10, 17), date(2026, 10, 12)


@pytest.fixture
def s(pagini_golden, monkeypatch):
    monkeypatch.setenv("ORAR_ADMIN_USER", ADMIN)
    monkeypatch.setenv("ORAR_ADMIN_PAROLA", PAROLA)
    monkeypatch.delenv("ORAR_ADMIN_PAROLA_HASH", raising=False)
    engine = _engine_memorie()

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as sesiune:
        incarca_pagini(sesiune, pagini_golden)
        consolideaza(sesiune, an_universitar="2025-2026")
        # doua sali cu capacitate cunoscuta, ca ordinea sa aiba ce arata
        for nume, locuri in (("Amf.701", 150), ("L.410", 30)):
            sesiune.scalar(select(Sala).where(Sala.nume == nume)).nr_locuri = locuri
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


def _csrf(client, cale: str) -> str:  # noqa: ANN001
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


def _intra(client) -> None:  # noqa: ANN001
    client.post(
        "/login",
        data={"csrf": _csrf(client, "/login"), "email": ADMIN, "parola": PAROLA},
        follow_redirects=False,
    )


def _sala(s, nume: str) -> Sala:  # noqa: ANN001
    return s.scalar(select(Sala).where(Sala.nume == nume))


def _eveniment(s, **campuri) -> Eveniment:  # noqa: ANN001, ANN003
    e = Eveniment(
        **{
            "fel": "asmi",
            "titlu": "Seara de jocuri",
            "data_inceput": SAMBATA,
            "data_sfarsit": SAMBATA,
            "ora_inceput": time(18),
            "ora_sfarsit": time(21, 30),
            "vizibilitate": "toate",
            "creat_de": "test",
            "creat_la": datetime(2026, 10, 1),
            **campuri,
        }
    )
    s.add(e)
    s.commit()
    return e


def _libere(s, zi, inceput, sfarsit, **k):  # noqa: ANN001, ANN003, ANN202
    perioada = s.scalar(select(Ora.perioada_id).limit(1))
    return [
        x.sala.nume
        for x in sali_libere(
            s, zi, time(inceput), time(sfarsit), perioada_id=perioada, saptamana=None, **k
        )
    ]


# ------------------------------------------------------------------ sali libere


def test_salile_libere_vin_de_la_cea_mai_incapatoare(s):
    libere = _libere(s, SAMBATA, 10, 12)
    # sambata nu sunt cursuri: toate salile facultatii, cu cele masurate primele
    assert libere[:2] == ["Amf.701", "L.410"]
    assert len(libere) == len(list(s.scalars(select(Sala).where(Sala.tip == "fizica"))))


def test_o_sala_cu_curs_nu_e_libera_in_timpul_lui(s):
    o = s.scalar(select(Ora).where(Ora.sala_id == _sala(s, "Amf.701").id).order_by(Ora.id))
    zi = LUNI.replace(
        day=LUNI.day + ["Luni", "Marti", "Miercuri", "Joi", "Vineri"].index(o.zi_saptamana)
    )
    assert "Amf.701" not in _libere(s, zi, o.ora_inceput.hour, o.ora_sfarsit.hour)
    assert "Amf.701" in _libere(s, SAMBATA, o.ora_inceput.hour, o.ora_sfarsit.hour)


def test_un_eveniment_ocupa_sala_doar_in_orele_lui(s):
    e = _eveniment(s, sala=_sala(s, "Amf.701"))
    assert "Amf.701" not in _libere(s, SAMBATA, 20, 22)
    assert "Amf.701" in _libere(s, SAMBATA, 10, 18)  # se termina exact cand incepe celalalt
    # cand se editeaza chiar el, sala lui nu e "ocupata"
    assert "Amf.701" in _libere(s, SAMBATA, 20, 22, fara_eveniment=e.id)


# ------------------------------------------------------------------ pe orare


def test_vizibilitatea_hotaraste_pe_ce_orare_apare(s):
    sala = _sala(s, "L.410")
    _eveniment(s, titlu="Pentru toti", sala=sala)
    _eveniment(s, titlu="Doar Info", vizibilitate="specializari", specializari="INFO")
    _eveniment(s, titlu="Doar sala", vizibilitate="sala", sala=_sala(s, "Amf.701"), fel="alta")

    def titluri(**k) -> set[str]:  # noqa: ANN003
        return {x.eveniment.titlu for x in evenimente_in_grila(s, SAMBATA, **k)}

    assert titluri(grupa=gaseste_grupa(s, "244")) == {"Pentru toti", "Doar Info"}
    assert titluri(grupa=gaseste_grupa(s, "101")) == {"Pentru toti"}
    assert titluri(sala=_sala(s, "Amf.701")) == {"Doar sala"}
    assert titluri(sala=sala) == {"Pentru toti"}
    # in alta saptamana nu apare nimic
    assert evenimente_in_grila(s, date(2026, 10, 5), grupa=gaseste_grupa(s, "244")) == []


def test_grila_se_intinde_pentru_weekend_si_pentru_seara(s):
    _eveniment(s)
    grila = construieste_grila([], evenimente=evenimente_in_grila(s, LUNI))
    assert [z.zi for z in grila.zile][-1] == "Sambata"  # duminica n-are nimic, deci lipseste
    assert grila.ore[-1] == 21  # 21:30 ocupa si coloana 21:00-22:00
    assert grila.are_evenimente
    # fara evenimente, grila e cea obisnuita
    obisnuita = construieste_grila([])
    assert len(obisnuita.zile) == 5 and obisnuita.ore[-1] == 19


def test_evenimentul_apare_pe_pagina_grupei_doar_in_saptamana_lui(client, s):
    _eveniment(s, sala=_sala(s, "L.410"))
    in_saptamana = client.get(f"/grupa/244?zi={LUNI.isoformat()}").text
    assert "Seara de jocuri" in in_saptamana and "Sâmbătă" in in_saptamana
    assert "Seara de jocuri" not in client.get("/grupa/244?zi=2026-10-05").text
    assert "Seara de jocuri" in client.get(f"/sala/l-410?zi={SAMBATA.isoformat()}").text


# ------------------------------------------------------------------ adminul


def _formular(**campuri) -> dict:  # noqa: ANN003
    return {
        "fel": "asmi",
        "titlu": "Hackathon",
        "data_inceput": SAMBATA.isoformat(),
        "ora_inceput": "10:00",
        "ora_sfarsit": "18:00",
        "unde": "sala",
        "sala": "amf-701",
        "vizibilitate": "toate",
        **campuri,
    }


def test_adminul_cauta_o_sala_libera_si_adauga_evenimentul(client, s):
    _intra(client)
    cautare = client.get(
        "/admin/evenimente/sali-libere",
        params={
            "data_inceput": SAMBATA.isoformat(),
            "ora_inceput": "10:00",
            "ora_sfarsit": "18:00",
        },
    ).text
    assert cautare.index("Amf.701") < cautare.index("L.410") and "150 locuri" in cautare

    r = client.post(
        "/admin/evenimente/nou",
        data={"csrf": _csrf(client, "/admin/evenimente/nou"), **_formular()},
        follow_redirects=False,
    )
    assert r.status_code == 303
    e = s.scalar(select(Eveniment))
    assert (e.titlu, e.sala.nume, e.ora_sfarsit) == ("Hackathon", "Amf.701", time(18))

    # aceeasi sala, peste el: refuzat
    r = client.post(
        "/admin/evenimente/nou",
        data={
            "csrf": _csrf(client, "/admin/evenimente/nou"),
            **_formular(titlu="Altul", ora_inceput="17:00", ora_sfarsit="19:00"),
        },
    )
    assert r.status_code == 400 and "nu mai e liberă" in r.text
    assert len(list(s.scalars(select(Eveniment)))) == 1


@pytest.mark.parametrize(
    ("campuri", "mesaj"),
    [
        ({"titlu": ""}, "numele"),
        ({"ora_sfarsit": ""}, "ora de început, și ora de sfârșit"),
        ({"ora_inceput": "19:00"}, "după cea de început"),
        ({"data_sfarsit": "2026-10-18"}, "o singură zi"),
        ({"vizibilitate": "specializari"}, "cel puțin o specializare sau un an"),
        ({"unde": "alt", "loc": ""}, "locul"),
        ({"link": "javascript:alert(1)"}, "https://"),
    ],
)
def test_formularul_refuza_ce_nu_are_sens(client, s, campuri, mesaj):
    _intra(client)
    r = client.post(
        "/admin/evenimente/nou",
        data={"csrf": _csrf(client, "/admin/evenimente/nou"), **_formular(**campuri)},
    )
    assert r.status_code == 400 and mesaj in r.text
    assert s.scalar(select(Eveniment)) is None


def test_o_perioada_fara_sala_si_fara_ore(client, s):
    _intra(client)
    r = client.post(
        "/admin/evenimente/nou",
        data={
            "csrf": _csrf(client, "/admin/evenimente/nou"),
            **_formular(
                titlu="Formular recrutări", data_sfarsit="2026-10-31", ora_inceput="",
                ora_sfarsit="", unde="fara", link="https://example.org/formular",
            ),
        },
        follow_redirects=False,
    )  # fmt: skip
    assert r.status_code == 303
    e = s.scalar(select(Eveniment))
    assert e.pe_mai_multe_zile and e.sala is None and e.ora_inceput is None
    # fara ore nu intra in grile, dar e in calendar
    assert evenimente_in_grila(s, SAMBATA) == []
    calendar = client.get("/calendar-asmi?luna=2026-10").text
    assert "Formular recrutări" in calendar and "17-31 octombrie 2026" in calendar
    assert 'href="https://example.org/formular"' in calendar


def test_doar_adminul_administreaza_evenimentele(client, s):
    for cale in ("/admin/evenimente", "/admin/evenimente/nou", "/admin/evenimente/sali-libere"):
        assert client.get(cale, follow_redirects=False).status_code in (303, 401, 403)
    r = client.post("/admin/evenimente/nou", data=_formular(), follow_redirects=False)
    assert r.status_code in (303, 401, 403) and s.scalar(select(Eveniment)) is None


def test_editarea_si_stergerea(client, s):
    e = _eveniment(s, sala=_sala(s, "Amf.701"))
    _intra(client)
    pagina = client.get(f"/admin/evenimente/{e.id}").text
    assert 'value="amf-701" checked' in pagina  # sala lui ramane aleasa, desi el o ocupa
    r = client.post(
        f"/admin/evenimente/{e.id}",
        data={"csrf": _csrf(client, "/admin/evenimente"), **_formular(titlu="Mutat", sala="l-410")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    s.expire_all()
    assert (e.titlu, e.sala.nume) == ("Mutat", "L.410")

    client.post(f"/admin/evenimente/{e.id}/sterge", data={"csrf": "gresit"})
    assert s.get(Eveniment, e.id) is not None
    client.post(
        f"/admin/evenimente/{e.id}/sterge", data={"csrf": _csrf(client, "/admin/evenimente")}
    )
    assert s.scalar(select(Eveniment)) is None


# ------------------------------------------------------------------ calendarul


def test_calendarul_public_arata_doar_evenimentele_asmi(client, s):
    _eveniment(s, descriere="Aduceți jocuri.", sala=_sala(s, "L.410"))
    _eveniment(s, titlu="Examen de admitere", fel="alta", sala=_sala(s, "Amf.701"))
    r = client.get("/calendar-asmi?luna=2026-10")
    assert r.status_code == 200
    assert "Seara de jocuri" in r.text and "Aduceți jocuri." in r.text
    assert "sâmbătă, 17 octombrie 2026 · 18:00-21:30" in r.text
    assert "Examen de admitere" not in r.text
    # o luna scrisa gresit nu strica pagina
    assert client.get("/calendar-asmi?luna=nu-e-luna").status_code == 200


def test_sala_unui_eveniment_nu_se_sterge_ca_orfana(s):
    from orar.worker.sync import curata_orfanii

    sala = Sala(nume="S.999", slug="s-999", tip="fizica")
    s.add(sala)
    _eveniment(s, sala=sala)
    curata_orfanii(s)
    assert _sala(s, "S.999") is not None


def test_culoarea_evenimentului(client, s):
    _intra(client)
    formular = client.get("/admin/evenimente/nou").text
    assert 'name="culoare" value="verde"' in formular
    client.post(
        "/admin/evenimente/nou",
        data={"csrf": _csrf(client, "/admin/evenimente/nou"), **_formular(culoare="verde")},
    )
    e = s.scalar(select(Eveniment))
    assert e.culoare == "verde"
    assert "culoare-verde" in client.get("/calendar-asmi?luna=2026-10").text
    assert "culoare-verde" in client.get(f"/grupa/244?zi={LUNI.isoformat()}").text
    # o culoare care nu e in lista inseamna cea obisnuita
    client.post(
        f"/admin/evenimente/{e.id}",
        data={"csrf": _csrf(client, "/admin/evenimente"), **_formular(culoare="url(x)")},
    )
    s.expire_all()
    assert e.culoare is None


def test_culoarea_proprie(client, s):
    _intra(client)

    def salveaza(**campuri) -> Eveniment:  # noqa: ANN003
        e = s.scalar(select(Eveniment))
        cale = f"/admin/evenimente/{e.id}" if e else "/admin/evenimente/nou"
        client.post(
            cale, data={"csrf": _csrf(client, "/admin/evenimente/nou"), **_formular(**campuri)}
        )
        s.expire_all()
        return s.scalar(select(Eveniment))

    e = salveaza(culoare="proprie", culoare_proprie="#12AB9F")
    assert e.culoare == "#12ab9f"
    calendar = client.get("/calendar-asmi?luna=2026-10").text
    assert 'culoare-proprie" id="ev-' in calendar and 'style="--ev: #12ab9f;"' in calendar
    assert "--ev: #12ab9f;" in client.get(f"/grupa/244?zi={LUNI.isoformat()}").text
    # formularul o arata aleasa
    assert 'name="culoare_proprie" value="#12ab9f"' in client.get(f"/admin/evenimente/{e.id}").text
    # doar forma #rrggbb ajunge in pagina; orice altceva inseamna culoarea obisnuita
    for rea in ("red", "#12ab9f; background: url(x)", "#fff", '"><script>'):
        assert salveaza(culoare="proprie", culoare_proprie=rea).culoare is None


def test_un_eveniment_nou_porneste_de_la_ziua_de_azi(client):
    _intra(client)
    formular = client.get("/admin/evenimente/nou").text
    assert f'name="data_inceput" required value="{date.today().isoformat()}"' in formular


def test_implicit_evenimentul_nu_apare_pe_niciun_orar_de_grupa(client, s):
    _intra(client)
    assert 'name="vizibilitate" value="sala" checked' in client.get("/admin/evenimente/nou").text
    date_formular = _formular()
    del date_formular["vizibilitate"]
    client.post(
        "/admin/evenimente/nou",
        data={"csrf": _csrf(client, "/admin/evenimente/nou"), **date_formular},
    )
    assert s.scalar(select(Eveniment)).vizibilitate == "sala"
    assert evenimente_in_grila(s, SAMBATA, grupa=gaseste_grupa(s, "244")) == []
    assert len(evenimente_in_grila(s, SAMBATA, sala=_sala(s, "Amf.701"))) == 1


def test_evenimentul_doar_pentru_unii_ani(client, s):
    from orar.db.evenimente import cheie_an

    assert cheie_an(gaseste_grupa(s, "244")) == "L2"
    assert cheie_an(gaseste_grupa(s, "407")) == "M1"

    def vad(identificator: str) -> set[str]:
        return {
            x.eveniment.titlu
            for x in evenimente_in_grila(s, SAMBATA, grupa=gaseste_grupa(s, identificator))
        }

    _eveniment(s, titlu="Boboci", vizibilitate="specializari", ani="L1")
    _eveniment(s, titlu="Info anul 2", vizibilitate="specializari", specializari="INFO", ani="L2")
    assert vad("101") == {"Boboci"} and vad("131") == {"Boboci"}
    assert vad("244") == {"Info anul 2"}
    assert (
        vad("344") == set() and vad("407") == set()
    )  # alt an; masterul anul 1 nu e licenta anul 1

    # din formular: doar ani, fara nicio specializare
    _intra(client)
    formular = client.get("/admin/evenimente/nou").text
    assert 'name="ani" value="L1"' in formular and "Master · anul 1" in formular
    r = client.post(
        "/admin/evenimente/nou",
        data={
            "csrf": _csrf(client, "/admin/evenimente/nou"),
            **_formular(titlu="Master", vizibilitate="specializari", ani=["M1", "M2", "X9"]),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    e = s.scalar(select(Eveniment).where(Eveniment.titlu == "Master"))
    assert (e.ani, e.specializari) == ("M1,M2", None)
    assert "Master" in vad("407")
    assert "master anul 1, master anul 2" in client.get("/admin/evenimente").text
