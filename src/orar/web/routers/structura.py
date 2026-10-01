"""Structura anului universitar, pentru admini: perioadele de activitate didactica,
vacantele, sesiunile, restantele si sustinerea licentei.

Din ea se numara saptamanile academice (`domain/weeks.py`): odata configurat un an, numarul
si paritatea saptamanii nu mai depind de ce publica facultatea pe pagina ei. Pagina arata
si numerotarea care rezulta, ca adminul s-o poata compara cu anuntul oficial.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from orar.db.models import PerioadaStructura
from orar.db.structura import interval_an, perioade_anului
from orar.domain.weeks import (
    FELURI_PERIOADA,
    PUBLIC_PERIOADA,
    Paritate,
    an_universitar_al,
    ce_lipseste,
    numeroteaza,
)
from orar.web.auth import Cont, cere_admin, verifica_csrf
from orar.web.deps import context_saptamana, get_db, templates

router = APIRouter(prefix="/admin/an-universitar", tags=["structura"])

ZILE_SCURTE = ("lun", "mar", "mie", "joi", "vin", "sâm", "dum")


def _an_valid(an: str) -> str:
    """`2026-2027`, cu anii consecutivi; altfel anul universitar curent."""
    parti = an.split("-")
    if (
        len(parti) == 2
        and all(p.isdigit() and len(p) == 4 for p in parti)
        and int(parti[1]) == int(parti[0]) + 1
    ):
        return an
    return an_universitar_al(date.today())


def _zi(d: date) -> str:
    return f"{ZILE_SCURTE[d.weekday()]} {d:%d.%m}"


def _intervale(zile: list[date]) -> list[str]:
    """Zilele, stranse in intervale continue: `joi 01.10 - vin 02.10`. Weekendul dintre doua
    zile lucratoare nu rupe intervalul."""
    zile = sorted(d for d in zile if d.weekday() < 5)
    intervale: list[list[date]] = []
    for d in zile:
        if (
            intervale
            and (d - intervale[-1][1]).days <= 3
            and all(
                (intervale[-1][1] + timedelta(days=i)).weekday() >= 5
                for i in range(1, (d - intervale[-1][1]).days)
            )
        ):
            intervale[-1][1] = d
        else:
            intervale.append([d, d])
    return [_zi(a) if a == b else f"{_zi(a)} - {_zi(b)}" for a, b in intervale]


def _saptamani(perioade: list[PerioadaStructura]) -> dict[int, list[tuple[int, str, list[str]]]]:
    """Numerotarea rezultata, pe semestre: {semestru: [(numar, paritate, intervale)]}."""
    zile = numeroteaza([p.ca_perioada() for p in perioade])
    semestru_zilei = {
        d: p.semestru
        for p in perioade
        if p.fel == "didactica"
        for d in (
            p.data_inceput + timedelta(days=i)
            for i in range((p.data_sfarsit - p.data_inceput).days + 1)
        )
    }
    pe_semestre: dict[int, dict[int, list[date]]] = {}
    for d, numar in zile.items():
        pe_semestre.setdefault(semestru_zilei.get(d) or 0, {}).setdefault(numar, []).append(d)
    return {
        sem: [
            (numar, Paritate.din_numar(numar).eticheta, _intervale(z))
            for numar, z in sorted(saptamani.items())
        ]
        for sem, saptamani in sorted(pe_semestre.items())
    }


def _pagina(
    request: Request, s: Session, an: str, *, eroare: str = "", stare: int = 200, v=None
) -> HTMLResponse:  # noqa: ANN001
    perioade = perioade_anului(s, an)
    inceput = int(an.split("-")[0])
    return templates.TemplateResponse(
        request=request,
        name="admin_structura.html",
        context={
            **context_saptamana(s=s),
            "an": an,
            "an_inainte": f"{inceput - 1}-{inceput}",
            "an_dupa": f"{inceput + 1}-{inceput + 2}",
            "e_anul_curent": an == an_universitar_al(date.today()),
            "perioade": perioade,
            "lipsuri": ce_lipseste([p.ca_perioada() for p in perioade]),
            "saptamani": _saptamani(perioade),
            "feluri": FELURI_PERIOADA,
            "publicuri": PUBLIC_PERIOADA,
            "minim": interval_an(an)[0].isoformat(),
            "maxim": interval_an(an)[1].isoformat(),
            "zi": _zi,
            "eroare": eroare,
            # valorile formularului, pastrate dupa o eroare
            "v": v
            or {
                "fel": "didactica",
                "semestru": "1",
                "nume": "",
                "de_la": "",
                "pana_la": "",
                "saptamana": "",
                "pentru": "toti",
            },
        },
        status_code=stare,
    )


@router.get("", response_class=HTMLResponse)
def structura(
    request: Request,
    an: str = Query(""),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> HTMLResponse:
    return _pagina(request, s, _an_valid(an))


@router.post("/perioada", response_model=None)
def adauga_perioada(
    request: Request,
    an: str = Form(""),
    fel: str = Form(""),
    semestru: str = Form(""),
    nume: str = Form(""),
    de_la: str = Form(""),
    pana_la: str = Form(""),
    saptamana: str = Form(""),
    pentru: str = Form("toti"),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> HTMLResponse | RedirectResponse:
    an = _an_valid(an)
    v = {
        "fel": fel,
        "semestru": semestru,
        "nume": nume,
        "de_la": de_la,
        "pana_la": pana_la,
        "saptamana": saptamana,
        "pentru": pentru,
    }

    def eroare(text: str) -> HTMLResponse:
        return _pagina(request, s, an, eroare=text, stare=400, v=v)

    if not verifica_csrf(request, csrf):
        return eroare("Formularul a expirat. Încearcă din nou.")
    if fel not in FELURI_PERIOADA:
        return eroare("Alege felul perioadei.")
    try:
        inceput, sfarsit = date.fromisoformat(de_la), date.fromisoformat(pana_la or de_la)
    except ValueError:
        return eroare("Alege datele perioadei.")
    # un an scris din doua cifre (27 in loc de 2027) ajunge din browser ca anul 0027
    for d in (inceput, sfarsit):
        if d.year < 1000:
            return eroare(
                f"Data {d.day:02d}.{d.month:02d}.{d.year:04d} are anul greșit: "
                "scrie anul cu patru cifre."
            )
    if sfarsit < inceput:
        return eroare("Data de sfârșit e înaintea celei de început.")
    if pentru not in PUBLIC_PERIOADA:
        return eroare("Alege pentru cine e perioada.")
    minim, maxim = interval_an(an)
    if not (minim <= inceput and sfarsit <= maxim):
        return eroare(
            f"Perioada {inceput:%d.%m.%Y} - {sfarsit:%d.%m.%Y} nu e în anul universitar {an}, "
            f"care merge de la {minim:%d.%m.%Y} la {maxim:%d.%m.%Y}."
        )
    if semestru not in ("", "1", "2"):
        return eroare("Semestrul e 1 sau 2.")
    if fel == "didactica" and not semestru:
        return eroare("Activitatea didactică ține de un semestru: alege-l.")
    numar = None
    if saptamana.strip():
        if fel != "didactica":
            return eroare(
                "Doar o perioadă de activitate didactică se poate număra ca o săptămână anume."
            )
        if not saptamana.strip().isdigit() or not 1 <= int(saptamana) <= 20:
            return eroare("Săptămâna e un număr între 1 și 20.")
        numar = int(saptamana)

    s.add(
        PerioadaStructura(
            an_univ=an,
            fel=fel,
            semestru=int(semestru) if semestru else None,
            nume=" ".join(nume.split())[:80] or None,
            data_inceput=inceput,
            data_sfarsit=sfarsit,
            saptamana=numar,
            pentru=pentru,
        )
    )
    s.commit()
    return RedirectResponse(f"/admin/an-universitar?an={an}#perioade", status_code=303)


@router.post("/perioada/{perioada_id}/sterge")
def sterge_perioada(
    request: Request,
    perioada_id: int,
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> RedirectResponse:
    p = s.get(PerioadaStructura, perioada_id)
    if p is None:
        raise HTTPException(404, "perioada nu exista")
    an = p.an_univ
    if verifica_csrf(request, csrf):
        s.delete(p)
        s.commit()
    return RedirectResponse(f"/admin/an-universitar?an={an}#perioade", status_code=303)
