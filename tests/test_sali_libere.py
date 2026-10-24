"""Tabul „Fereastra": sali libere publice si blocarea salilor de catre admin."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from orar.db.models import Sala


@pytest.fixture
def curat(db):
    yield
    db.rollback()
    for sala in db.scalars(select(Sala)):
        sala.blocata = False
    db.commit()


def _blocheaza_toate(db):
    for sala in db.scalars(select(Sala).where(Sala.tip == "fizica")):
        sala.blocata = True
    db.commit()


def test_fereastra_fara_parametri(client):
    r = client.get("/sali-libere")
    assert r.status_code == 200
    assert "Alege data" in r.text


def test_fereastra_cu_interval(client, curat):
    r = client.get("/sali-libere?data=2026-01-05&ora_inceput=10:00&ora_sfarsit=12:00")
    assert r.status_code == 200
    assert ("/sala/" in r.text) or ("Nicio sală" in r.text)


def test_fereastra_exclude_sali_blocate(client, db, curat):
    _blocheaza_toate(db)
    r = client.get("/sali-libere?data=2026-01-05&ora_inceput=10:00&ora_sfarsit=12:00")
    assert "/sala/" not in r.text


def test_admin_salveaza_blocare(client, db, sso_fals, curat):
    from tests.conftest import intra_sso

    intra_sso(client, sso_fals, email="admin@test.com", realm=("cddd",))
    sala = db.scalar(select(Sala).where(Sala.tip == "fizica").limit(1))
    assert sala is not None
    csrf = re.search(r'name="csrf" value="([^"]+)"', client.get("/admin").text).group(1)
    r = client.post(
        "/admin/sali", data={"csrf": csrf, "blocata": [str(sala.id)]}, follow_redirects=False
    )
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Sala, sala.id).blocata is True
