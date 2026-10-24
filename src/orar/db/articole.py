"""Ultimul articol de pe asmi.ro, afisat ca „fun fact" in subsol.

Workerul (`worker/scheduler.py`) il aduce periodic; web-ul doar il citeste. Daca site-ul
ASMI nu raspunde sau isi schimba structura, functia de aducere intoarce None si subsolul
ramane fara acea caseta -- nu e o eroare, doar un plus de continut.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from orar.db.models import ArticolAsmi

log = logging.getLogger(__name__)

__all__ = ["prospateaza_articole", "ultimul_articol"]

#: Pagina cu lista de articole; primul link `/Articole/{slug}` e ultimul articol publicat.
URL_ARTICOLE = "https://asmi.ro/Articole"


def _parseaza(html: str) -> tuple[str, str] | None:
    """(slug, titlu) al primului articol din pagina de articole, sau None."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select('a[href^="/Articole/"]'):
        slug = (a.get("href") or "").removeprefix("/Articole/").strip("/")
        if not slug:
            continue
        # Titlul sta intr-un heading (`h5.card-title`); altfel cadem pe alt/imagine sau pe text.
        titlu = ""
        if (titlu_el := a.find(["h1", "h2", "h3", "h4", "h5", "h6"])) is not None:
            titlu = titlu_el.get_text(" ", strip=True)
        if not titlu and (img := a.find("img")) is not None:
            titlu = (img.get("alt") or "").strip()
        if not titlu:
            titlu = a.get_text(" ", strip=True)
        if slug and titlu:
            return slug, titlu
    return None


def prospateaza_articole(s: Session) -> ArticolAsmi | None:
    """Aduce ultimul articol de pe asmi.ro si il salveaza; None pe orice esec.

    Tabela tine o singura linie: articolul cel mai recent vazut. E o operatie de fundal,
    deci orice eroare se logheaza si ramane sa se reinceapa la urmatoarea sincronizare.
    """
    import httpx

    try:
        raspuns = httpx.get(URL_ARTICOLE, timeout=15, follow_redirects=True)
        raspuns.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("nu am putut citi articolele de pe asmi.ro: %s", e)
        return None
    gasit = _parseaza(raspuns.text)
    if gasit is None:
        log.warning("nu am gasit niciun articol in pagina asmi.ro/Articole")
        return None
    slug, titlu = gasit

    articol = s.scalar(select(ArticolAsmi).limit(1))
    if articol is None:
        articol = ArticolAsmi(slug=slug, titlu=titlu, actualizat_la=datetime.now())
        s.add(articol)
    else:
        articol.slug = slug
        articol.titlu = titlu
        articol.actualizat_la = datetime.now()
    s.commit()
    return articol


def ultimul_articol(s: Session) -> ArticolAsmi | None:
    """Ultimul articol adus, pentru subsol; None daca inca nu s-a adus nimic."""
    return s.scalar(select(ArticolAsmi).limit(1))
