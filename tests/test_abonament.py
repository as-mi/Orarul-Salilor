"""Abonamentul la calendarul ICS: feed-ul pe semestru, tokenurile si permisiunile.

Baza e cea din golden, cu o structura de semestru pusa de mana (13 saptamani si o vacanta),
ca testele sa nu depinda de anul in care ruleaza. Fiecare test porneste de la aceeasi stare:
fixtura de curatenie goleste doar tabelele pe care le scriu testele, nu orarul.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.orm import sessionmaker

from orar.db.abonamente import PreferinteAbonament
from orar.db.models import (
    AbonamentCalendar,
    ActivitateImportata,
    Base,
    Eveniment,
    EvenimentPersonal,
    Grupa,
    PerioadaStructura,
    Permisiune,
    Profesor,
    Sala,
    SsoSesiune,
    Tutoriat,
    TutoriatAnulare,
    TutoriatTutor,
    User,
    VersiuneOrar,
)
from orar.db.queries import gaseste_grupa, gaseste_profesor
from orar.web.feed import intrari_grupa
from orar.web.feed import zile_semestru as zilele
from tests.conftest import _engine_memorie, intra_sso

AN, SEM = "2025-2026", 2
SLUG = "244"
PROFESOR = "Popescu S"
#: Semestrul de test: 13 saptamani didactice, cu o vacanta de primavara la mijloc.
INCEPUT = date(2026, 2, 23)
SFARSIT = date(2026, 5, 29)
VACANTA = (date(2026, 4, 13), date(2026, 4, 17))
MIJLOC_VACANTA = date(2026, 4, 15)
#: Lunele saptamanilor academice 1..13 (saptamana vacantei nu primeste numar).
LUNI = [
    date(2026, 2, 23),
    date(2026, 3, 2),
    date(2026, 3, 9),
    date(2026, 3, 16),
    date(2026, 3, 23),
    date(2026, 3, 30),
    date(2026, 4, 6),
    date(2026, 4, 20),
    date(2026, 4, 27),
    date(2026, 5, 4),
    date(2026, 5, 11),
    date(2026, 5, 18),
    date(2026, 5, 25),
]
#: Lunele saptamanilor impare / pare.
IMPARI = [LUNI[i] for i in (0, 2, 4, 6, 8, 10, 12)]
PARI = [LUNI[i] for i in (1, 3, 5, 7, 9, 11)]


# ------------------------------------------------------------------ baza de test


@pytest.fixture(scope="module")
def _engine(pagini_golden):
    engine = _engine_memorie()

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Sesiune = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with Sesiune() as sesiune:
        from orar.ingest.consolidate import consolideaza
        from orar.ingest.load import incarca_pagini

        incarca_pagini(sesiune, pagini_golden)
        consolideaza(sesiune, an_universitar=AN)
        sesiune.add_all(
            [
                PerioadaStructura(
                    an_univ=AN,
                    fel="didactica",
                    semestru=SEM,
                    data_inceput=INCEPUT,
                    data_sfarsit=date(2026, 4, 10),
                ),
                PerioadaStructura(
                    an_univ=AN,
                    fel="vacanta",
                    nume="Vacanța de primăvară",
                    data_inceput=VACANTA[0],
                    data_sfarsit=VACANTA[1],
                ),
                PerioadaStructura(
                    an_univ=AN,
                    fel="didactica",
                    semestru=SEM,
                    data_inceput=date(2026, 4, 20),
                    data_sfarsit=SFARSIT,
                ),
            ]
        )
        sesiune.commit()
    return engine


def _goleste(s) -> None:  # noqa: ANN001
    """Tabelele pe care le scriu testele; orarul si structura raman."""
    for model in (
        AbonamentCalendar,
        TutoriatAnulare,
        TutoriatTutor,
        Tutoriat,
        Eveniment,
        EvenimentPersonal,
        ActivitateImportata,
        Permisiune,
        SsoSesiune,
        User,
    ):
        s.execute(delete(model))
    s.commit()


@pytest.fixture
def s(_engine):
    Sesiune = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    with Sesiune() as sesiune:
        _goleste(sesiune)
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


# ------------------------------------------------------------------ ajutoare


def _csrf(c, cale: str | None = None) -> str:  # noqa: ANN001
    """Tokenul CSRF din prima pagina care il are: bara (cont), intrarea, contul sau meniu."""
    for pagina in ([cale] if cale else []) + ["/", "/login", "/account", f"/grupa/{SLUG}"]:
        potrivire = re.search(r'name="csrf" value="([^"]+)"', c.get(pagina).text)
        if potrivire is not None:
            return potrivire.group(1)
    raise AssertionError("nicio pagina nu are token CSRF")


def _grupa(s, slug: str = SLUG):  # noqa: ANN001, ANN202
    grupa = gaseste_grupa(s, slug)
    assert isinstance(grupa, Grupa)
    return grupa


def _intrari(s, preferinte: PreferinteAbonament | None = None, *, tutor=None):  # noqa: ANN001
    return intrari_grupa(
        s,
        _grupa(s),
        an_univ=AN,
        semestru=SEM,
        preferinte=preferinte or PreferinteAbonament(),
        domeniu="test",
        tutor=tutor,
    )


def _zile(intrari, prefix: str) -> set[date]:  # noqa: ANN001
    """Zilele (din UID) intrarilor care incep cu `ora-<id>-` sau `eveniment-<id>-`."""
    rezultat = set()
    for intrare in intrari:
        if not intrare.uid.startswith(prefix):
            continue
        ymd = intrare.uid.split("-")[2].split("@")[0]
        rezultat.add(date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])))
    return rezultat


def _token_din(raspuns) -> str:  # noqa: ANN001
    assert raspuns.status_code == 303, raspuns.text[:300]
    potrivire = re.search(r"/calendar/([^/]+)/", raspuns.headers["location"])
    assert potrivire is not None, raspuns.headers["location"]
    return potrivire.group(1)


def _creeaza(c, *, inapoi: str = f"/grupa/{SLUG}", target: str = "ajutor", fel: str = "grupa", csrf=None):  # noqa: ANN001
    return c.post(
        "/calendar/abonament",
        data={
            "csrf": csrf if csrf is not None else _csrf(c),
            "fel": fel,
            "inapoi": inapoi,
            "target": target,
        },
        follow_redirects=False,
    )


def _cont(client, s, sso, email: str = "ana@example.com", *, grupa: bool = True):  # noqa: ANN001
    r = intra_sso(client, sso, email=email, nume="Ana Pop")
    assert r.status_code == 303
    if grupa:
        r = client.post(
            "/account",
            data={"csrf": _csrf(client, "/account"), "grupa_id": str(_grupa(s).id)},
            follow_redirects=False,
        )
        assert r.status_code == 303
    return s.scalar(select(User).where(User.email == email))


def _tutoriate() -> tuple[Tutoriat, Tutoriat]:
    """Doua tutoriate publicate pe Luni 16-18, pentru grupa 244."""
    acum = datetime(2026, 2, 1, 12, 0)

    def fa(materie: str) -> Tutoriat:
        return Tutoriat(
            an_univ=AN,
            semestru=SEM,
            materie=materie,
            zi_saptamana="Luni",
            ora_inceput=time(16),
            ora_sfarsit=time(18),
            data_inceput=INCEPUT,
            data_sfarsit=SFARSIT,
            specializari="INFO",
            ani="L2",
            stare="publicat",
            creat_de="test",
            creat_la=acum,
        )

    return fa("Analiză matematică"), fa("Fizică")


def _eveniment(s, **date_):  # noqa: ANN001, ANN003
    valorile = {
        "fel": "alta",
        "titlu": "Eveniment de test",
        "data_inceput": date(2026, 3, 6),
        "data_sfarsit": date(2026, 3, 6),
        "vizibilitate": "toate",
        "creat_de": "test",
        "creat_la": datetime(2026, 2, 1, 12, 0),
    }
    valorile.update(date_)
    e = Eveniment(**valorile)
    s.add(e)
    return e


# ------------------------------------------------------------------ zilele si orele


def test_zilele_semestrului_vin_din_structura(s):
    zile, cal = zilele(s, AN, SEM)
    assert len(zile) == 65  # 13 saptamani x 5 zile
    assert zile[0].zi == INCEPUT and zile[-1].zi == SFARSIT
    assert [z.saptamana.numar for z in zile][:5] == [1] * 5
    assert all(z.zi.weekday() < 5 for z in zile)
    assert not [z for z in zile if VACANTA[0] <= z.zi <= VACANTA[1]]
    assert cal.saptamana(date(2026, 4, 20)).numar == 8


def test_fiecare_ora_devine_un_eveniment_cu_ora_romaniei(s):
    intrari = _intrari(s)
    iarna = next(i for i in intrari if i.uid == "ora-932-20260223@test")
    vara = next(i for i in intrari if i.uid == "ora-932-20260420@test")
    assert (iarna.inceput.hour, iarna.inceput.minute) == (16, 0)
    assert (vara.inceput.hour, vara.inceput.minute) == (16, 0)
    assert iarna.titlu.startswith("FunLbProgr")
    assert iarna.loc == "L.511"
    assert iarna.descriere  # profesorul, grupa sau semigrupa


def test_paritatea_saptamanilor(s):
    intrari = _intrari(s)
    assert _zile(intrari, "ora-933-") == set(IMPARI)  # SI
    assert _zile(intrari, "ora-930-") == set(PARI)  # SP


def test_intervalul_de_saptamani_se_respecta(s):
    intrari = _intrari(s)
    marti_impare = {zi + timedelta(days=1) for zi in IMPARI}

    # 939: Marti 18-20, SI, sapt 1-7.
    assert _zile(intrari, "ora-939-") == {zi for zi in marti_impare if zi <= date(2026, 4, 10)}

    miercuri_impare = {zi + timedelta(days=2) for zi in IMPARI}
    # 945: Miercuri 16-18, SI, sapt 8-14; semestrul de test are doar 13 saptamani.
    assert _zile(intrari, "ora-945-") == {zi for zi in miercuri_impare if zi > date(2026, 4, 10)}


def test_vacanta_nu_are_ore(s):
    for intrare in _intrari(s):
        if intrare.uid.startswith("ora-"):
            assert not VACANTA[0] <= intrare.inceput.date() <= VACANTA[1], intrare


def test_semigrupa_filtreaza_activitatile(s):
    gr_1 = _intrari(s, PreferinteAbonament(semigrupa="Gr_1"))
    gr_2 = _intrari(s, PreferinteAbonament(semigrupa="Gr_2"))
    assert _zile(gr_1, "ora-940-") and not _zile(gr_1, "ora-941-")
    assert _zile(gr_2, "ora-941-") and not _zile(gr_2, "ora-940-")
    assert _zile(gr_1, "ora-929-") and not _zile(gr_1, "ora-934-")
    assert _zile(gr_2, "ora-934-") and not _zile(gr_2, "ora-929-")
    # Activitatea fara semigrupa ramane la ambele.
    assert _zile(gr_1, "ora-932-") and _zile(gr_2, "ora-932-")


def test_materia_ascunsa_dispara_din_feed(s):
    toate = _intrari(s)
    ascunse = _intrari(s, PreferinteAbonament(ascunse=frozenset({"print-model3d"})))
    assert any("Print&Model3D" in i.titlu for i in toate)
    assert not any("Print&Model3D" in i.titlu for i in ascunse)
    # facultativele sunt pachet optional, deci se ascund pe materie
    assert not any(i.uid.startswith("ora-65-") for i in ascunse)


# ------------------------------------------------------------------ tutoriatele


def test_doar_tutoriatele_alese_apar_si_anularile_sunt_marcate(s):
    unul, altul = _tutoriate()
    s.add_all([unul, altul])
    s.commit()
    s.add(
        TutoriatAnulare(
            tutoriat_id=unul.id,
            data=date(2026, 3, 9),
            motiv="Conferință",
            de_catre="test",
        )
    )
    s.commit()

    intrari = _intrari(s, PreferinteAbonament(tutoriate=frozenset({unul.id})))
    tutoriate = [i for i in intrari if i.uid.startswith("tutoriat-")]
    assert tutoriate
    assert all(i.titlu == "Tutoriat: Analiză matematică" for i in tutoriate)
    assert {i.inceput.date() for i in tutoriate} == set(LUNI)

    anulata = next(i for i in tutoriate if i.inceput.date() == date(2026, 3, 9))
    assert anulata.status == "CANCELLED"
    assert "Anulat: Conferință" in anulata.descriere
    assert all(i.status == "CONFIRMED" for i in tutoriate if i is not anulata)


def test_tutorul_isi_vede_tutoriatele_lui_in_feed(s):
    unul, altul = _tutoriate()
    user = User(nume="Tutor Test", email="tutor@example.com", rol="student")
    s.add_all([unul, altul, user])
    s.flush()
    s.add(TutoriatTutor(tutoriat_id=altul.id, user_id=user.id, principal=True))
    s.commit()

    intrari = _intrari(s, PreferinteAbonament(), tutor=user)
    assert any("Fizică" in i.titlu for i in intrari)
    assert not any("Analiză matematică" in i.titlu for i in intrari)


def test_tutoriatele_altei_grupe_nu_apar(s):
    unul, _ = _tutoriate()
    unul.ani = "L3"  # alt an de studiu decat grupa 244
    s.add(unul)
    s.commit()
    intrari = _intrari(s, PreferinteAbonament(tutoriate=frozenset({unul.id})))
    assert not [i for i in intrari if i.uid.startswith("tutoriat-")]


# ------------------------------------------------------------------ evenimentele


def test_evenimentele_apar_o_data_pe_fiecare_zi_si_respecta_vizibilitatea(s):
    _eveniment(
        s,
        titlu="Ziua Porților Deschise",
        data_inceput=date(2026, 3, 6),
        data_sfarsit=date(2026, 3, 7),
    )
    _eveniment(
        s,
        titlu="ASMI Q&A",
        data_inceput=MIJLOC_VACANTA,
        data_sfarsit=MIJLOC_VACANTA,
        ora_inceput=time(18),
        ora_sfarsit=time(20),
    )
    _eveniment(s, titlu="Doar sala", vizibilitate="sala", ora_inceput=time(10))
    _eveniment(
        s, titlu="INFO an 2", vizibilitate="specializari", specializari="INFO", ani="L2"
    )
    _eveniment(
        s, titlu="CTI an 2", vizibilitate="specializari", specializari="CTI", ani="L2"
    )
    _eveniment(s, titlu="Fara sfarsit", data_inceput=date(2026, 3, 4), ora_inceput=time(10))
    s.commit()

    intrari = _intrari(s)
    titluri = {i.titlu for i in intrari}
    assert {"Ziua Porților Deschise", "ASMI Q&A", "INFO an 2", "Fara sfarsit"} <= titluri
    assert "Doar sala" not in titluri
    assert "CTI an 2" not in titluri
    # Evenimentul din vacanta ramane: e o intamplare reala, nu o ora.
    assert any(i.titlu == "ASMI Q&A" and i.inceput.date() == MIJLOC_VACANTA for i in intrari)

    ziua = [i for i in intrari if i.titlu == "Ziua Porților Deschise"]
    assert {i.inceput for i in ziua} == {date(2026, 3, 6), date(2026, 3, 7)}
    assert all(i.toata_ziua for i in ziua)


def test_evenimentul_fara_ora_de_sfarsit_dureaza_o_ora(s):
    _eveniment(s, titlu="Fara sfarsit", data_inceput=date(2026, 3, 4), ora_inceput=time(10))
    s.commit()
    intrare = next(i for i in _intrari(s) if i.titlu == "Fara sfarsit")
    assert (intrare.inceput.hour, intrare.sfarsit.hour) == (10, 11)


# ------------------------------------------------------------------ tokenuri si rute


def test_feed_ul_are_antetele_potrivite(client):
    token = _token_din(_creeaza(client))
    r = client.get(f"/calendar/{token}.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert r.headers["cache-control"] == "private, max-age=300"
    assert r.headers["x-robots-tag"] == "noindex, nofollow"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert f'filename="{SLUG}.ics"' in r.headers["content-disposition"]
    assert r.content.startswith(b"BEGIN:VCALENDAR\r\n")
    assert r.content.endswith(b"END:VCALENDAR\r\n")


def test_acelasi_vizitator_primeste_acelasi_token(client, s):
    primul = _token_din(_creeaza(client))
    al_doilea = _token_din(_creeaza(client))
    assert primul == al_doilea
    assert s.scalar(select(AbonamentCalendar).where(AbonamentCalendar.token == primul))


def test_alt_vizitator_primeste_alt_token(client, s):
    primul = _token_din(_creeaza(client))
    client.cookies.clear()
    al_doilea = _token_din(_creeaza(client))
    assert primul != al_doilea
    assert len(list(s.scalars(select(AbonamentCalendar)))) == 2


def test_token_necunoscut_si_token_revocat(client):
    r = client.get("/calendar/inexistent.ics")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/plain")
    assert "inexistent" in r.text

    token = _token_din(_creeaza(client))
    csrf = re.search(
        r'name="csrf" value="([^"]+)"', client.get(f"/calendar/{token}/ajutor").text
    ).group(1)
    r = client.post(
        f"/calendar/{token}/revoca",
        data={"csrf": csrf, "inapoi": f"/grupa/{SLUG}"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = client.get(f"/calendar/{token}.ics")
    assert r.status_code == 410
    assert r.text == "Calendar revocat.\n"
    assert client.get(f"/calendar/{token}/ajutor").status_code == 410


def test_fara_csrf_nu_se_creeaza_nimic(client, s):
    assert _creeaza(client, csrf="gresit").status_code == 400
    assert s.scalar(select(AbonamentCalendar)) is None


def test_adresa_de_intoarcere_este_validata(client, s):
    assert _creeaza(client, inapoi="//evil.com").status_code == 400
    assert _creeaza(client, inapoi="/sala/amf-501").status_code == 400
    assert _creeaza(client, inapoi=f"/grupa/{SLUG}\\x").status_code == 400
    assert s.scalar(select(AbonamentCalendar)) is None

    assert _creeaza(client, inapoi=f"/grupa/{SLUG}?x=1").status_code == 303


def test_meniul_de_export_are_intrarile_de_calendar(client, s):
    pagina = client.get(f"/grupa/{SLUG}").text
    assert "Adaugă în Google Calendar" in pagina
    assert "Abonează-te pe iPhone / iPad" in pagina
    assert "Abonează-te în calendar…" in pagina
    assert 'action="/calendar/abonament"' in pagina

    sala = s.scalar(select(Sala).where(Sala.nume == "Amf.501"))
    pagina_sala = client.get(f"/sala/{sala.slug}").text
    assert "Exportă" in pagina_sala
    assert "Google Calendar" not in pagina_sala

    versiune = VersiuneOrar(
        an_univ=AN, semestru=SEM, arhivat_la=datetime(2026, 6, 1, 12, 0), nr_ore=0
    )
    s.add(versiune)
    s.commit()
    pagina_arhiva = client.get(f"/grupa/{SLUG}?versiune={versiune.id}").text
    assert "Exportă" in pagina_arhiva
    assert "Google Calendar" not in pagina_arhiva


# ------------------------------------------------------------------ cont vs instantaneu


def test_contul_urmeaza_live_semigrupa_din_cont(client, s, sso_fals):
    user = _cont(client, s, sso_fals)
    token = _token_din(_creeaza(client))
    abonament = s.scalar(select(AbonamentCalendar).where(AbonamentCalendar.token == token))
    assert abonament.mod == "cont" and abonament.user_id == user.id

    assert "ora-941-" in client.get(f"/calendar/{token}.ics").text  # Gr_2, SP

    user.semigrupa = "Gr_1"
    s.commit()
    text = client.get(f"/calendar/{token}.ics").text
    assert "ora-940-" in text and "ora-941-" not in text

    user.semigrupa = "Gr_2"
    s.commit()
    text = client.get(f"/calendar/{token}.ics").text
    assert "ora-941-" in text and "ora-940-" not in text

    # Refaceerea abonamentului nu creeaza altul: e acelasi cont si acelasi fel.
    assert _token_din(_creeaza(client)) == token
    assert len(list(s.scalars(select(AbonamentCalendar)))) == 1


def test_anonimul_ramane_pe_snapshot_iar_resubscrierea_il_actualizeaza(client, s):
    client.get(f"/grupa/{SLUG}?semigrupa=Gr_1")
    token = _token_din(_creeaza(client))
    assert "ora-941-" not in client.get(f"/calendar/{token}.ics").text

    # Schimba alegerea pe pagina, fara sa reia abonamentul: feed-ul ramane inghetat.
    client.get(f"/grupa/{SLUG}?semigrupa=Gr_2")
    text = client.get(f"/calendar/{token}.ics").text
    assert "ora-940-" in text and "ora-941-" not in text

    # Re-subscrierea actualizeaza snapshot-ul in acelasi rand, deci acelasi token.
    assert _token_din(_creeaza(client)) == token
    text = client.get(f"/calendar/{token}.ics").text
    assert "ora-941-" in text and "ora-940-" not in text
    assert len(list(s.scalars(select(AbonamentCalendar)))) == 1


def test_feed_ul_nu_contine_date_personale(client, s, sso_fals):
    user = _cont(client, s, sso_fals)
    s.add(
        EvenimentPersonal(
            user_id=user.id,
            titlu="Secret personal",
            zi_saptamana="Luni",
            ora_inceput=time(16),
            ora_sfarsit=time(17),
            creat_la=datetime(2026, 2, 1, 12, 0),
        )
    )
    s.add(
        ActivitateImportata(
            user_id=user.id,
            sursa="Seria 24",
            materie="Import privat",
            zi_saptamana="Luni",
            ora_inceput=time(18),
            ora_sfarsit=time(19),
            creat_la=datetime(2026, 2, 1, 12, 0),
        )
    )
    s.commit()

    token = _token_din(_creeaza(client))
    text = client.get(f"/calendar/{token}.ics").text
    assert "Secret personal" not in text
    assert "Import privat" not in text
    assert "FunLbProgr" in text  # orarul propriu-zis e acolo


# ------------------------------------------------------------------ profesorii


def _profesor(s) -> Profesor:  # noqa: ANN001
    profesor = gaseste_profesor(s, "popescu-s")
    assert profesor is not None and profesor.nume == PROFESOR
    return profesor


def test_anonimul_nu_poate_abona_orarul_unui_profesor(client, s):
    r = _creeaza(client, inapoi=f"/profesor/{_profesor(s).slug}", fel="profesor")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?inapoi=")


def test_alt_cont_nu_poate_abona_orarul_unui_profesor(client, s, sso_fals):
    _cont(client, s, sso_fals, "student@example.com", grupa=False)
    r = _creeaza(client, inapoi=f"/profesor/{_profesor(s).slug}", fel="profesor")
    assert r.status_code == 403
    assert s.scalar(select(AbonamentCalendar)) is None


def test_titularul_isi_aboneaza_orarul_lui_si_permisiunea_se_reverifica(
    client, s, sso_fals
):
    user = _cont(client, s, sso_fals, "prof@example.com", grupa=False)
    user.rol = "profesor"
    user.profesor = PROFESOR
    s.commit()

    token = _token_din(_creeaza(client, inapoi=f"/profesor/{_profesor(s).slug}", fel="profesor"))
    text = client.get(f"/calendar/{token}.ics").text
    assert PROFESOR in text
    assert "Miclaus A" not in text  # doar orele titularului

    # Retragerea rolului invalideaza feed-ul, fara sa fie nevoie de revocare.
    user.rol = "student"
    s.commit()
    assert client.get(f"/calendar/{token}.ics").status_code == 410


def test_permisiunea_orar_profesori_nu_da_dreptul_la_abonament(client, s, sso_fals):
    user = _cont(client, s, sso_fals, "voluntar@example.com", grupa=False)
    s.add(
        Permisiune(
            user_id=user.id,
            cheie="orar_profesori",
            acordat_de="test",
            acordat_la=datetime(2026, 2, 1, 12, 0),
        )
    )
    s.commit()

    profesor = _profesor(s)
    assert client.get(f"/profesor/{profesor.slug}").status_code == 200
    # Pagina se vede, dar optiunea de abonare nu: feed-ul n-ar putea re-verifica permisiunea.
    assert "Google Calendar" not in client.get(f"/profesor/{profesor.slug}").text
    assert (
        _creeaza(client, inapoi=f"/profesor/{profesor.slug}", fel="profesor").status_code == 403
    )


def test_titularul_vede_optiunea_in_meniu(client, s, sso_fals):
    user = _cont(client, s, sso_fals, "prof2@example.com", grupa=False)
    user.rol = "profesor"
    user.profesor = PROFESOR
    s.commit()
    assert "Adaugă în Google Calendar" in client.get(f"/profesor/{_profesor(s).slug}").text
