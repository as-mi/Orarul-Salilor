"""Sesizari: oricine poate semnala o problema din orar; adminii le trateaza.

Trimiterea e publica -- nu ceri cont ca sa spui ca o sala e gresita. Tocmai de aceea are
cateva frane: tokenul CSRF al sesiunii, un camp-capcana pe care il completeaza doar robotii,
limite de lungime si cel mult cateva sesizari pe sesiune intr-un interval scurt.
"""

from __future__ import annotations

import time
from datetime import datetime
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from orar.db.corectii import descriere
from orar.db.models import Ora, Sesizare
from orar.web.auth import Cont, cere_admin, cont_curent, verifica_csrf
from orar.web.deps import context_saptamana, get_db, templates

router = APIRouter(tags=["sesizari"])

MESAJ_MIN, MESAJ_MAX = 5, 1000
CONTACT_MAX = 180
#: Cel mult atatea sesizari pe sesiune in fereastra de mai jos.
LIMITA, FEREASTRA = 5, 10 * 60
STARI = ("noua", "rezolvata", "respinsa")


def _pagina_sigura(pagina: str) -> str | None:
    """Sesizarile se trimit de pe o pagina de orar si tot acolo ne intoarcem."""
    if not pagina.startswith(("/grupa/", "/sala/")) or "//" in pagina or "\\" in pagina:
        return None
    return pagina[:200]


def _cu_parametru(pagina: str, **parametri: str) -> str:
    """Pagina, cu parametrii dati adaugati (si fara cei vechi cu acelasi nume)."""
    parti = urlsplit(pagina)
    ramasi = [(k, v) for k, v in parse_qsl(parti.query) if k not in ("raportat", "raport_eroare")]
    return f"{parti.path}?{urlencode([*ramasi, *parametri.items()])}"


def _prea_multe(request: Request) -> bool:
    acum = time.time()
    recente = [t for t in request.session.get("sesizari", []) if acum - t < FEREASTRA]
    if len(recente) >= LIMITA:
        request.session["sesizari"] = recente
        return True
    request.session["sesizari"] = [*recente, acum]
    return False


# ------------------------------------------------------------------ trimiterea


@router.post("/sesizare")
def trimite(
    request: Request,
    pagina: str = Form(""),
    ora_id: str = Form(""),
    mesaj: str = Form(""),
    contact: str = Form(""),
    website: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    inapoi = _pagina_sigura(pagina)
    if inapoi is None:
        raise HTTPException(400, "sesizarea trebuie trimisa de pe pagina unui orar")

    def eroare(text: str) -> RedirectResponse:
        return RedirectResponse(_cu_parametru(inapoi, raport_eroare=text), status_code=303)

    if not verifica_csrf(request, csrf):
        return eroare("Formularul a expirat. Încearcă din nou.")
    if website:
        # Campul e ascuns: il completeaza doar un robot. Ne prefacem ca a mers.
        return RedirectResponse(_cu_parametru(inapoi, raportat="1"), status_code=303)

    mesaj = mesaj.strip()
    if len(mesaj) < MESAJ_MIN:
        return eroare("Scrie în câteva cuvinte care e problema.")
    if len(mesaj) > MESAJ_MAX:
        return eroare(f"Mesajul e prea lung (cel mult {MESAJ_MAX} de caractere).")
    if _prea_multe(request):
        return eroare("Ai trimis mai multe sesizări la rând. Încearcă peste câteva minute.")

    ora = s.get(Ora, int(ora_id)) if ora_id.isdigit() else None
    s.add(
        Sesizare(
            creat_la=datetime.now(),
            pagina=inapoi,
            ora_id=ora.id if ora else None,
            activitate=descriere(ora)[:300] if ora else None,
            mesaj=mesaj,
            user_id=cont.user.id if cont and cont.user else None,
            contact=contact.strip()[:CONTACT_MAX] or None,
        )
    )
    s.commit()
    return RedirectResponse(_cu_parametru(inapoi, raportat="1"), status_code=303)


# ---------------------------------------------------------------- coada adminului


@router.get("/admin/sesizari", response_class=HTMLResponse)
def coada(
    request: Request,
    arata: str = Query("noi", description="noi | toate"),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> HTMLResponse:
    stmt = select(Sesizare).options(joinedload(Sesizare.user), joinedload(Sesizare.ora))
    if arata != "toate":
        stmt = stmt.where(Sesizare.stare == "noua")
    # cele noi intai, de la cea mai veche: prima venita, prima tratata
    sesizari = list(s.scalars(stmt.order_by(Sesizare.stare != "noua", Sesizare.creat_la)).unique())
    return templates.TemplateResponse(
        request=request,
        name="admin_sesizari.html",
        context={
            **context_saptamana(),
            "sesizari": sesizari,
            "arata": "toate" if arata == "toate" else "noi",
            "nr_noi": s.scalar(
                select(func.count()).select_from(Sesizare).where(Sesizare.stare == "noua")
            ),
            "inapoi": quote(f"/admin/sesizari?arata={arata}", safe=""),
        },
    )


@router.post("/admin/sesizari/{sesizare_id}")
def trateaza(
    request: Request,
    sesizare_id: int,
    stare: str = Form(""),
    nota: str = Form(""),
    arata: str = Form("noi"),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    admin: Cont = Depends(cere_admin),
) -> RedirectResponse:
    """Marcheaza o sesizare ca rezolvata sau respinsa (sau o redeschide)."""
    inapoi = f"/admin/sesizari?arata={'toate' if arata == 'toate' else 'noi'}"
    if not verifica_csrf(request, csrf):
        return RedirectResponse(inapoi, status_code=303)
    if stare not in STARI:
        raise HTTPException(400, f"stare necunoscuta: {stare!r}")
    sesizare = s.get(Sesizare, sesizare_id)
    if sesizare is None:
        raise HTTPException(404, "sesizarea nu exista")
    marcheaza(sesizare, stare, de_catre=admin.nume, nota=nota)
    s.commit()
    return RedirectResponse(inapoi, status_code=303)


def marcheaza(sesizare: Sesizare, stare: str, *, de_catre: str, nota: str = "") -> None:
    sesizare.stare = stare
    if stare == "noua":
        sesizare.tratata_de = sesizare.tratata_la = None
    else:
        sesizare.tratata_de, sesizare.tratata_la = de_catre, datetime.now()
    if nota.strip():
        sesizare.nota = nota.strip()[:500]
