"""Incarcarea setului golden si consolidarea activitatilor de serie."""

from __future__ import annotations

from datetime import time

from sqlalchemy import func, select

from orar.db.models import Grupa, Ora, Sala
from orar.db.queries import gaseste_grupa, gaseste_sala, ids_stramosi, ore_pentru_grupa

DAYS = ("Luni", "Marti", "Miercuri", "Joi", "Vineri")


def _activitati_in_golden(pagini) -> int:
    return sum(len(p.get(z, [])) for p in pagini for z in DAYS)


def test_toate_activitatile_ajung_in_baza(sesiune_neconsolidata, pagini_golden):
    """Inainte de consolidare, numarul de randuri trebuie sa fie exact cel din sursa."""
    n = sesiune_neconsolidata.scalar(select(func.count()).select_from(Ora))
    assert n == _activitati_in_golden(pagini_golden) == 1143


def test_nomenclatoarele_sunt_deduplicate(db):
    """Un profesor apare pe zeci de pagini, dar trebuie sa aiba un singur rand."""
    for model, coloana in ((Sala, Sala.nume), (Grupa, Grupa.slug)):
        total = db.scalar(select(func.count()).select_from(model))
        distincte = db.scalar(select(func.count(func.distinct(coloana))).select_from(model))
        assert total == distincte, f"{model.__name__} are duplicate"


def test_ierarhia_se_construieste_complet(db):
    g = gaseste_grupa(db, "244")
    assert g is not None and g.tip == "grupa"

    lant = []
    cur = g.parinte
    while cur:
        lant.append((cur.nume, cur.tip))
        cur = cur.parinte
    assert lant == [("Seria 24", "serie"), ("Informatică - anul 2", "specializare")]


def test_semigrupele_devin_copii_ai_grupei(db):
    g = gaseste_grupa(db, "244")
    copii = sorted(c.nume for c in g.copii)
    assert copii == ["244/1", "244/2"]
    assert all(c.tip == "semigrupa" for c in g.copii)


def test_grupa_vede_orele_de_serie(db):
    """Cerinta centrala: o ora alocata seriei e vizibila tuturor grupelor copil."""
    g = gaseste_grupa(db, "244")
    ore = ore_pentru_grupa(db, g.id)
    stramosi = ids_stramosi(db, g.id) - {g.id}

    mostenite = [o for o in ore if o.grupa_id in stramosi]
    assert mostenite, "grupa nu mosteneste nicio ora de la serie/an"
    assert any(o.grupa.tip == "serie" for o in mostenite)


def test_toate_grupele_dintr_o_serie_vad_acelasi_curs(db):
    """Cursul de serie trebuie sa apara identic la 241, 242, 243 si 244."""
    seturi = []
    for nume in ("241", "242", "243", "244"):
        g = gaseste_grupa(db, nume)
        ore = ore_pentru_grupa(db, g.id)
        seturi.append(
            {
                (o.materie.nume, o.zi_saptamana, o.ora_inceput)
                for o in ore
                if o.grupa.tip == "serie" and o.materie
            }
        )
    assert seturi[0] and all(s == seturi[0] for s in seturi)


def test_consolidarea_elimina_duplicatele_de_curs(db, sesiune_neconsolidata):
    inainte = sesiune_neconsolidata.scalar(select(func.count()).select_from(Ora))
    dupa = db.scalar(select(func.count()).select_from(Ora))
    assert dupa < inainte
    # 1143 -> 836. Numarul a scazut cu unu fata de masuratoarea initiala dupa ce am corectat
    # in referinta grafia `ProgrAvObJava` (aparea si `ProgrAvObjJava`, si `ProgrAvObjava` --
    # vezi docs/formatul-orarului.md §9b): erau doua nume pentru aceeasi materie, deci un
    # curs de serie ramasese nedublat si parea ca ocupa sala de doua ori.
    assert dupa == 836


def test_cursul_de_serie_ocupa_sala_o_singura_data(db):
    """Fara consolidare, un curs de serie parea ca ocupa sala de 4 ori simultan."""
    sala = gaseste_sala(db, "Amf.501")
    ore = list(
        db.execute(
            select(Ora).where(
                Ora.sala_id == sala.id,
                Ora.zi_saptamana == "Marti",
                Ora.ora_inceput == time(14),
            )
        ).scalars()
    )
    assert len(ore) == 1
    assert ore[0].grupa.tip == "serie"


def test_orele_partajate_sunt_legate_prin_junction(db):
    """Optionalele nu au parinte comun cu grupele -- ajung prin ORA_GRUPA."""
    g = gaseste_grupa(db, "244")
    ore = ore_pentru_grupa(db, g.id)
    partajate = [o for o in ore if o.grupa.tip == "optional"]
    assert partajate, "grupa nu vede niciun optional/facultativ"


def test_intervalele_orare_sunt_valide(db):
    invalide = list(db.execute(select(Ora).where(Ora.ora_inceput >= Ora.ora_sfarsit)).scalars())
    assert invalide == []


def test_salile_sunt_clasificate(db):
    tipuri = {t for (t,) in db.execute(select(Sala.tip).distinct())}
    assert tipuri <= {"fizica", "externa", "virtuala"}
    assert db.scalar(select(func.count()).select_from(Sala).where(Sala.tip == "fizica")) == 31


def test_pagina_cu_titlu_necunoscut_nu_pierde_activitati(db):
    """ "Conferinte si Seminarii" nu se incadreaza in ierarhie, dar ocupa sali reale."""
    g = db.scalar(select(Grupa).where(Grupa.nume == "Conferinte si Seminarii"))
    assert g is not None
    assert db.scalar(select(func.count()).select_from(Ora).where(Ora.grupa_id == g.id)) > 0
