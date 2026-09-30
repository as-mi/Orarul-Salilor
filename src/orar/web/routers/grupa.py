"""Ruta /grupa/{id} -- orarul unei formatiuni, cu mostenire ierarhica."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from orar.db.evenimente import evenimente_in_grila
from orar.db.models import Grupa, Ora, OraArhivata, User
from orar.db.queries import gaseste_grupa, ids_descendenti, ids_stramosi, ore_pentru_grupa
from orar.db.versiuni import versiuni_pentru_grupa
from orar.domain.grid import Provenienta, construieste_grila
from orar.web.afisare import (
    ascunse_din_cont,
    ascunse_pe_pagina,
    e_ascunsa,
    grupeaza_optiunile,
    pune_ascunse_in_cont,
    salveaza,
    salveaza_semigrupa,
    semigrupe_salvate,
)
from orar.web.auth import Cont, cont_curent, verifica_csrf
from orar.web.deps import (
    context_editare,
    context_saptamana,
    context_versiuni,
    get_db,
    saptamana_activa,
    selectie,
    templates,
)

router = APIRouter(prefix="/grupa", tags=["grupa"])

#: `?semigrupa=toate`: renunta la semigrupa aleasa (si la cea tinuta minte in cookie).
TOATE_SEMIGRUPELE = "toate"


def _clasifica(
    ore: list[Ora] | list[OraArhivata], grupa: Grupa, stramosi: set[int], descendenti: set[int]
) -> tuple[dict[int, Provenienta], dict[int, str]]:
    """De unde vine fiecare ora, raportat la grupa ceruta.

    Pentru orele partajate NU numim grupa-proprietar: un student din 244 nu are de ce sa
    vada "de la 241" pentru un curs pe care il au amandoua. Le aratam drept comune.
    """
    provenienta: dict[int, Provenienta] = {}
    surse: dict[int, str] = {}

    for o in ore:
        if o.grupa_id == grupa.id:
            provenienta[o.id] = Provenienta.PROPRIE
        elif o.grupa_id in stramosi:
            provenienta[o.id] = Provenienta.MOSTENITA
            surse[o.id] = o.grupa.nume
        elif o.grupa_id in descendenti:
            provenienta[o.id] = Provenienta.SEMIGRUPA
            surse[o.id] = o.semigrupa.replace("_", " ") if o.semigrupa else o.grupa.nume
        else:
            provenienta[o.id] = Provenienta.PARTAJATA
            surse[o.id] = o.grupa.nume if o.grupa.tip == "optional" else "activitate comună"

    return provenienta, surse


def _orarul_lui(cont: Cont | None, grupa: Grupa) -> User | None:
    """Contul, daca pagina e chiar orarul lui: grupa pe care si-a ales-o. Adminii nu au
    "orarul meu", deci pentru ei pagina ramane una obisnuita, cu alegerile in cookie."""
    if cont is None or cont.user is None or cont.e_admin:
        return None
    return cont.user if cont.user.grupa_id == grupa.id else None


def _pagina_semigrupei(request: Request, semigrupa: Grupa) -> RedirectResponse | None:
    """`/grupa/244-1` -> `/grupa/244?semigrupa=Gr_1`, cu restul parametrilor pastrati.

    Semigrupele nu mai au pagina lor: se aleg cu butoanele Gr 1 / Gr 2 de pe pagina grupei,
    iar alegerea se tine minte in cookie. Nodurile raman in ierarhie -- ele detin
    activitatile semigrupei -- deci adresele vechi (bookmark-uri, linkuri) duc tot acolo.
    """
    numar = semigrupa.nume.rsplit("/", 1)[-1]
    if semigrupa.parinte is None or not numar.isdigit():
        return None
    parametri = dict(request.query_params)
    parametri["semigrupa"] = f"Gr_{numar}"
    return RedirectResponse(
        f"/grupa/{semigrupa.parinte.slug}?{urlencode(parametri)}", status_code=308
    )


@router.get("/{identificator}", response_class=HTMLResponse, response_model=None)
def afiseaza_grupa(
    request: Request,
    identificator: str,
    doar_saptamana: bool = Query(
        False, alias="saptamana", description="doar activitatile din saptamana curenta"
    ),
    zi: date | None = Query(None, description="data de referinta (implicit azi)"),
    semigrupa: str | None = Query(None, description="filtreaza pe semigrupa, ex. Gr_1"),
    versiune: int | None = Query(None, description="o versiune anterioara a orarului"),
    perioada: int | None = Query(None, description="alt orar decat cel implicit"),
    editare: bool = Query(False, description="modul de editare, pentru admini"),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> HTMLResponse | RedirectResponse:
    # Ce orar se afiseaza: cel implicit, altul ales din panoul de versiuni, sau o versiune
    # anterioara. Baza poate tine mai multe semestre; pagina arata unul singur.
    sel = selectie(s, perioada, versiune)
    arhiva = sel.versiune
    grupa = gaseste_grupa(s, identificator, an_universitar=sel.an_universitar)
    if grupa is None:
        raise HTTPException(status_code=404, detail=f"Nu există formațiunea {identificator!r}")
    if grupa.tip == "semigrupa" and (redirect := _pagina_semigrupei(request, grupa)):
        return redirect

    stramosi = ids_stramosi(s, grupa.id) - {grupa.id}
    descendenti = ids_descendenti(s, grupa.id) - {grupa.id}
    ore = ore_pentru_grupa(
        s,
        grupa.id,
        perioada_id=None if arhiva else sel.perioada_id,
        versiune_id=sel.versiune_id,
    )

    # Semigrupele sunt ale orarului de baza. La optionale / facultative / limbi, `Gr_3` e
    # semigrupa *pachetului*, nu a grupei -- acolo alegi activitatea din dropdown, nu dupa
    # semigrupa. Deci si butoanele, si filtrul privesc doar activitatile care nu sunt de pachet.
    semigrupe = sorted({o.semigrupa for o in ore if o.semigrupa and o.grupa.tip != "optional"})

    # Semigrupa aleasa se tine minte in cookie, pe pagina. Fara `?semigrupa=` in adresa se
    # foloseste cea salvata -- daca grupa inca o are; cu `?semigrupa=...` alegerea se
    # inlocuieste, iar `?semigrupa=toate` o uita.
    # Pe orarul propriu, alegerile se tin in cont, nu in cookie.
    al_meu = _orarul_lui(cont, grupa)
    salvata = al_meu.semigrupa if al_meu else semigrupe_salvate(request).get(grupa.slug)
    de_salvat: str | None = salvata
    if semigrupa is None:
        semigrupa = salvata if salvata in semigrupe else None
    else:
        semigrupa = None if semigrupa == TOATE_SEMIGRUPELE else semigrupa
        de_salvat = semigrupa if semigrupa in semigrupe else None

    if semigrupa:
        ore = [o for o in ore if o.grupa.tip == "optional" or o.semigrupa in (None, semigrupa)]

    # Optionalele/facultativele alese din dropdown (cookie, pe pagina), pe materie sau pe
    # activitate. Lista se construieste inainte de ascundere, ca ce e ascuns sa ramana in
    # dropdown, nebifat. Ce e ascuns si nu apare acum in lista ramane ascuns la salvare.
    ascunse = ascunse_din_cont(al_meu) if al_meu else ascunse_pe_pagina(request, grupa.slug)
    optiuni = grupeaza_optiunile(ore, ascunse)
    if ascunse:
        ore = [o for o in ore if not e_ascunsa(o, ascunse)]

    provenienta, surse = _clasifica(ore, grupa, stramosi, descendenti)
    ctx_sapt = context_saptamana(zi, s)
    sapt = saptamana_activa(ctx_sapt)
    grila = construieste_grila(
        ore,
        provenienta=provenienta,
        surse=surse,
        saptamana=sapt,
        doar_saptamana_curenta=doar_saptamana and sapt is not None,
        # evenimentele cu data din saptamana afisata; o versiune veche a orarului nu le are
        evenimente=[] if arhiva else evenimente_in_grila(s, ctx_sapt["azi"], grupa=grupa),
    )

    lant = []
    cur: Grupa | None = grupa.parinte
    while cur is not None:
        lant.append(cur)
        cur = cur.parinte
    lant.reverse()

    # Creditele se numara **o data pe disciplina**, nu pe activitate: un curs si seminarul
    # lui sunt aceeasi materie si aceleasi credite. Disciplinele fara plan incarcat nu intra
    # in suma, si spunem cate sunt -- altfel un total mai mic ar parea o eroare de date.
    materii = {o.materie.id: o.materie for o in ore if o.materie}
    cu_credite = [m for m in materii.values() if m.credite]
    credite = sum(m.credite for m in cu_credite)

    versiuni = versiuni_pentru_grupa(s, grupa, perioada=sel.perioada)

    raspuns = templates.TemplateResponse(
        request=request,
        name="grupa.html",
        context={
            **ctx_sapt,
            "grupa": grupa,
            "lant": lant,
            # Copiii directi: grupele unei serii, seriile si pachetele unui an. Nu si
            # semigrupele -- acelea se aleg cu butoanele Gr 1 / Gr 2, nu au pagina lor.
            "copii": sorted((c for c in grupa.copii if c.tip != "semigrupa"), key=lambda g: g.nume),
            "grila": grila,
            "total": len(ore),
            "credite": credite,
            "materii_cu_credite": len(cu_credite),
            "materii_total": len(materii),
            "filtru_saptamana": doar_saptamana,
            "semigrupa_activa": semigrupa,
            "semigrupe": semigrupe,
            "Provenienta": Provenienta,
            **context_versiuni(s, sel, versiuni),
            "optiuni": optiuni,
            # numarate pe activitati: o materie poate fi aleasa doar pe jumatate
            "nr_optiuni": sum(len(m.activitati) for g in optiuni for m in g.materii),
            "nr_optiuni_vizibile": sum(m.vizibile for g in optiuni for m in g.materii),
            "toate_semigrupele": TOATE_SEMIGRUPELE,
            **context_editare(request, cont, ore, editare=editare, versiune=arhiva),
            "orarul_meu": al_meu is not None,
        },
    )
    if de_salvat != salvata:
        if al_meu:
            al_meu.semigrupa = de_salvat
            s.commit()
        else:
            salveaza_semigrupa(request, raspuns, grupa.slug, de_salvat)
    return raspuns


@router.post("/{identificator}/afisare")
def salveaza_afisarea(
    request: Request,
    identificator: str,
    oferite: list[str] = Form(default=[]),
    vizibile: list[str] = Form(default=[]),
    materii: list[str] = Form(default=[]),
    inapoi: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    """Salveaza ce optionale/facultative se vad pe pagina asta: in cont, daca e orarul
    propriu; altfel in cookie.

    `oferite` = cheile activitatilor din dropdown, `vizibile` = cele bifate, `materii` =
    slug-urile materiilor din dropdown. Ascunse = activitatile oferite si nebifate, plus ce
    era ascuns deja si nu apare acum in dropdown (de pilda cand alegi pe o versiune veche,
    unde o activitate nu exista): alegerea de pe o vedere nu o sterge pe cea de pe alta.
    Ascunderile vechi pe materie intreaga, pentru materiile din dropdown, sunt inlocuite de
    alegerea pe activitati.
    """
    grupa = gaseste_grupa(s, identificator)
    if grupa is None:
        raise HTTPException(status_code=404, detail=f"Nu există formațiunea {identificator!r}")

    # Ne intoarcem doar pe o pagina de grupa a sitului, nu oriunde ne-ar trimite formularul.
    acasa = f"/grupa/{grupa.slug}"
    tinta = inapoi if inapoi.startswith("/grupa/") and "//" not in inapoi else acasa
    raspuns = RedirectResponse(tinta, status_code=303)
    if verifica_csrf(request, csrf):
        oferite_set = set(oferite)
        al_meu = _orarul_lui(cont, grupa)
        vechi = ascunse_din_cont(al_meu) if al_meu else ascunse_pe_pagina(request, grupa.slug)
        ascunse = (vechi - oferite_set - set(materii)) | (oferite_set - set(vizibile))
        if al_meu:
            pune_ascunse_in_cont(al_meu, ascunse)
            s.commit()
        else:
            salveaza(request, raspuns, grupa.slug, ascunse)
    return raspuns
