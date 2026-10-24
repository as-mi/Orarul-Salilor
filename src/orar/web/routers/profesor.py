"""Orarul unui profesor: toate activitatile lui, indiferent de grupa si de sala.

E si "orarul meu" pentru un cont cu rolul `profesor` -- vezi `routers/cont.py`.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from orar.db.queries import gaseste_profesor, ore_pentru_profesor
from orar.domain.grid import construieste_grila
from orar.web.auth import Cont, cont_curent
from orar.web.deps import (
    context_saptamana,
    context_versiuni,
    get_db,
    saptamana_activa,
    selectie,
    templates,
)
from orar.web.export import raspuns_export

router = APIRouter(prefix="/profesor", tags=["profesor"])


@router.get("/{identificator}", response_class=HTMLResponse, response_model=None)
def orar_profesor(
    request: Request,
    identificator: str,
    doar_saptamana: bool = Query(
        False, alias="saptamana", description="doar activitatile din saptamana curenta"
    ),
    zi: date | None = Query(None),
    perioada: int | None = Query(None, description="alt orar decat cel implicit"),
    export: str | None = Query(None, description="descarca grila ca imagine: png sau jpg"),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> HTMLResponse | Response:
    profesor = gaseste_profesor(s, identificator)
    if profesor is None:
        raise HTTPException(status_code=404, detail=f"Nu există profesorul {identificator!r}")
    sel = selectie(s, perioada)
    ore = ore_pentru_profesor(s, profesor.nume, perioada_id=sel.perioada_id)
    ctx_sapt = context_saptamana(zi, s)
    sapt = saptamana_activa(ctx_sapt)
    grila = construieste_grila(
        ore, saptamana=sapt, doar_saptamana_curenta=doar_saptamana and sapt is not None
    )
    if export in ("png", "jpg"):
        return raspuns_export(
            grila, titlu=profesor.nume, slug=profesor.slug, mod="profesor", format=export,
            sel=sel, doar_saptamana=doar_saptamana,
        )
    user = cont.user if cont else None
    al_meu = bool(user and user.rol == "profesor" and user.profesor == profesor.nume)
    return templates.TemplateResponse(
        request=request,
        name="profesor.html",
        context={
            **ctx_sapt,
            **context_versiuni(s, sel, []),
            "profesor": profesor,
            "grila": grila,
            "total": len(ore),
            "ore_pe_saptamana": sum(o.ora_sfarsit.hour - o.ora_inceput.hour for o in ore),
            "sali": sorted({o.sala.nume for o in ore if o.sala}),
            "filtru_saptamana": doar_saptamana,
            "al_meu": al_meu,
        },
    )
