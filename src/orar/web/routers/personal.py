"""Orarul propriu: adauga/stergi evenimente personale si importi activitati din alte
grupe/serii. Totul se intoarce pe pagina de orar din care a venit (`inapoi`)."""

from __future__ import annotations

from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from orar.db.models import ActivitateImportata, EvenimentPersonal, Grupa, Ora
from orar.db.orare import perioada_implicita
from orar.db.personal import activitati_import, formatiuni_import
from orar.domain.grid import ZILE
from orar.web.auth import Cont, cont_curent, verifica_csrf
from orar.web.deps import get_db, templates

router = APIRouter(tags=["orarul-meu"])

FELURI_EVENIMENT = ("saptamanal", "data")


def _contul(request: Request, cont: Cont | None) -> RedirectResponse | None:
    """Pagina e doar pentru un cont cu orar propriu; None = continua."""
    if cont is None or cont.user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return None


def _inapoi(inapoi: str) -> str:
    return inapoi if inapoi.startswith("/grupa/") and "//" not in inapoi else "/orarul-meu"


# ------------------------------------------------------------------ evenimente


@router.post("/orarul-meu/eveniment")
def adauga_eveniment(
    request: Request,
    tip: str = Form(...),
    titlu: str = Form(...),
    zi_saptamana: str = Form(""),
    data: str = Form(""),
    ora_inceput: time = Form(...),
    ora_sfarsit: time = Form(...),
    loc: str = Form(""),
    frecventa: str = Form(""),
    inapoi: str = Form("/orarul-meu"),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    _contul(request, cont)
    tinta = _inapoi(inapoi)
    if not verifica_csrf(request, csrf):
        return RedirectResponse(tinta, status_code=303)
    if tip not in FELURI_EVENIMENT or not titlu.strip() or ora_sfarsit <= ora_inceput:
        return RedirectResponse(tinta, status_code=303)

    saptamanal = tip == "saptamanal"
    zi = zi_saptamana if saptamanal and zi_saptamana in ZILE else None
    ziua = date.fromisoformat(data) if (not saptamanal and data) else None
    if saptamanal and zi is None:
        return RedirectResponse(tinta, status_code=303)
    if not saptamanal and ziua is None:
        return RedirectResponse(tinta, status_code=303)

    s.add(
        EvenimentPersonal(
            user_id=cont.user.id,
            titlu=titlu.strip(),
            loc=loc.strip() or None,
            zi_saptamana=zi,
            data=ziua,
            ora_inceput=ora_inceput,
            ora_sfarsit=ora_sfarsit,
            frecventa=frecventa if saptamanal and frecventa in ("SI", "SP") else None,
            creat_la=datetime.now(),
        )
    )
    s.commit()
    return RedirectResponse(tinta, status_code=303)


@router.post("/orarul-meu/eveniment/{eveniment_id}/sterge")
def sterge_eveniment(
    request: Request,
    eveniment_id: int,
    inapoi: str = Form("/orarul-meu"),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    _contul(request, cont)
    tinta = _inapoi(inapoi)
    if verifica_csrf(request, csrf):
        ev = s.get(EvenimentPersonal, eveniment_id)
        if ev is not None and ev.user_id == cont.user.id:
            s.delete(ev)
            s.commit()
    return RedirectResponse(tinta, status_code=303)


# ------------------------------------------------------------------ import


@router.get("/orarul-meu/import", response_class=HTMLResponse)
def activitati_de_import(
    request: Request,
    formatiune_id: int = Query(...),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> HTMLResponse:
    """Partial htmx: activitatile unei formatiuni, pentru a alege ce sa importi."""
    _contul(request, cont)
    orar = perioada_implicita(s)
    formatiune = s.get(Grupa, formatiune_id)
    activitati = [] if formatiune is None else activitati_import(s, formatiune.id, orar)
    return templates.TemplateResponse(
        request=request,
        name="_personal_import_activitati.html",
        context={"formatiune": formatiune, "activitati": activitati},
    )


@router.post("/orarul-meu/import")
def importa_activitati(
    request: Request,
    activitati: list[str] = Form([]),
    inapoi: str = Form("/orarul-meu"),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    _contul(request, cont)
    tinta = _inapoi(inapoi)
    if not verifica_csrf(request, csrf):
        return RedirectResponse(tinta, status_code=303)

    # ce exista deja, ca sa nu importam de doua ori acelasi lucru
    existente = {
        (i.materie, i.zi_saptamana, i.ora_inceput, i.ora_sfarsit, i.sala, i.sursa)
        for i in s.scalars(
            select(ActivitateImportata).where(ActivitateImportata.user_id == cont.user.id)
        )
    }
    for text in activitati:
        if not text.isdigit():
            continue
        o = s.get(Ora, int(text))
        if o is None:
            continue
        sursa = o.grupa.nume if o.grupa is not None else "?"
        sala = o.sala.nume if o.sala is not None else None
        cheie = (
            o.materie.nume if o.materie is not None else "?",
            o.zi_saptamana,
            o.ora_inceput,
            o.ora_sfarsit,
            sala,
            sursa,
        )
        if cheie in existente:
            continue
        s.add(
            ActivitateImportata(
                user_id=cont.user.id,
                sursa=sursa,
                materie=o.materie.nume if o.materie is not None else "?",
                tip=o.tip_ora_materie,
                profesor=o.profesor.nume if o.profesor is not None else None,
                sala=sala,
                zi_saptamana=o.zi_saptamana,
                ora_inceput=o.ora_inceput,
                ora_sfarsit=o.ora_sfarsit,
                frecventa=o.frecventa,
                saptamani=o.saptamani,
                creat_la=datetime.now(),
            )
        )
        existente.add(cheie)
    s.commit()
    return RedirectResponse(tinta, status_code=303)


@router.post("/orarul-meu/import/{import_id}/sterge")
def sterge_import(
    request: Request,
    import_id: int,
    inapoi: str = Form("/orarul-meu"),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    _contul(request, cont)
    tinta = _inapoi(inapoi)
    if verifica_csrf(request, csrf):
        im = s.get(ActivitateImportata, import_id)
        if im is not None and im.user_id == cont.user.id:
            s.delete(im)
            s.commit()
    return RedirectResponse(tinta, status_code=303)
