"""Tutoriatele: sedintele, salile libere saptamanal, asignarea tutorilor, panoul
responsabilului, alegerea pe orar si importul foii de pe site-ul facultatii."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from orar.db.models import (
    Base,
    Ora,
    Perioada,
    Sala,
    Tutor,
    Tutoriat,
    TutoriatAnulare,
    TutoriatTutor,
    User,
)
from orar.db.orare import perioada_implicita
from orar.db.queries import gaseste_grupa
from orar.db.tutoriate import (
    DURATA_ASIGNARE,
    leaga_invitatiile,
    oferite_grupei,
    pentru_grupa,
    sali_libere_saptamanal,
    sedintele,
    tutoriate_in_grila,
)
from orar.domain.tutoriate import sedinte
from orar.domain.weeks import CalendarAcademic, PerioadaAn
from orar.ingest.consolidate import consolideaza
from orar.ingest.load import incarca_pagini
from orar.web.auth import hash_parola
from orar.web.routers.tutoriate import importa_foaia
from tests.conftest import _engine_memorie, intra_sso

ADMIN, PAROLA = "sefa", "o-parola-doar-pentru-teste"
PAROLA_CONT = "parola-buna-123"
#: Luni, in afara oricarei structuri configurate in test: sedinte in fiecare saptamana.
LUNI = date(2026, 3, 2)
#: Lunea viitoare: tutoriatele publicate care nu s-au terminat apar pe pagina publica.
VIITOARE = date.today() + timedelta(days=7 - date.today().weekday())

#: Foaia tutoriatelor, in formatul de pe site-ul FMI (nume si adrese inventate).
FOAIE = """\
,,,,,,
Matematică an I,,,,,,
Materia,Nume,Adresa,Interval,Sala,Link Whatsapp,Link Teams
Geometrie I,Ana Pop,ana.pop@s.unibuc.ro,Marți 16 - 18,sala 220,https://tinyurl.com/wa-geo,https://tinyurl.com/teams-geo
,Dan Ionescu,dan.ionescu@s.unibuc.ro,,,,
Algebră I,Ioana Radu,ioana.radu@s.unibuc.ro,Vineri 8-10,L-321,,
Ceva greșit,X Y,x.y@s.unibuc.ro,Cândva,sala 1,,
,,,,,,
Informatică an II,,,,,,
Materia,Nume,Adresa,Interval,Sala,Link Whatsapp,Link Teams
Programare orientată pe obiecte,Ana Pop,ana.pop@s.unibuc.ro,Joi 18 - 20,sala 220,,
"""


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


def _csrf(client, cale: str = "/login?admin=1") -> str:  # noqa: ANN001
    return re.search(r'name="csrf" value="([^"]+)"', client.get(cale).text).group(1)


def _intra(client, email: str = ADMIN, parola: str = PAROLA) -> None:  # noqa: ANN001
    r = client.post(
        "/login",
        data={"csrf": _csrf(client), "email": email, "parola": parola},
        follow_redirects=False,
    )
    assert r.status_code == 303


def _cont(s, nume: str, email: str, *, grupa: str | None = None, rol: str = "student") -> User:  # noqa: ANN001
    u = User(
        nume=nume,
        email=email,
        parola_hash=hash_parola(PAROLA_CONT),
        rol=rol,
        grupa=gaseste_grupa(s, grupa) if grupa else None,
    )
    s.add(u)
    s.commit()
    return u


def _sala_libera(s) -> Sala:  # noqa: ANN001
    """O sala fizica in care nu e nicio ora vineri dupa 18."""
    ocupate = set(
        s.scalars(
            select(Ora.sala_id).where(Ora.zi_saptamana == "Vineri", Ora.ora_sfarsit > time(18))
        )
    )
    return next(
        x
        for x in s.scalars(select(Sala).where(Sala.tip == "fizica").order_by(Sala.nume))
        if x.id not in ocupate
    )


def _ora_cu_sala(s) -> Ora:  # noqa: ANN001
    return s.scalar(
        select(Ora)
        .join(Ora.sala)
        .where(Sala.tip == "fizica", Ora.frecventa.is_(None))
        .order_by(Ora.id)
        .limit(1)
    )


def _tutoriat(s, **campuri) -> Tutoriat:  # noqa: ANN001, ANN003
    orar = perioada_implicita(s)
    t = Tutoriat(
        **{
            "an_univ": orar.an_univ,
            "semestru": orar.semestru,
            "materie": "Programare orientată pe obiecte",
            "zi_saptamana": "Vineri",
            "ora_inceput": time(18),
            "ora_sfarsit": time(20),
            "data_inceput": LUNI,
            "data_sfarsit": LUNI + timedelta(weeks=6),
            "stare": "publicat",
            "creat_de": "test",
            "creat_la": datetime(2026, 2, 1),
            **campuri,
        }
    )
    s.add(t)
    s.commit()
    return t


# ------------------------------------------------------------------ sedintele


def test_sedintele_sar_peste_vacanta_si_zilele_libere():
    cal = CalendarAcademic(
        perioade=[
            PerioadaAn("didactica", date(2026, 3, 2), date(2026, 4, 5), semestru=2),
            PerioadaAn("vacanta", date(2026, 4, 6), date(2026, 4, 12), semestru=2),
            PerioadaAn("didactica", date(2026, 4, 13), date(2026, 6, 7), semestru=2),
            PerioadaAn("liber", date(2026, 3, 18), date(2026, 3, 18), semestru=2),
        ]
    )
    zile = sedinte("Miercuri", date(2026, 3, 2), date(2026, 4, 30), cal)
    assert date(2026, 3, 4) in zile
    assert date(2026, 3, 18) not in zile  # zi libera
    assert date(2026, 4, 8) not in zile  # vacanta
    assert date(2026, 4, 15) in zile
    # saptamanile impare: 4 martie e saptamana 1, 18 martie (a 3-a) e zi libera
    impare = sedinte("Miercuri", date(2026, 3, 2), date(2026, 3, 31), cal, "SI")
    assert impare == [date(2026, 3, 4)]
    pare = sedinte("Miercuri", date(2026, 3, 2), date(2026, 3, 31), cal, "SP")
    assert pare == [date(2026, 3, 11), date(2026, 3, 25)]


def test_fara_structura_se_tine_in_fiecare_saptamana():
    zile = sedinte("Luni", date(2026, 3, 2), date(2026, 3, 30), CalendarAcademic())
    assert len(zile) == 5 and all(z.weekday() == 0 for z in zile)


# ------------------------------------------------------------------ salile


def test_sala_e_libera_saptamanal_doar_daca_e_libera_la_toate_sedintele(s):
    o = _ora_cu_sala(s)
    sali, zile = sali_libere_saptamanal(
        s, o.zi_saptamana, o.ora_inceput, o.ora_sfarsit, LUNI, LUNI + timedelta(weeks=4)
    )
    assert len(zile) == 5
    ocupata = next(x for x in sali if x.sala.id == o.sala_id)
    assert not ocupata.mereu and ocupata.libera == 0
    assert sali[0].mereu  # intai cele libere la toate


def test_un_tutoriat_ocupa_sala_pentru_celelalte(s):
    sala = _sala_libera(s)
    args = ("Vineri", time(18), time(20), LUNI, LUNI + timedelta(weeks=4))
    inainte = next(x for x in sali_libere_saptamanal(s, *args)[0] if x.sala.id == sala.id)
    assert inainte.mereu
    t = _tutoriat(s, sala=sala)
    dupa = next(x for x in sali_libere_saptamanal(s, *args)[0] if x.sala.id == sala.id)
    assert not dupa.mereu
    # ...dar nu pentru el insusi, cand e editat
    al_lui = next(x for x in sali_libere_saptamanal(s, *args, fara=t.id)[0] if x.sala.id == sala.id)
    assert al_lui.mereu


# ------------------------------------------------------------------ public si orar


def test_publicul_unui_tutoriat(s):
    g = gaseste_grupa(s, "244")
    assert pentru_grupa(_tutoriat(s), g)
    assert not pentru_grupa(_tutoriat(s, ani="L1"), g)
    assert pentru_grupa(_tutoriat(s, ani="L2"), g)


def test_tutoriatul_apare_pe_orar_doar_daca_e_ales(s):
    g = gaseste_grupa(s, "244")
    t = _tutoriat(s)
    assert tutoriate_in_grila(s, LUNI, grupa=g, alese=set()) == []
    blocuri = tutoriate_in_grila(s, LUNI, grupa=g, alese={t.id})
    assert len(blocuri) == 1 and blocuri[0].zi == "Vineri"
    assert blocuri[0].eveniment.titlu.startswith("Tutoriat")
    # o ciorna nu apare nici aleasa
    t.stare = "ciorna"
    s.commit()
    assert tutoriate_in_grila(s, LUNI, grupa=g, alese={t.id}) == []


def test_tutorul_isi_vede_tutoriatul_fara_sa_il_aleaga(s):
    tutor = _cont(s, "Ana Pop", "ana@s.unibuc.ro", grupa="244")
    t = _tutoriat(s, stare="ciorna")
    t.echipa = [TutoriatTutor(user=tutor, principal=True)]
    s.commit()
    blocuri = tutoriate_in_grila(s, LUNI, grupa=tutor.grupa, tutor=tutor)
    assert len(blocuri) == 1 and blocuri[0].eveniment.al_meu


def test_sedinta_anulata_ramane_pe_orar_ca_anulata(s):
    t = _tutoriat(s)
    vineri = LUNI + timedelta(days=4)
    s.add(TutoriatAnulare(tutoriat=t, data=vineri, de_catre="test"))
    s.commit()
    (bloc,) = tutoriate_in_grila(s, LUNI, grupa=gaseste_grupa(s, "244"), alese={t.id})
    assert bloc.eveniment.anulat
    assert vineri in sedintele(s, t)


# ------------------------------------------------------------------ tutorii


def test_invitatia_pe_email_se_leaga_la_primul_login_sso(client, s, sso_fals):
    s.add(
        Tutor(
            email="nou@s.unibuc.ro",
            materie="Algebră I",
            valabil_de_la=date.today(),
            valabil_pana_la=date.today() + DURATA_ASIGNARE,
            creat_de="test",
            creat_la=datetime.now(),
        )
    )
    s.commit()
    r = intra_sso(client, sso_fals, email="Nou@s.unibuc.ro", nume="Cineva Nou")
    assert r.status_code == 303
    tutor = s.scalar(select(Tutor))
    assert tutor.user is not None and tutor.user.email == "nou@s.unibuc.ro"
    assert leaga_invitatiile(s, tutor.user) == 0  # o singura data


def test_asignarea_tine_6_luni_si_se_poate_prelungi(client, s):
    tutor = _cont(s, "Ana Pop", "ana@s.unibuc.ro")
    _intra(client)
    csrf = _csrf(client, "/tutoriate/panou?tab=tutori")
    for materie in ("Algebră I", "Geometrie I"):
        r = client.post(
            "/tutoriate/panou/tutori",
            data={"csrf": csrf, "cine": str(tutor.id), "materie": materie},
            follow_redirects=False,
        )
        assert r.status_code == 303
    asignari = list(s.scalars(select(Tutor).where(Tutor.user_id == tutor.id)))
    assert len(asignari) == 2  # se cumuleaza
    assert all(a.valabil_pana_la == date.today() + DURATA_ASIGNARE for a in asignari)
    client.post(f"/tutoriate/panou/tutori/{asignari[0].id}/prelungeste", data={"csrf": csrf})
    s.refresh(asignari[0])
    assert asignari[0].valabil_pana_la == date.today() + 2 * DURATA_ASIGNARE


# ------------------------------------------------------------------ cine intra in panou


def test_panoul_e_doar_al_responsabilului(client, s, sso_fals):
    assert client.get("/tutoriate/panou", follow_redirects=False).status_code == 303
    intra_sso(client, sso_fals, email="student@s.unibuc.ro")
    assert client.get("/tutoriate/panou").status_code == 403
    assert client.get("/admin/statistici").status_code == 403

    # responsabilul are rolul de client `responsabil-tutoriate` in SSO
    client.cookies.clear()
    intra_sso(
        client, sso_fals, email="student@s.unibuc.ro", roluri_client=("responsabil-tutoriate",)
    )
    for tab in ("planificare", "tutoriate", "tutori", "sali", "import"):
        assert client.get(f"/tutoriate/panou?tab={tab}").status_code == 200, tab
    # statisticile sunt o permisiune aparte: rolul de client al tutoriatelor nu le aduce
    assert client.get("/admin/statistici").status_code == 403
    # tutoriate e o sectiune a panoului, ca toate permisiunile functionale
    assert client.get("/admin").status_code == 200
    assert "Tutoriate" in client.get("/admin").text


def test_adminul_vede_responsabilii_din_sso(client, s, sso_fals):
    intra_sso(client, sso_fals, email="vol@s.unibuc.ro", roluri_client=("responsabil-tutoriate",))
    client.cookies.clear()
    _intra(client)
    panou = client.get("/admin").text
    # rolul de client `responsabil-tutoriate` aduce permisiunea `tutoriate`, ca pastila
    assert "tutoriate" in panou
    # iar adminul poate ajusta permisiunile fiecarui cont
    assert 'name="chei"' in panou and "/permisiuni" in panou


# ------------------------------------------------------------------ formularul


def test_responsabilul_adauga_si_publica_un_tutoriat(client, s):
    tutor = _cont(s, "Ana Pop", "ana@s.unibuc.ro")
    sala = _sala_libera(s)
    _intra(client)
    formular = client.get("/tutoriate/panou/tutoriat/nou?zi=Vineri&ora=18")
    assert formular.status_code == 200
    r = client.post(
        "/tutoriate/panou/tutoriat/nou",
        data={
            "csrf": _csrf(client, "/tutoriate/panou"),
            "materie": "Programare orientată pe obiecte",
            "zi_saptamana": "Vineri",
            "ora_inceput": "18",
            "ora_sfarsit": "20",
            "data_inceput": VIITOARE.isoformat(),
            "data_sfarsit": (VIITOARE + timedelta(weeks=6)).isoformat(),
            "sala": sala.slug,
            "ani": ["L2"],
            "tutori": [str(tutor.id)],
            "link_whatsapp": "https://chat.whatsapp.com/x",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:500]
    t = s.scalar(select(Tutoriat))
    assert t.stare == "ciorna" and t.sala_id == sala.id and t.ani == "L2"
    assert [m.user_id for m in t.echipa] == [tutor.id] and t.echipa[0].principal
    assert "Programare orientată" not in client.get("/tutoriate").text  # ciorna

    client.post(
        f"/tutoriate/panou/tutoriat/{t.id}/stare",
        data={"csrf": _csrf(client, "/tutoriate/panou"), "stare": "publicat"},
    )
    assert "Programare orientată pe obiecte" in client.get("/tutoriate").text
    assert client.get(f"/tutoriate/panou/tutoriat/{t.id}").status_code == 200


def test_nu_se_poate_pune_intr_o_sala_ocupata(client, s):
    o = _ora_cu_sala(s)
    _intra(client)
    r = client.post(
        "/tutoriate/panou/tutoriat/nou",
        data={
            "csrf": _csrf(client, "/tutoriate/panou"),
            "materie": "Algebră",
            "zi_saptamana": o.zi_saptamana,
            "ora_inceput": str(o.ora_inceput.hour),
            "ora_sfarsit": str(o.ora_sfarsit.hour),
            "data_inceput": LUNI.isoformat(),
            "data_sfarsit": (LUNI + timedelta(weeks=3)).isoformat(),
            "sala": o.sala.slug,
        },
    )
    assert r.status_code == 400 and "nu e liberă la toate ședințele" in r.text
    assert s.scalar(select(Tutoriat)) is None


def test_salile_libere_saptamanal_prin_htmx(client, s):
    _intra(client)
    r = client.get(
        "/tutoriate/panou/sali-libere",
        params={
            "zi_saptamana": "Vineri",
            "ora_inceput": "18:00",
            "ora_sfarsit": "20:00",
            "data_inceput": LUNI.isoformat(),
            "data_sfarsit": (LUNI + timedelta(weeks=4)).isoformat(),
        },
    )
    assert r.status_code == 200 and "săli libere la toate" in r.text


# ------------------------------------------------------------------ tutorul


def test_tutorul_editeaza_linkurile_si_anuleaza_o_sedinta(client, s, sso_fals):
    tutor = _cont(s, "Ana Pop", "ana@s.unibuc.ro", grupa="244")
    t = _tutoriat(s)
    t.echipa = [TutoriatTutor(user=tutor, principal=True)]
    s.commit()
    intra_sso(client, sso_fals, email=tutor.email)
    pagina = client.get(f"/tutoriate/al-meu/{t.id}")
    assert pagina.status_code == 200
    csrf = _csrf(client, f"/tutoriate/al-meu/{t.id}")
    client.post(
        f"/tutoriate/al-meu/{t.id}",
        data={
            "csrf": csrf,
            "link_teams": "https://teams.microsoft.com/x",
            "descriere": "Aduceți laptopul.",
        },
    )
    s.refresh(t)
    assert t.link_teams == "https://teams.microsoft.com/x" and t.descriere == "Aduceți laptopul."
    # ziua si ora nu le poate schimba: panoul nu e al lui
    assert client.get(f"/tutoriate/panou/tutoriat/{t.id}").status_code == 403

    vineri = LUNI + timedelta(days=4)
    client.post(
        f"/tutoriate/{t.id}/anulare",
        data={"csrf": csrf, "data": vineri.isoformat(), "motiv": "boală"},
    )
    assert s.scalar(select(TutoriatAnulare.motiv)) == "boală"
    # pe orarul lui apare, marcat
    assert "anulat" in client.get(f"/grupa/244?zi={vineri}").text
    assert "ții tu" in client.get(f"/grupa/244?zi={vineri + timedelta(weeks=1)}").text
    assert "Sunt tutor" in client.get("/account").text


def test_altcineva_nu_poate_anula(client, s, sso_fals):
    t = _tutoriat(s)
    altul = _cont(s, "Altul", "altul@s.unibuc.ro")
    intra_sso(client, sso_fals, email=altul.email)
    r = client.post(
        f"/tutoriate/{t.id}/anulare",
        data={"csrf": _csrf(client), "data": (LUNI + timedelta(days=4)).isoformat()},
    )
    assert r.status_code == 403
    assert client.get(f"/tutoriate/al-meu/{t.id}").status_code == 403


# ------------------------------------------------------------------ alegerea


def test_alegerea_fara_cont_se_tine_in_cookie(client, s):
    t = _tutoriat(s, data_sfarsit=date.today() + timedelta(weeks=8))
    pagina = client.get("/grupa/244").text
    assert "Tutoriate" in pagina and f'name="alese" value="{t.id}"' in pagina
    client.post(
        "/tutoriate/alegere",
        data={
            "csrf": _csrf(client),
            "oferite": [str(t.id)],
            "alese": [str(t.id)],
            "inapoi": "/grupa/244",
        },
    )
    assert "orar_tutoriate" in client.cookies
    assert f'value="{t.id}" checked' in client.get("/grupa/244").text


def test_alegerea_cu_cont_se_tine_in_cont(client, s, sso_fals):
    t = _tutoriat(s, data_sfarsit=date.today() + timedelta(weeks=8))
    student = _cont(s, "Student", "student@s.unibuc.ro", grupa="244")
    intra_sso(client, sso_fals, email=student.email)
    client.post(
        "/tutoriate/alegere",
        data={"csrf": _csrf(client), "oferite": [str(t.id)], "alese": [str(t.id)]},
    )
    s.refresh(student)
    assert student.tutoriate_alese == f"[{t.id}]"
    assert [x.id for x in oferite_grupei(s, student.grupa)] == [t.id]


# ------------------------------------------------------------------ importul


def test_importul_foii(s):
    orar = s.scalar(select(Perioada))
    ana = _cont(s, "Ana Pop", "ana.pop@s.unibuc.ro")
    raport = importa_foaia(
        s, FOAIE, orar=orar, de_la=LUNI, pana_la=LUNI + timedelta(weeks=12), de_catre="test"
    )
    s.commit()
    tutoriate = list(s.scalars(select(Tutoriat).order_by(Tutoriat.id)))
    assert [t.materie for t in tutoriate] == [
        "Geometrie I",
        "Algebră I",
        "Programare orientată pe obiecte",
    ]
    geo, alg, poo = tutoriate
    assert (geo.zi_saptamana, geo.interval, geo.sala.nume) == ("Marti", "16-18", "sala 220")
    assert geo.specializari == "MATE" and geo.ani == "L1"
    assert poo.specializari == "INFO" and poo.ani == "L2"
    assert alg.sala.nume == "L.321" and alg.ora_inceput == time(8)
    assert all(t.stare == "ciorna" for t in tutoriate)
    assert geo.link_whatsapp == "https://tinyurl.com/wa-geo"
    # Ana are cont: e in echipa; ceilalti au invitatii pe email
    assert [m.user_id for m in geo.echipa] == [ana.id]
    invitatii = {t.email for t in s.scalars(select(Tutor).where(Tutor.user_id.is_(None)))}
    assert invitatii == {"dan.ionescu@s.unibuc.ro", "ioana.radu@s.unibuc.ro"}
    assert any("sărit" in r and "Ceva greșit" in r for r in raport)
    # sala 220 nu exista in orar: a fost adaugata ca sala pentru tutoriate, o singura data
    assert s.scalar(select(Sala).where(Sala.slug == "sala-220")).doar_tutoriate


def test_exportul_are_formatul_foii(client, s):
    tutor = _cont(s, "Ana Pop", "ana@s.unibuc.ro")
    t = _tutoriat(s, ani="L2", specializari="INFO", link_whatsapp="https://wa.me/x")
    t.echipa = [TutoriatTutor(user=tutor, principal=True)]
    s.commit()
    _intra(client)
    r = client.get("/tutoriate/panou/export.csv")
    assert r.status_code == 200
    randuri = r.text.splitlines()
    assert "Materia,Nume,Adresa,Interval,Sala,Link WhatsApp,Link Teams" in randuri
    assert any(
        x.startswith("Programare orientată pe obiecte,Ana Pop,ana@s.unibuc.ro,Vineri 18 - 20")
        for x in randuri
    )
