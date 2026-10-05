"""Validarea incrucisata cu orarul profesorilor.

Testele nu citesc imagini: construiesc direct indexul pe care l-ar produce citirea celor 191
de pagini. Partea care poate sa se strice tacit e **ce facem cu diferentele** -- si aceea se
probeaza mai bine cu date puse la mana decat cu 12 minute de OCR.
"""

from __future__ import annotations

from collections import defaultdict

import pytest
from sqlalchemy import select

from orar.db.models import Ora, Profesor
from orar.ingest.crosscheck import (
    IndexProfesori,
    completeaza_profesorii,
    extinde_numele,
    verifica,
)


@pytest.fixture(autouse=True)
def curata(db):
    """Fiecare test isi anuleaza modificarile.

    `db` e o sesiune comuna intre teste, iar functiile verificate aici *scriu* (redenumesc
    profesori, muta ore). Fara rollback, al doilea test ar porni de la ce a lasat primul si
    ar verifica altceva decat crede.
    """
    yield
    db.rollback()


@pytest.fixture
def ora(db):
    """O activitate reala din baza, cu profesor, sala si materie."""
    o = db.scalar(
        select(Ora).where(
            Ora.profesor_id.is_not(None), Ora.sala_id.is_not(None), Ora.materie_id.is_not(None)
        )
    )
    assert o is not None
    return o


def _nume_intreg(prescurtat: str) -> str:
    """Un nume intreg plauzibil pentru o prescurtare din baza: `Stamate D` -> `Stamate Dxxx`.

    Prenumele trebuie sa inceapa chiar cu initiala din prescurtare -- altfel construim o
    pereche care nu se potriveste si testam altceva decat credem.
    """
    parti = prescurtat.split()
    familie = parti[0]
    initiala = parti[1][0] if len(parti) > 1 else "A"
    return f"{familie} {initiala}lexandru-Cristian"


def _index_pentru(o: Ora, nume: set[str]) -> IndexProfesori:
    """Index care contine exact slotul activitatii date, atribuit numelor cerute."""
    from orar.ingest.crosscheck import _cheie_ora

    idx = IndexProfesori(nume=sorted(nume), pagini=len(nume))
    idx.pe_slot = defaultdict(set, {_cheie_ora(o): set(nume)})
    return idx


def test_slotul_gasit_cu_acelasi_profesor_e_confirmat(db, ora):
    intreg = _nume_intreg(ora.profesor.nume)
    rap = verifica(db, _index_pentru(ora, {intreg}))
    assert rap.confirmate == 1
    assert rap.profesor_confirmat == 1
    assert rap.divergente == []


def test_slotul_gasit_cu_alt_profesor_e_divergenta(db, ora):
    rap = verifica(db, _index_pentru(ora, {"Cutareanu Cutarel"}))
    assert rap.confirmate == 1
    assert rap.profesor_confirmat == 0
    assert len(rap.divergente) == 1


def test_ce_nu_apare_in_a_doua_sursa_e_neconfirmat(db):
    rap = verifica(db, IndexProfesori())
    total = db.scalar(select(Ora.id).limit(1))
    assert total is not None
    assert rap.confirmate == 0
    assert rap.neconfirmate, "toate activitatile trebuie sa iasa neconfirmate"
    assert rap.acoperire == 0.0


def test_numele_din_baza_nu_apar_ca_noi(db, ora):
    """`Alexe B` in baza si `Alexe Bogdan` in titlu sunt acelasi om, nu doi."""
    rap = verifica(db, _index_pentru(ora, {_nume_intreg(ora.profesor.nume)}))
    assert rap.nume_noi == []


def test_completarea_atinge_doar_campurile_marcate_nesigure(db, ora):
    """Un profesor citit cu incredere nu se rescrie, nici daca a doua sursa zice altceva."""
    vechi = ora.profesor.nume
    ora.campuri_nesigure = None
    db.flush()
    assert completeaza_profesorii(db, _index_pentru(ora, {"Cutareanu Cutarel"})) == []
    db.refresh(ora)
    assert ora.profesor.nume == vechi


def test_completarea_umple_ce_am_marcat_nesigur(db, ora):
    ora.campuri_nesigure = "profesor"
    db.flush()
    schimbate = completeaza_profesorii(db, _index_pentru(ora, {"Cutareanu Cutarel"}))
    assert len(schimbate) == 1
    assert ora.profesor.nume == "Cutareanu Cutarel"
    assert ora.campuri_nesigure is None


def test_activitatea_cu_mai_multi_profesori_ii_ia_pe_toti(db, ora):
    """Ora tinuta in doi apare pe pagina fiecaruia, deci slotul intoarce doua nume."""
    ora.campuri_nesigure = "profesor"
    db.flush()
    completeaza_profesorii(db, _index_pentru(ora, {"Bbb Bogdan", "Aaa Ana"}))
    assert ora.profesor.nume == "Aaa Ana / Bbb Bogdan"


