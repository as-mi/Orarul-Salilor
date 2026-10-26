"""Exportul orarului ca imagine: rutele `?export=png|jpg` si rendererul Pillow."""

from __future__ import annotations

import io

from PIL import Image
from sqlalchemy import select

from orar.db.models import Profesor, Sala
from orar.domain.grid import construieste_grila
from orar.web.export import bytes_imagine, imagine_grila
from tests.conftest import intra_sso

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def test_export_grupa_png(client):
    r = client.get("/grupa/244?export=png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content.startswith(PNG_MAGIC)
    assert "attachment" in r.headers.get("content-disposition", "")


def test_export_grupa_jpg(client):
    r = client.get("/grupa/244?export=jpg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    assert r.content.startswith(JPEG_MAGIC)


def test_export_sala_png(client, db):
    sala = db.scalar(select(Sala).where(Sala.tip == "fizica").limit(1))
    assert sala is not None
    r = client.get(f"/sala/{sala.slug}?export=png")
    assert r.status_code == 200
    assert r.content.startswith(PNG_MAGIC)


def test_export_profesor_png(client, db, sso_fals):
    # orarul profesorilor cere acum autentificare; adminul are acces implicit
    intra_sso(client, sso_fals, email="sef@example.com", realm=("cddd",))
    prof = db.scalar(select(Profesor).limit(1))
    assert prof is not None
    r = client.get(f"/profesor/{prof.slug}?export=png")
    assert r.status_code == 200
    assert r.content.startswith(PNG_MAGIC)


def test_export_pastreaza_filtrele(client):
    r = client.get("/grupa/244?export=png&semigrupa=Gr_1&saptamana=true")
    assert r.status_code == 200
    assert r.content.startswith(PNG_MAGIC)


def test_export_grupa_necunoscuta_404(client):
    assert client.get("/grupa/nu-exista?export=png").status_code == 404


def test_renderer_unit():
    """Renderizarea merge si pe o grila goala, cu diacritice romanesti in titlu."""
    grila = construieste_grila([])
    im = imagine_grila(grila, titlu="Orar Săli FMI — ȘâțÎÂ", subtitlu="Semestrul 1 · 2025-2026", mod="grupa")
    png, jpg = bytes_imagine(im, "png"), bytes_imagine(im, "jpg")
    assert png and jpg
    assert Image.open(io.BytesIO(png)).format == "PNG"
    assert Image.open(io.BytesIO(jpg)).format == "JPEG"
