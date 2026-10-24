"""Paginile statice de politici: confidentialitate, cookies si date personale."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from orar.web.deps import context_saptamana, get_db, templates

router = APIRouter(tags=["pagini"])


def _pagina(request: Request, sablon: str, s: Session) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=sablon,
        context=context_saptamana(s=s),
    )


@router.get("/confidentialitate", response_class=HTMLResponse)
def confidentialitate(request: Request, s: Session = Depends(get_db)) -> HTMLResponse:
    return _pagina(request, "confidentialitate.html", s)


@router.get("/cookies", response_class=HTMLResponse)
def cookies(request: Request, s: Session = Depends(get_db)) -> HTMLResponse:
    return _pagina(request, "cookies.html", s)


@router.get("/date-personale", response_class=HTMLResponse)
def date_personale(request: Request, s: Session = Depends(get_db)) -> HTMLResponse:
    return _pagina(request, "date_personale.html", s)