def test_extinderea_inlocuieste_prescurtarea(db, ora):
    scurt = ora.profesor.nume
    intreg = _nume_intreg(scurt)
    db.flush()

    extinse, ambigue = extinde_numele(db, _index_pentru(ora, {intreg}))
    assert ambigue == []
    assert any(intreg in x for x in extinse)
    assert db.scalar(select(Profesor).where(Profesor.nume == intreg)) is not None


def test_extinderea_nu_alege_intre_doi_omonimi(db, ora):
    """`Popescu A` se potriveste si cu Adrian, si cu Ana: prescurtarea chiar nu-i distinge."""
    ora.profesor.nume = "Popescu A"
    db.flush()
    idx = _index_pentru(ora, {"Popescu Adrian", "Popescu Ana"})
    extinse, ambigue = extinde_numele(db, idx)
    assert extinse == []
    assert ambigue and "Popescu A" in ambigue[0]
    assert ora.profesor.nume == "Popescu A", "ramane cum era"


def test_extinderea_nu_atinge_ce_nu_are_corespondent(db, ora):
    vechi = ora.profesor.nume
    extinse, _ = extinde_numele(db, _index_pentru(ora, {"Nimeni Nimeni"}))
    assert extinse == []
    assert ora.profesor.nume == vechi


def test_acelasi_profesor_scris_altfel_nu_strica_ingestul():
    """Un nume care difera doar printr-un caracter (aici o cratima neobisnuita, U+2011) da
    acelasi slug: e acelasi rand, nu unul nou care ar lovi unicitatea slug-ului."""
    from sqlalchemy.orm import sessionmaker

    from orar.db.models import Base
    from orar.ingest.load import _Cache, profesor_dupa_nume
    from tests.conftest import _engine_memorie

    engine = _engine_memorie()
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        cache = _Cache(s, "2025-2026")  # cache-ul nu stie de randul adaugat dupa el
        existent = profesor_dupa_nume(s, "Sterian Mara-Cristina")
        assert cache.profesor("Sterian Mara‑Cristina") is existent
        # doua persoane cu acelasi slug primesc slug-uri diferite, fara eroare
        s.add(Profesor(nume="Popa A", slug="popa-a"))
        s.flush()
        assert profesor_dupa_nume(s, "Popa A").slug == "popa-a"


def test_numele_intreg_se_uneste_si_cu_versiunile_anterioare():
    """`Alexe B` devine `Alexe Bogdan`, care exista deja ca rand. Daca orarul vechi, arhivat
    la reincarcare, inca trimite la `Alexe B`, unirea trebuie sa-l mute si pe el -- altfel
    stergerea randului vechi pica pe cheia straina si cade tot ingestul."""
    from datetime import datetime, time

    from sqlalchemy import event, select
    from sqlalchemy.orm import sessionmaker

    from orar.db.models import Base, Grupa, OraArhivata, Perioada, VersiuneOrar
    from orar.ingest.crosscheck import IndexProfesori, extinde_numele
    from tests.conftest import _engine_memorie

    engine = _engine_memorie()

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        scurt = Profesor(nume="Alexe B", slug="alexe-b")
        intreg = Profesor(nume="Alexe Bogdan", slug="alexe-bogdan", din_orar=True)
        g = Grupa(nume="407", slug="407", tip="grupa", an_universitar="2025-2026")
        perioada = Perioada(an_univ="2025-2026", semestru=2)
        v = VersiuneOrar(an_univ="2025-2026", semestru=2, arhivat_la=datetime(2026, 4, 1), nr_ore=1)
        s.add_all([scurt, intreg, g, perioada, v])
        s.flush()
        s.add(
            Ora(
                grupa=g,
                perioada=perioada,
                profesor=scurt,
                zi_saptamana="Luni",
                ora_inceput=time(8),
                ora_sfarsit=time(10),
            )
        )
        s.add(
            OraArhivata(
                versiune_id=v.id,
                ora_originala=1,
                perioada_id=perioada.id,
                grupa_id=g.id,
                profesor_id=scurt.id,
                zi_saptamana="Luni",
                ora_inceput=time(8),
                ora_sfarsit=time(10),
            )
        )
        s.flush()

        extinse, _ = extinde_numele(s, IndexProfesori(nume=["Alexe Bogdan"]))
        s.commit()
        assert extinse
        assert s.scalar(select(Profesor).where(Profesor.nume == "Alexe B")) is None
        assert {o.profesor_id for o in s.scalars(select(Ora))} == {intreg.id}
        assert {o.profesor_id for o in s.scalars(select(OraArhivata))} == {intreg.id}
