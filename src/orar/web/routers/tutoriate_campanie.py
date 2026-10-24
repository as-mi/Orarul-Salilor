"""Campaniile de tutoriate: formular dinamic, aplicatii, echipe si planificare automata.

Un flux complet de recrutare in fata tutoriatelor existente:

1. responsabilul creeaza o **campanie** pe semestru si ii construieste **formularul**;
2. deschide **posturi** (materii pentru publicul-ținta);
3. studentii **aplica** prin pagina publica (raspunsuri + disponibilitate + prioritati);
4. responsabilul ii accepta si formeaza **echipe**;
5. planificatorul propune **sesiuni** (acoperire + continuitate), iar confirmarea le
   materializeaza in `Tutoriat`/`TutoriatTutor` existente.
"""

from __future__ import annotations

from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from orar.db.models import (
    AplicatieTutor,
    CampanieTutoriate,
    EchipaTutoriat,
    EchipaTutoriatMembru,
    Perioada,
    PosturiDeschise,
    Sala,
    SesiuneTutoriat,
    StructuraFormular,
)
from orar.db.orare import perioada_implicita
from orar.db.tutoriate_campanie import (
    disponibilitate_tutor,
    incarca_json,
    materializeaza_sesiune,
    ocupare_public,
    scrie_json,
    tutori_liberi_slot,
)
from orar.domain.grid import ORA_MAX, ORA_MIN, ZILE
from orar.domain.tutoriate_scheduler import planifica
from orar.web.auth import Cont, cere_tutoriate, cont_curent, verifica_csrf
from orar.web.deps import context_saptamana, get_db, templates
from orar.web.routers.tutoriate import _optiuni_public

router = APIRouter(tags=["tutoriate-campanii"])

STARI_CAMPANIE = ("ciorna", "deschisa", "inchisa", "planificata", "arhivata")
TABURI_CAMPANIE = (
    ("formular", "Formular"),
    ("posturi", "Posturi"),
    ("aplicatii", "Aplicații"),
    ("echipe", "Echipe"),
    ("planificare", "Planificare"),
)
ZILE_RO = {
    "Luni": "Luni",
    "Marti": "Marți",
    "Miercuri": "Miercuri",
    "Joi": "Joi",
    "Vineri": "Vineri",
}
TIPURI_INTREBARE = ("text", "radio", "checkbox", "grila_orar")
#: Cum apar tipurile in constructorul de formular; codurile raman cele de mai sus.
TIPURI_INTREBARE_LABEL = {
    "text": "Răspuns scurt",
    "radio": "O singură alegere",
    "checkbox": "Mai multe alegeri",
    "grila_orar": "Grila de orar",
}


# ------------------------------------------------------------------ ajutoare


def _campania(s: Session, campanie_id: int) -> CampanieTutoriate:
    campanie = s.get(CampanieTutoriate, campanie_id)
    if campanie is None:
        raise HTTPException(status_code=404, detail="Campania nu există.")
    return campanie


def _intrebari(formular: StructuraFormular | None) -> list[dict]:
    data = incarca_json(formular.intrebari if formular else None, [])
    return data if isinstance(data, list) else []


def _context_campanie(s: Session, campanie: CampanieTutoriate, *, tab: str = "") -> dict:
    return {
        **context_saptamana(s=s),
        "campanie": campanie,
        "tab": tab,
        "taburi": TABURI_CAMPANIE,
        "stari": STARI_CAMPANIE,
        "zile": ZILE,
        "zile_ro": ZILE_RO,
        "ore": list(range(ORA_MIN, ORA_MAX)),
        "tipuri_intrebare": [(cod, TIPURI_INTREBARE_LABEL[cod]) for cod in TIPURI_INTREBARE],
        "tipuri_etichete": TIPURI_INTREBARE_LABEL,
    }


