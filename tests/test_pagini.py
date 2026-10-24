"""Paginile de politici si aducerea ultimului articol ASMI."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from orar.db.articole import _parseaza, prospateaza_articole, ultimul_articol
from orar.db.models import ArticolAsmi


@pytest.fixture
def curat(db):
    yield
    db.rollback()
    db.execute(delete(ArticolAsmi))
    db.commit()


def test_pagina_confidentialitate(client):
    r = client.get("/confidentialitate")
    assert r.status_code == 200
    assert "prioritate absolută" in r.text
    assert "făcut de studenți" in r.text


def test_pagina_cookies(client):
    assert client.get("/cookies").status_code == 200


def test_pagina_date_personale(client):
    assert client.get("/date-personale").status_code == 200


def test_parseaza_primul_articol():
    html = '<a href="/Articole/inscriere-si-taxe-2026">Înscrierea în an și plata taxelor</a>'
    assert _parseaza(html) == ("inscriere-si-taxe-2026", "Înscrierea în an și plata taxelor")


def test_parseaza_sare_paginarea():
    html = '<a href="/Articole?currentPage=1">1</a><a href="/Articole/burse-ppc-2026">Burse PPC</a>'
    assert _parseaza(html) == ("burse-ppc-2026", "Burse PPC")


def test_prospateaza_scrie_si_citeste(db, monkeypatch, curat):
    class Raspuns:
        text = '<a href="/Articole/asr2026">ASR 2026</a>'

        def raise_for_status(self):
            pass

    monkeypatch.setattr("httpx.get", lambda *a, **k: Raspuns())
    assert prospateaza_articole(db).slug == "asr2026"
    assert ultimul_articol(db).slug == "asr2026"
