"""Ruta /sala/{id} -- gradul de ocupare al unei sali."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from orar.db.evenimente import evenimente_in_grila
from orar.db.models import Ora, Sala
from orar.db.queries import gaseste_sala, ore_pentru_sala
from orar.db.versiuni import versiuni_generale, versiuni_pentru_sala
from orar.domain.grid import ORA_MAX, ORA_MIN, ZILE, construieste_grila
from orar.domain.weeks import parse_interval_saptamani
from orar.web.auth import Cont, cont_curent
from orar.web.deps import (
    Selectie,
    context_editare,
    context_saptamana,
    context_versiuni,
    get_db,
    saptamana_activa,
    selectie,
    templates,
)

router = APIRouter(prefix="/sala", tags=["sala"])

#: Sloturi disponibile intr-o saptamana: 5 zile x 12 ore.
SLOTURI_TOTAL = len(ZILE) * (ORA_MAX - ORA_MIN)


@dataclass
class Conflict:
    """Doua activitati care ocupa aceeasi sala in acelasi timp, in aceeasi saptamana."""

    zi: str
    ora: str
    activitati: list[Ora]
    motiv: str


def _saptamani(o: Ora) -> set[int] | None:
    return parse_interval_saptamani(o.saptamani) if o.saptamani else None


def _chiar_se_suprapun(a: Ora, b: Ora) -> bool:
    """Se calca efectiv, sau doar par?

    Doua activitati in aceeasi sala si acelasi interval NU sunt in conflict daca una e in
    saptamani impare si cealalta in pare, sau daca intervalele lor de saptamani sunt
    disjuncte. Fara verificarea asta pagina ar semnala zeci de conflicte inexistente.
    """
    if a.frecventa and b.frecventa and a.frecventa != b.frecventa:
        return False
    sa, sb = _saptamani(a), _saptamani(b)
    return not (sa is not None and sb is not None and not (sa & sb))


def _detecteaza_conflicte(ore: list[Ora]) -> list[Conflict]:
    """Suprapuneri reale in aceeasi sala."""
    conflicte: list[Conflict] = []
    pe_zi: dict[str, list[Ora]] = {}
    for o in ore:
        pe_zi.setdefault(o.zi_saptamana, []).append(o)

    for zi, lot in pe_zi.items():
        for i, a in enumerate(lot):
            for b in lot[i + 1 :]:
                if a.ora_inceput >= b.ora_sfarsit or b.ora_inceput >= a.ora_sfarsit:
                    continue
                if not _chiar_se_suprapun(a, b):
                    continue
                motiv = (
                    "aceeași disciplină listată de două ori cu durate diferite"
                    if a.materie_id == b.materie_id
                    else "două activități diferite în același interval"
                )
                conflicte.append(
                    Conflict(
                        zi=zi,
                        ora=f"{max(a.ora_inceput, b.ora_inceput):%H:%M}",
                        activitati=[a, b],
                        motiv=motiv,
                    )
                )
    return conflicte


def _ocupare(ore: list[Ora]) -> tuple[dict[str, int], int]:
    """Cate sloturi de o ora sunt ocupate, per zi si in total."""
    pe_zi = dict.fromkeys(ZILE, 0)
    ocupate: set[tuple[str, int]] = set()
    for o in ore:
        for h in range(
            max(ORA_MIN, o.ora_inceput.hour), min(ORA_MAX, o.ora_sfarsit.hour or ORA_MAX)
        ):
            ocupate.add((o.zi_saptamana, h))
    for zi, _h in ocupate:
        if zi in pe_zi:
            pe_zi[zi] += 1
    return pe_zi, len(ocupate)


def _ore_salii(s: Session, sala: Sala, sel: Selectie) -> list:
    """Ce ocupa sala in orarul ales: cel viu al perioadei, sau o versiune anterioara."""
    return ore_pentru_sala(
        s,
        sala.id,
        perioada_id=None if sel.versiune else sel.perioada_id,
        versiune_id=sel.versiune_id,
    )


@router.get("", response_class=HTMLResponse)
def listeaza_sali(
    request: Request,
    versiune: int | None = Query(None, description="o versiune anterioara a orarului"),
    perioada: int | None = Query(None, description="alt orar decat cel implicit"),
    s: Session = Depends(get_db),
) -> HTMLResponse:
    sel = selectie(s, perioada, versiune)
    sali = list(s.execute(select(Sala).order_by(Sala.tip, Sala.nume)).scalars())
    ocupari = {}
    for sala in sali:
        _, total = _ocupare(_ore_salii(s, sala, sel))
        ocupari[sala.id] = total
    ctx_sapt = context_saptamana(s=s)
    versiuni = versiuni_generale(s, perioada=sel.perioada)
    return templates.TemplateResponse(
        request=request,
        name="sali.html",
        context={
            **ctx_sapt,
            **context_versiuni(s, sel, versiuni),
            "sali": sali,
            "ocupari": ocupari,
            "sloturi_total": SLOTURI_TOTAL,
        },
    )


@router.get("/{identificator}", response_class=HTMLResponse)
def afiseaza_sala(
    request: Request,
    identificator: str,
    doar_saptamana: bool = Query(
        False, alias="saptamana", description="doar activitatile din saptamana curenta"
    ),
    zi: date | None = Query(None),
    versiune: int | None = Query(None, description="o versiune anterioara a orarului"),
    perioada: int | None = Query(None, description="alt orar decat cel implicit"),
    editare: bool = Query(False, description="modul de editare, pentru admini"),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> HTMLResponse:
    sala = gaseste_sala(s, identificator)
    if sala is None:
        raise HTTPException(status_code=404, detail=f"Nu există sala {identificator!r}")
    sel = selectie(s, perioada, versiune)
    arhiva = sel.versiune

    ore = _ore_salii(s, sala, sel)
    ctx_sapt = context_saptamana(zi, s)
    versiuni = versiuni_pentru_sala(s, sala, perioada=sel.perioada)
    sapt = saptamana_activa(ctx_sapt)
    grila = construieste_grila(
        ore,
        saptamana=sapt,
        doar_saptamana_curenta=doar_saptamana and sapt is not None,
        # rezervarile cu data din saptamana afisata
        evenimente=[] if arhiva else evenimente_in_grila(s, ctx_sapt["azi"], sala=sala),
    )
    pe_zi, total_ocupate = _ocupare(ore)

    return templates.TemplateResponse(
        request=request,
        name="sala.html",
        context={
            **ctx_sapt,
            **context_versiuni(s, sel, versiuni),
            **context_editare(request, cont, ore, editare=editare, versiune=arhiva),
            "sala": sala,
            "grila": grila,
            "total": len(ore),
            "ocupare_pe_zi": pe_zi,
            "ocupate": total_ocupate,
            "sloturi_total": SLOTURI_TOTAL,
            "procent": round(100 * total_ocupate / SLOTURI_TOTAL),
            "conflicte": _detecteaza_conflicte(ore),
            "filtru_saptamana": doar_saptamana,
            "ore_pe_zi": ORA_MAX - ORA_MIN,
        },
    )