def _este_deschisa(campanie: CampanieTutoriate, azi: date | None = None) -> bool:
    azi = azi or date.today()
    return campanie.stare == "deschisa" and campanie.data_deschidere <= azi <= campanie.data_inchidere


def _orar_campanie(s: Session, campanie: CampanieTutoriate) -> Perioada | None:
    """Perioada (semestrul + anul) pe care se construiesc posturile, ca sa propuna publicul
    real din orar: specializarile, anii si seriile existente, nu coduri scrise de mana."""
    return s.scalar(
        select(Perioada).where(Perioada.an_univ == campanie.an_univ, Perioada.semestru == campanie.semestru)
    )


def _sfarsit_semestru(s: Session, an_univ: str, semestru: int) -> date | None:
    """Ultima zi de activitate didactica a unui semestru, din structura anului (None daca
    structura nu e configurata). E data sugerata pentru sfarsitul sesiunilor unei campanii."""
    from orar.db.structura import perioade_anului

    didactice = [
        p
        for p in perioade_anului(s, an_univ)
        if p.fel == "didactica" and p.semestru == semestru and p.pentru != "terminali"
    ]
    return max((p.data_sfarsit for p in didactice), default=None)


# ------------------------------------------------------------------ lista si creare


@router.get("/tutoriate/campanii", response_class=HTMLResponse)
def campanii(
    request: Request,
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> HTMLResponse:
    lista = list(
        s.scalars(select(CampanieTutoriate).order_by(CampanieTutoriate.an_univ.desc(), CampanieTutoriate.semestru.desc()))
    )
    return templates.TemplateResponse(
        request=request,
        name="tut_campanii.html",
        context={**context_saptamana(s=s), "campanii": lista},
    )


@router.get("/tutoriate/campanii/noua", response_class=HTMLResponse)
def campanie_noua_formular(
    request: Request,
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> HTMLResponse:
    orar = perioada_implicita(s)
    azi = date.today()
    sfarsit = _sfarsit_semestru(s, orar.an_univ, orar.semestru) if orar else None
    return templates.TemplateResponse(
        request=request,
        name="tut_campanie_noua.html",
        context={
            **context_saptamana(s=s),
            "orar": orar,
            "azi": azi,
            "sfarsit": sfarsit,
            "stari": STARI_CAMPANIE,
        },
    )


@router.get("/tutoriate/campanii/noua/sfarsit", response_class=HTMLResponse)
def sugereaza_sfarsit(
    request: Request,
    an_univ: str = Query(""),
    semestru: int = Query(1),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> HTMLResponse:
    """Partial htmx: data de sfarsit sugerata din structura anului, cand se schimba anul sau
    semestrul in formularul de campanie noua."""
    sfarsit = _sfarsit_semestru(s, an_univ.strip(), semestru) if an_univ.strip() else None
    return templates.TemplateResponse(
        request=request,
        name="_tutc_sfarsit_sugerat.html",
        context={"sfarsit": sfarsit, "azi": date.today()},
    )


@router.post("/tutoriate/campanii/noua")
def campanie_noua(
    request: Request,
    nume: str = Form(...),
    an_univ: str = Form(...),
    semestru: int = Form(...),
    data_deschidere: date = Form(...),
    data_inchidere: date = Form(...),
    data_inceput: date = Form(...),
    data_sfarsit: date = Form(...),
    indulgenta: int = Form(75),
    min_tutori_echipa: int = Form(1),
    max_tutori_echipa: int = Form(3),
    durata_sesiune: int = Form(2),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    if not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Token CSRF invalid.")
    campanie = CampanieTutoriate(
        an_univ=an_univ,
        semestru=semestru,
        nume=nume,
        data_deschidere=data_deschidere,
        data_inchidere=data_inchidere,
        data_inceput=data_inceput,
        data_sfarsit=data_sfarsit,
        indulgenta=max(0, min(100, indulgenta)),
        min_tutori_echipa=max(1, min(3, min_tutori_echipa)),
        max_tutori_echipa=max(1, min(3, max_tutori_echipa)),
        durata_sesiune=max(1, min(6, durata_sesiune)),
        stare="ciorna",
        creat_de=responsabil.nume,
        creat_la=datetime.now(),
    )
    s.add(campanie)
    s.flush()
    s.add(StructuraFormular(campanie_id=campanie.id, intrebari="[]", creat_de=responsabil.nume, creat_la=datetime.now()))
    s.commit()
    return RedirectResponse(f"/tutoriate/campanii/{campanie.id}", status_code=303)


# ------------------------------------------------------------------ aplicarea publica


@router.get("/tutoriate/campanii/{campanie_id}/aplica", response_class=HTMLResponse)
def aplica_formular(
    request: Request,
    campanie_id: int,
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> HTMLResponse:
    campanie = _campania(s, campanie_id)
    posturi = list(
        s.scalars(select(PosturiDeschise).where(PosturiDeschise.campanie_id == campanie.id).order_by(PosturiDeschise.materie))
    )
    existenta = None
    if cont is not None and cont.user is not None:
        existenta = s.scalar(
            select(AplicatieTutor).where(
                AplicatieTutor.campanie_id == campanie.id, AplicatieTutor.user_id == cont.user.id
            )
        )
    return templates.TemplateResponse(
        request=request,
        name="tut_aplica.html",
        context={
            **context_saptamana(s=s),
            "campanie": campanie,
            "posturi": posturi,
            "intrebari": _intrebari(campanie.formular),
            "zile": ZILE,
            "zile_ro": ZILE_RO,
            "ore": list(range(ORA_MIN, ORA_MAX)),
            "deschisa": _este_deschisa(campanie),
            "existenta": existenta,
        },
    )


@router.post("/tutoriate/campanii/{campanie_id}/aplica")
def aplica_trimite(
    request: Request,
    campanie_id: int,
    raspunsuri: str = Form("{}"),
    disponibilitate: str = Form("{}"),
    materii: str = Form("[]"),
    preferinte_co_tutori: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    campanie = _campania(s, campanie_id)
    if cont is None or cont.user is None:
        inapoi = f"/tutoriate/campanii/{campanie_id}/aplica"
        raise HTTPException(status_code=303, headers={"Location": f"/login?inapoi={inapoi}"})
    if not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Token CSRF invalid.")
    if not _este_deschisa(campanie):
        raise HTTPException(status_code=400, detail="Campania nu mai acceptă aplicații.")

    existenta = s.scalar(
        select(AplicatieTutor).where(
            AplicatieTutor.campanie_id == campanie.id, AplicatieTutor.user_id == cont.user.id
        )
    )
    if existenta is not None:
        existenta.raspunsuri = raspunsuri
        existenta.disponibilitate = disponibilitate
        existenta.materii = materii
        existenta.preferinte_co_tutori = preferinte_co_tutori or None
        existenta.stare = "aplicata"
    else:
        s.add(
            AplicatieTutor(
                campanie_id=campanie.id,
                user_id=cont.user.id,
                raspunsuri=raspunsuri,
                disponibilitate=disponibilitate,
                materii=materii,
                preferinte_co_tutori=preferinte_co_tutori or None,
                stare="aplicata",
                creat_de=cont.nume,
                creat_la=datetime.now(),
            )
        )
    s.commit()
    return RedirectResponse(f"/tutoriate/campanii/{campanie_id}/aplica", status_code=303)


# ------------------------------------------------------------------ dashboard


@router.get("/tutoriate/campanii/{campanie_id}", response_class=HTMLResponse)
def campanie(
    request: Request,
    campanie_id: int,
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    return RedirectResponse(f"/tutoriate/campanii/{campanie_id}/formular", status_code=303)


@router.post("/tutoriate/campanii/{campanie_id}/stare")
def schimba_starea(
    request: Request,
    campanie_id: int,
    stare: str = Form(...),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    campanie = _campania(s, campanie_id)
    if stare not in STARI_CAMPANIE or not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Stare sau token invalid.")
    campanie.stare = stare
    s.commit()
    return RedirectResponse(f"/tutoriate/campanii/{campanie_id}", status_code=303)


# ------------------------------------------------------------------ formular


@router.post("/tutoriate/campanii/{campanie_id}/formular")
def salveaza_formular(
    request: Request,
    campanie_id: int,
    intrebari: str = Form("[]"),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    campanie = _campania(s, campanie_id)
    if not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Token CSRF invalid.")
    date = incarca_json(intrebari, [])
    if not isinstance(date, list):
        raise HTTPException(status_code=400, detail="Formular invalid.")
    curatate = []
    for i, q in enumerate(date):
        if not isinstance(q, dict) or q.get("tip") not in TIPURI_INTREBARE:
            continue
        curatate.append(
            {
                "cheie": str(q.get("cheie") or f"q{i}").strip(),
                "tip": q["tip"],
                "text": str(q.get("text") or ""),
                "obligatoriu": bool(q.get("obligatoriu")),
                "optiuni": [str(o).strip() for o in q.get("optiuni", []) if str(o).strip()],
            }
        )
    if campanie.formular is None:
        campanie.formular = StructuraFormular(campanie_id=campanie.id, intrebari="[]", creat_de=responsabil.nume, creat_la=datetime.now())
        s.add(campanie.formular)
    campanie.formular.intrebari = scrie_json(curatate)
    s.commit()
    # Constructorul salveaza fiecare intrebare in fundal (fetch cu HX-Request); atunci nu
    # vrem redirectul catre pagina intreaga, doar o confirmare usoara.
    if request.headers.get("HX-Request"):
        return HTMLResponse("", status_code=200)
    return RedirectResponse(f"/tutoriate/campanii/{campanie_id}/formular", status_code=303)


# ------------------------------------------------------------------ posturi


@router.post("/tutoriate/campanii/{campanie_id}/posturi")
def adauga_post(
    request: Request,
    campanie_id: int,
    materie: str = Form(...),
    descriere: str = Form(""),
    specializari: list[str] = Form([]),
    ani: list[str] = Form([]),
    serii: list[str] = Form([]),
    nr_max_tutori: int = Form(0),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    campanie = _campania(s, campanie_id)
    if not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Token CSRF invalid.")
    s.add(
        PosturiDeschise(
            campanie_id=campanie.id,
            materie=materie.strip(),
            descriere=descriere.strip() or None,
            specializari=",".join(specializari).strip() or None,
            ani=",".join(ani).strip() or None,
            serii=",".join(serii).strip() or None,
            nr_max_tutori=nr_max_tutori or None,
            creat_de=responsabil.nume,
            creat_la=datetime.now(),
        )
    )
    s.commit()
    return RedirectResponse(f"/tutoriate/campanii/{campanie_id}/posturi", status_code=303)


@router.post("/tutoriate/campanii/{campanie_id}/posturi/{post_id}/sterge")
def sterge_post(
    request: Request,
    campanie_id: int,
    post_id: int,
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    _campania(s, campanie_id)
    post = s.get(PosturiDeschise, post_id)
    if not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Token CSRF invalid.")
    if post is not None and post.campanie_id == campanie_id:
        s.delete(post)
        s.commit()
    return RedirectResponse(f"/tutoriate/campanii/{campanie_id}/posturi", status_code=303)


# ------------------------------------------------------------------ aplicatii


def _context_aplicatii(s: Session, campanie: CampanieTutoriate) -> dict:
    aplicatii = list(
        s.scalars(
            select(AplicatieTutor)
            .options(joinedload(AplicatieTutor.user))
            .where(AplicatieTutor.campanie_id == campanie.id)
            .order_by(AplicatieTutor.creat_la)
        )
    )
    posturi = {p.id: p for p in s.scalars(select(PosturiDeschise).where(PosturiDeschise.campanie_id == campanie.id))}
    return {"aplicatii": aplicatii, "posturi_dict": posturi}


@router.post("/tutoriate/campanii/{campanie_id}/aplicatii/{aplicatie_id}/stare")
def schimba_stare_aplicatie(
    request: Request,
    campanie_id: int,
    aplicatie_id: int,
    stare: str = Form(...),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    _campania(s, campanie_id)
    aplicatie = s.get(AplicatieTutor, aplicatie_id)
    if stare not in ("acceptata", "respinsa", "aplicata") or not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Stare sau token invalid.")
    if aplicatie is not None and aplicatie.campanie_id == campanie_id:
        aplicatie.stare = stare
        s.commit()
    return RedirectResponse(f"/tutoriate/campanii/{campanie_id}/aplicatii", status_code=303)


# ------------------------------------------------------------------ echipe


def _context_echipe(s: Session, campanie: CampanieTutoriate) -> dict:
    echipe = list(
        s.scalars(
            select(EchipaTutoriat)
            .options(
                joinedload(EchipaTutoriat.post),
                selectinload(EchipaTutoriat.membri).joinedload(EchipaTutoriatMembru.user),
            )
            .where(EchipaTutoriat.campanie_id == campanie.id)
        ).unique()
    )
    acceptati = list(
        s.scalars(
            select(AplicatieTutor)
            .options(joinedload(AplicatieTutor.user))
            .where(AplicatieTutor.campanie_id == campanie.id, AplicatieTutor.stare == "acceptata")
        )
    )
    posturi = list(
        s.scalars(select(PosturiDeschise).where(PosturiDeschise.campanie_id == campanie.id).order_by(PosturiDeschise.materie))
    )
    return {"echipe": echipe, "acceptati": acceptati, "posturi": posturi}


@router.post("/tutoriate/campanii/{campanie_id}/echipe")
def formeaza_echipa(
    request: Request,
    campanie_id: int,
    post_id: int = Form(...),
    membri: list[int] = Form([]),
    principal: int = Form(0),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    campanie = _campania(s, campanie_id)
    post = s.get(PosturiDeschise, post_id)
    if not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Token CSRF invalid.")
    if post is None or post.campanie_id != campanie.id:
        raise HTTPException(status_code=400, detail="Post invalid.")
    if not (campanie.min_tutori_echipa <= len(membri) <= campanie.max_tutori_echipa):
        raise HTTPException(status_code=400, detail="Număr de membri în afara limitelor.")
    echipa = EchipaTutoriat(
        campanie_id=campanie.id, post_id=post.id, creat_de=responsabil.nume, creat_la=datetime.now()
    )
    for ordine, user_id in enumerate(membri):
        echipa.membri.append(
            EchipaTutoriatMembru(user_id=user_id, principal=(user_id == principal), ordine=ordine)
        )
    s.add(echipa)
    s.commit()
    return RedirectResponse(f"/tutoriate/campanii/{campanie_id}/echipe", status_code=303)


# ------------------------------------------------------------------ planificare


def _context_planificare(s: Session, campanie: CampanieTutoriate) -> dict:
    echipe = list(
        s.scalars(
            select(EchipaTutoriat)
            .options(
                joinedload(EchipaTutoriat.post),
                selectinload(EchipaTutoriat.membri).joinedload(EchipaTutoriatMembru.user),
            )
            .where(EchipaTutoriat.campanie_id == campanie.id)
        ).unique()
    )
    sesiuni = list(
        s.scalars(
            select(SesiuneTutoriat)
            .options(joinedload(SesiuneTutoriat.echipa), joinedload(SesiuneTutoriat.post), joinedload(SesiuneTutoriat.sala))
            .where(SesiuneTutoriat.campanie_id == campanie.id)
            .order_by(SesiuneTutoriat.creat_la)
        )
    )
    sali = list(
        s.scalars(
            select(Sala)
            .where(Sala.tip == "fizica")
            .order_by(Sala.doar_tutoriate, Sala.nume)
        )
    )
    return {"echipe_plan": echipe, "sesiuni": sesiuni, "sali": sali}


@router.get("/tutoriate/campanii/{campanie_id}/propuneri", response_class=HTMLResponse)
def propuneri(
    request: Request,
    campanie_id: int,
    echipa_id: int = Query(...),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> HTMLResponse:
    """Ruleaza planificatorul pentru o echipa si afiseaza propunerile (htmx partial)."""
    campanie = _campania(s, campanie_id)
    echipa = s.scalars(
        select(EchipaTutoriat)
        .options(
            joinedload(EchipaTutoriat.post),
            selectinload(EchipaTutoriat.membri).joinedload(EchipaTutoriatMembru.user),
        )
        .where(EchipaTutoriat.id == echipa_id, EchipaTutoriat.campanie_id == campanie.id)
    ).unique().one_or_none()
    if echipa is None:
        raise HTTPException(status_code=404, detail="Echipa nu există.")

    orar = perioada_implicita(s)
    public = ocupare_public(s, orar, echipa.post) if orar is not None else None
    if public is None or public.populatie == 0:
        return templates.TemplateResponse(
            request=request,
            name="_tut_propuneri.html",
            context={"echipa": echipa, "campanie": campanie, "seturi": [], "populatie": 0},
        )

    # fezabilitate: toti tutorii liberi (disponibilitate + fara ore proprii)
    aplicatii = {
        a.user_id: a
        for a in s.scalars(
            select(AplicatieTutor).where(
                AplicatieTutor.campanie_id == campanie.id, AplicatieTutor.user_id.in_([m.user_id for m in echipa.membri])
            )
        )
    }
    disponibilitati = {uid: disponibilitate_tutor(a) for uid, a in aplicatii.items()}
    user_ids = [m.user_id for m in echipa.membri]
    fezabile: set[tuple[str, int]] = set()
    durata = campanie.durata_sesiune
    for zi in ZILE:
        for h in range(ORA_MIN, ORA_MAX - durata + 1):
            if tutori_liberi_slot(
                s, user_ids=user_ids, zi=zi, inceput=time(h), sfarsit=time(h + durata),
                perioada=orar, disponibilitati=disponibilitati,
            ):
                fezabile.add((zi, h))

    seturi = planifica(
        public.ocupare,
        public.populatie,
        durata=durata,
        max_sesiuni=min(3, campanie.max_tutori_echipa or 3),
        indulgenta=campanie.indulgenta,
        dominante=public.dominante,
        fezabile=fezabile,
    )
    return templates.TemplateResponse(
        request=request,
        name="_tut_propuneri.html",
        context={
            "echipa": echipa,
            "campanie": campanie,
            "seturi": seturi,
            "populatie": public.populatie,
            "zile_ro": ZILE_RO,
        },
    )


@router.post("/tutoriate/campanii/{campanie_id}/sesiuni")
def salveaza_propuneri(
    request: Request,
    campanie_id: int,
    echipa_id: int = Form(...),
    sloturi: list[str] = Form([]),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    """Salveaza propunerile alese ca `SesiuneTutoriat` (stare `propusa`), fara sala inca."""
    campanie = _campania(s, campanie_id)
    echipa = s.get(EchipaTutoriat, echipa_id)
    if not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Token CSRF invalid.")
    if echipa is None or echipa.campanie_id != campanie.id:
        raise HTTPException(status_code=400, detail="Echipă invalidă.")
    durata = campanie.durata_sesiune
    for slot in sloturi:
        # format "Zi:HH"
        if ":" not in slot:
            continue
        zi, _, h_text = slot.partition(":")
        if zi not in ZILE or not h_text.isdigit():
            continue
        h = int(h_text)
        s.add(
            SesiuneTutoriat(
                campanie_id=campanie.id,
                post_id=echipa.post_id,
                echipa_id=echipa.id,
                zi_saptamana=zi,
                ora_inceput=time(h),
                ora_sfarsit=time(h + durata),
                stare="propusa",
                creat_de=responsabil.nume,
                creat_la=datetime.now(),
            )
        )
    s.commit()
    return RedirectResponse(f"/tutoriate/campanii/{campanie_id}/planificare", status_code=303)


@router.post("/tutoriate/campanii/{campanie_id}/sesiuni/{sesiune_id}/confirma")
def confirma_sesiune(
    request: Request,
    campanie_id: int,
    sesiune_id: int,
    sala_id: int = Form(0),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    _campania(s, campanie_id)
    sesiune = s.scalars(
        select(SesiuneTutoriat)
        .options(joinedload(SesiuneTutoriat.post), joinedload(SesiuneTutoriat.echipa).selectinload(EchipaTutoriat.membri))
        .where(SesiuneTutoriat.id == sesiune_id, SesiuneTutoriat.campanie_id == campanie_id)
    ).unique().one_or_none()
    if not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Token CSRF invalid.")
    if sesiune is None:
        raise HTTPException(status_code=404, detail="Sesiunea nu există.")
    if sala_id:
        sesiune.sala_id = sala_id
    materializeaza_sesiune(s, sesiune, de_catre=responsabil.nume)
    s.commit()
    return RedirectResponse(f"/tutoriate/campanii/{campanie_id}/planificare", status_code=303)


@router.post("/tutoriate/campanii/{campanie_id}/sesiuni/{sesiune_id}/respinge")
def respinge_sesiune(
    request: Request,
    campanie_id: int,
    sesiune_id: int,
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    _campania(s, campanie_id)
    sesiune = s.get(SesiuneTutoriat, sesiune_id)
    if not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Token CSRF invalid.")
    if sesiune is not None and sesiune.campanie_id == campanie_id and sesiune.stare == "propusa":
        sesiune.stare = "respinsa"
        s.commit()
    return RedirectResponse(f"/tutoriate/campanii/{campanie_id}/planificare", status_code=303)


# ------------------------------------------------------------------ dashboard (tab generic, inregistrat ultimul ca sa nu umbreasca rutele specifice)


@router.get("/tutoriate/campanii/{campanie_id}/{tab}", response_class=HTMLResponse)
def campanie_tab(
    request: Request,
    campanie_id: int,
    tab: str,
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> HTMLResponse:
    campanie = _campania(s, campanie_id)
    tab = tab if tab in dict(TABURI_CAMPANIE) else "formular"
    ctx = _context_campanie(s, campanie, tab=tab)

    if tab == "formular":
        ctx["intrebari"] = _intrebari(campanie.formular)
    elif tab == "posturi":
        ctx["posturi"] = list(
            s.scalars(
                select(PosturiDeschise).where(PosturiDeschise.campanie_id == campanie.id).order_by(PosturiDeschise.materie)
            )
        )
        # Bifele de public (specializari/ani/serii) se iau din orarul real, nu din coduri
        # scrise de mana. `ales` e gol la un post nou; valorile vin din bifele din formular.
        ctx |= _optiuni_public(s, _orar_campanie(s, campanie))
        ctx["ales"] = {"specializari": [], "ani": [], "serii": []}
    elif tab == "aplicatii":
        ctx |= _context_aplicatii(s, campanie)
    elif tab == "echipe":
        ctx |= _context_echipe(s, campanie)
    elif tab == "planificare":
        ctx |= _context_planificare(s, campanie)

    return templates.TemplateResponse(request=request, name="tut_campanie.html", context=ctx)


