"""Panoul de administrare, pe /admin, si coada de verificare.

Panoul e pentru orice cont cu vreo permisiune (`auth.cere_panou`), nu doar pentru admin:
bara din stanga arata doar sectiunile la care contul are acces. Aici nu se face autentificare
-- exista un singur formular, `/login`; cine vine neautentificat e trimis acolo. Panoul listeaza
conturile (celor cu permisiunea `utilizatori`) si le poate seta rolul: student, voluntar,
profesor sau admin.

Coada de verificare: activitatile pe care extragerea nu le-a putut confirma
---------------------------------------------------------------------------

De ce e o pagina, nu un raport in log
-------------------------------------
Lexiconul spune *ca* nu recunoaste un camp, dar nu si care e raspunsul corect. Singurul
lucru care lamureste asta e imaginea. Deci pagina pune decupajul celulei -- taiat exact din
captura din care a fost citita, prin `SURSA_PAGINA` + `SURSA_BBOX` -- langa valorile propuse.
Fara decupaj, ecranul ar cere sa ai incredere fix acolo unde am spus ca nu avem.

Ordinea cozii
-------------
Intai activitatile cu campuri pe care vocabularul le-a respins, apoi cele cu increderea cea
mai mica. Scorul brut al recunoasterii nu e un criteriu bun singur: sta pe la 0.7-0.9 chiar
si pe citiri perfecte, deci ar ineca lista in celule bune.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from orar.db import corectii
from orar.db.coada import grupeaza
from orar.db.models import (
    PERMISIUNI,
    ROLURI_LOCALE,
    Corectie,
    Ora,
    Permisiune,
    Sala,
    Sesizare,
    SsoSesiune,
    Tutor,
    TutoriatTutor,
    User,
)
from orar.db.orare import fa_implicita, orare_vii
from orar.db.queries import profesori_de_ales
from orar.ingest.capturi import Sesiune
from orar.ingest.crosscheck import profesori_potriviti
from orar.web.auth import Cont, cere_admin, cere_panou, cere_permisiune, verifica_csrf
from orar.web.deps import context_saptamana, get_db, templates
from orar.web.routers.editare import (
    _liste,
    _uneste_profesori,
    _valideaza,
    _valori_ora,
    materii_din_orar,
)
from orar.web.routers.index import _profesori_pentru_selector
from orar.worker import incarcare

router = APIRouter(prefix="/admin", tags=["admin"])

#: Anul propus in formularul de incarcare cand baza nu are inca niciun orar.
AN_IMPLICIT = "2025-2026"

#: Unde stau capturile din care s-a citit. Configurabil pentru cand baza si imaginile
#: ajung pe masini diferite.
DIRECTOR_CAPTURI = Path("data/screenshots")


# ----------------------------------------------------------------- panoul


@router.get("", response_class=HTMLResponse)
def panou(
    request: Request,
    s: Session = Depends(get_db),
    admin: Cont = Depends(cere_panou),
) -> HTMLResponse:
    """Panoul de administrare: pentru toate rolurile care au primit vreo permisiune.

    Bara din stanga arata doar sectiunile la care contul are acces; panourile grele
    (statisticile, coada de verificare, structura anului) se incarca la prima deschidere,
    prin htmx, de pe adresele lor."""
    def numara(model, *conditii) -> int:  # noqa: ANN001, ANN002
        return s.scalar(select(func.count()).select_from(model).where(*conditii)) or 0

    context: dict[str, object] = {
        **context_saptamana(),
        "admin": admin,
        # numaratorile din panoul de intampinare, mereu prezente (ieftine)
        "nr_sesizari": numara(Sesizare, Sesizare.stare == "noua"),
        "nr_corectii": numara(Corectie),
        "nr_neaplicate": numara(Corectie, Corectie.aplicata.is_(False)),
        "nr_nesigure": numara(Ora, Ora.campuri_nesigure.is_not(None)),
    }

    if admin.poate("utilizatori"):
        utilizatori = list(
            s.scalars(select(User).options(joinedload(User.grupa)).order_by(func.lower(User.nume)))
        )
        context.update(
            {
                "utilizatori": utilizatori,
                "cereri": [u for u in utilizatori if u.cerere_profesor],
                "drepturi_sso": _drepturile_sso(s),
                "profesori": profesori_de_ales(s),
                "roluri": ROLURI_LOCALE,
                "permisiuni": PERMISIUNI,
                "permisiuni_acordate": _permisiunile_acordate(s),
                "tutori": _tutorii(s),
            }
        )

    if admin.poate("orar_profesori"):
        context["profesori_orar"] = _profesori_pentru_selector(s)

    if admin.e_admin:
        context["sali_fizice"] = list(
            s.scalars(select(Sala).where(Sala.tip == "fizica").order_by(Sala.nume))
        )

    if admin.poate("incarcare"):
        orare = orare_vii(s)
        implicit = orare[0].perioada if orare else None
        context.update(
            {
                "incarcare": incarcare.stare_curenta(),
                "orare": orare,
                "semestru_curent": implicit.semestru if implicit else 2,
                "an_curent": implicit.an_univ if implicit else AN_IMPLICIT,
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context=context,
    )


# ------------------------------------------------------- incarcarea unui orar


@router.post("/sali", response_model=None)
def salveaza_sali_blocate(
    request: Request,
    blocata: list[str] = Form(default=[]),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    admin: Cont = Depends(cere_admin),
) -> RedirectResponse:
    """Bifeaza salile care nu trebuie sa apara ca libere in tabul „Fereastra"."""
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/admin", status_code=303)
    alese = {int(x) for x in blocata if x.isdigit()}
    for sala in s.scalars(select(Sala).where(Sala.tip == "fizica")):
        sala.blocata = sala.id in alese
    s.commit()
    return RedirectResponse("/admin#sali-blocate", status_code=303)


@router.post("/orar", response_class=HTMLResponse, response_model=None)
def incarca_orar(
    request: Request,
    url_grupe: str = Form(""),
    url_profesori: str = Form(""),
    url_tutoriate: str = Form(""),
    semestru: str = Form(""),
    an_universitar: str = Form(""),
    csrf: str = Form(""),
    admin: Cont = Depends(cere_permisiune("incarcare")),
) -> RedirectResponse:
    """Porneste, in fundal, incarcarea unui orar dintr-un link -- vezi `worker.incarcare`."""
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/admin", status_code=303)

    url_grupe, url_profesori, url_tutoriate = (
        url_grupe.strip(),
        url_profesori.strip(),
        url_tutoriate.strip(),
    )
    eroare = (
        incarcare.valideaza_link(url_grupe)
        # orarul profesorilor e obligatoriu: fara el nu se face verificarea incrucisata
        or (
            incarcare.valideaza_link(url_profesori)
            if url_profesori
            else "Lipsește linkul orarului profesorilor."
        )
        # orarul tutoriatelor e optional, dar daca e dat, trebuie sa fie un link valid
        or (incarcare.valideaza_link(url_tutoriate) if url_tutoriate else None)
        or (None if semestru in ("1", "2") else "Semestrul e 1 sau 2.")
        or incarcare.valideaza_an(an_universitar)
    )
    if eroare is None:
        pornita = incarcare.porneste(
            incarcare.Cerere(
                url_grupe=url_grupe,
                url_profesori=url_profesori,
                url_tutoriate=url_tutoriate,
                semestru=int(semestru),
                an_universitar=an_universitar.strip(),
                de_catre=admin.nume,
            )
        )
        if not pornita:
            eroare = "O încărcare e deja în lucru. Așteaptă să se termine."
    if eroare:
        return RedirectResponse(f"/admin?eroare_orar={quote(eroare)}#orar", status_code=303)
    return RedirectResponse("/admin#orar", status_code=303)


@router.post("/orar/implicit")
def alege_implicit(
    request: Request,
    perioada: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_permisiune("incarcare")),
) -> RedirectResponse:
    """Alege orarul pe care il vede toata lumea cand nu alege altul."""
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/admin#orare", status_code=303)
    aleasa = next((o.perioada for o in orare_vii(s) if str(o.perioada.id) == perioada), None)
    if aleasa is None:
        raise HTTPException(404, "orarul nu exista")
    fa_implicita(s, aleasa)
    s.commit()
    return RedirectResponse("/admin?implicit=1#orare", status_code=303)


@router.get("/orar/stare", response_class=HTMLResponse)
def starea_incarcarii(request: Request, _admin: Cont = Depends(cere_permisiune("incarcare"))) -> HTMLResponse:
    """Doar caseta de stare, pentru reimprospatarea automata din panou."""
    return templates.TemplateResponse(
        request=request,
        name="_stare_incarcare.html",
        context={"incarcare": incarcare.stare_curenta()},
    )


@router.post("/utilizatori/{user_id}/rol")
def schimba_rolul(
    request: Request,
    user_id: int,
    rol: str = Form(""),
    profesor: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_permisiune("utilizatori")),
) -> RedirectResponse:
    """Seteaza rolul unui cont: student, voluntar, profesor sau admin. Rolul de profesor
    cere si profesorul: al cui orar devine "orarul meu" al contului."""
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/admin", status_code=303)
    if rol not in ROLURI_LOCALE:
        raise HTTPException(400, f"rol necunoscut: {rol!r}")
    user = s.get(User, user_id)
    if user is None:
        raise HTTPException(404, "contul nu exista")
    if rol == "profesor":
        profesor = profesor.strip()
        if profesor not in profesori_de_ales(s):
            mesaj = quote(f"Pentru rolul de profesor, alege din listă profesorul lui {user.nume}.")
            return RedirectResponse(f"/admin?eroare_rol={mesaj}#utilizatori", status_code=303)
        user.profesor = profesor
    else:
        user.profesor = None
    user.rol = rol
    # rolul a fost hotarat de un admin: cererea, daca era una, e tratata
    user.cerere_profesor = None
    s.commit()
    return RedirectResponse("/admin?salvat=1#utilizatori", status_code=303)


def _drepturile_sso(s: Session) -> dict[int, tuple[bool, frozenset[str], frozenset[str]]]:
    """Ce spunea Keycloak, la ultima autentificare, despre fiecare cont: e admin (CDDD), ce
    permisiuni aduce (roluri de client + oficiu) si care e oficiul. Se schimba din Keycloak,
    nu de aici."""
    from orar.web import sso

    cfg = sso.configurare()
    rez: dict[int, tuple[bool, frozenset[str], frozenset[str]]] = {}
    for x in s.scalars(select(SsoSesiune).order_by(SsoSesiune.verificat_la)):
        roluri = sso.roluri_sesiunii(x)
        admin, permisiuni = sso.drepturi(roluri, cfg)
        rez[x.user_id] = (admin, permisiuni, sso.oficii(roluri))
    return rez


def _permisiunile_acordate(s: Session) -> dict[int, frozenset[str]]:
    """Permisiunile date de un admin in Orar (tabela PERMISIUNE), pe cont."""
    rez: dict[int, set[str]] = {}
    for user_id, cheie in s.execute(select(Permisiune.user_id, Permisiune.cheie)):
        rez.setdefault(user_id, set()).add(cheie)
    return {k: frozenset(v) for k, v in rez.items()}


def _tutorii(s: Session) -> set[int]:
    """Id-urile conturilor tutor (rolul „tutore"): asignare valabila in TUTOR sau membru in
    echipa unui tutoriat (TUTORIAT_TUTOR)."""
    from datetime import date

    azi = date.today()
    ids = {
        user_id
        for (user_id,) in s.execute(
            select(Tutor.user_id).where(
                Tutor.user_id.is_not(None), Tutor.valabil_de_la <= azi, Tutor.valabil_pana_la >= azi
            )
        )
    }
    ids |= {
        user_id
        for (user_id,) in s.execute(
            select(TutoriatTutor.user_id).where(TutoriatTutor.user_id.is_not(None))
        )
    }
    return ids


@router.post("/utilizatori/{user_id}/cerere")
def trateaza_cererea(
    request: Request,
    user_id: int,
    actiune: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_permisiune("utilizatori")),
) -> RedirectResponse:
    """Aproba sau respinge cererea unui cont de a primi rolul de profesor."""
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/admin", status_code=303)
    user = s.get(User, user_id)
    if user is None:
        raise HTTPException(404, "contul nu exista")
    if actiune == "aproba" and user.cerere_profesor in profesori_de_ales(s):
        user.rol, user.profesor = "profesor", user.cerere_profesor
    user.cerere_profesor = None
    s.commit()
    return RedirectResponse("/admin?salvat=1#utilizatori", status_code=303)


@router.post("/utilizatori/{user_id}/permisiuni")
def salveaza_permisiunile(
    request: Request,
    user_id: int,
    chei: list[str] = Form([]),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    admin: Cont = Depends(cere_permisiune("utilizatori")),
) -> RedirectResponse:
    """Seteaza permisiunile acordate unui cont, in Orar. Ce vine din SSO (rol de client,
    oficiu) ramane neschimbat: aici adminul doar adauga sau retrage peste el."""
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/admin", status_code=303)
    user = s.get(User, user_id)
    if user is None:
        raise HTTPException(404, "contul nu exista")
    valide = {c for c in chei if c in dict(PERMISIUNI)}
    existente = list(s.scalars(select(Permisiune).where(Permisiune.user_id == user.id)))
    pentru = {p.cheie: p for p in existente}
    for cheie in valide - set(pentru):
        s.add(Permisiune(user=user, cheie=cheie, acordat_de=admin.nume, acordat_la=datetime.now()))
    for cheie in set(pentru) - valide:
        s.delete(pentru[cheie])
    s.commit()
    return RedirectResponse("/admin?salvat=1#utilizatori", status_code=303)


# ------------------------------------------------------- coada de verificare


@router.get("/review", response_class=HTMLResponse)
def review(
    request: Request,
    sesiune: str = Query(
        "", description="directorul sesiunii de incarcare; implicit cea mai recenta"
    ),
    prag: float = Query(0.75, ge=0.0, le=1.0),
    limita: int = Query(100, ge=1, le=500),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_permisiune("review")),
) -> HTMLResponse:
    """Activitatile de verificat, pe sesiuni de incarcare -- cea mai recenta intai -- si, in
    fiecare, pe paginile din care au fost citite."""
    toate = list(
        s.scalars(
            select(Ora)
            .where(Ora.sursa_bbox.is_not(None))
            .where((Ora.campuri_nesigure.is_not(None)) | (Ora.confidence < prag))
        )
    )
    # sesiunile, cu cate activitati are fiecare de verificat
    pe_sesiuni: dict[str, list[Ora]] = {}
    for o in toate:
        pe_sesiuni.setdefault(Sesiune.din_sursa(o.sursa_pagina or "").director, []).append(o)
    sesiuni = sorted(
        (Sesiune.din_sursa(f"{d}/x") if d else Sesiune(director="") for d in pe_sesiuni),
        key=lambda x: (x.moment is None, -(x.moment.timestamp() if x.moment else 0), x.director),
    )
    aleasa = next((x for x in sesiuni if x.director == sesiune), sesiuni[0] if sesiuni else None)

    # in sesiunea aleasa: pe pagini, in ordinea lor; in pagina, cele mai indoielnice intai
    candidate = sorted(
        pe_sesiuni.get(aleasa.director, []) if aleasa else [],
        key=lambda o: (o.sursa_pagina or "", o.campuri_nesigure is None, o.confidence or 0),
    )[:limita]
    # aceeasi activitate citita de pe mai multe pagini se verifica o data: vezi db/coada.py
    dosare = grupeaza(s, candidate)
    pagini: dict[str, list] = {}
    for d in dosare:
        pagini.setdefault(d.principal.sursa_pagina or "", []).append(d)
    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={
            **context_saptamana(s=s),
            "ore": candidate,
            "pagini": pagini,
            "sesiuni": [(x, len(pe_sesiuni[x.director])) for x in sesiuni],
            "aleasa": aleasa,
            # paginile ale caror imagini nu mai exista (suprascrise de o incarcare veche)
            "fara_imagine": {
                o.sursa_pagina for o in candidate if _gaseste_captura(o.sursa_pagina or "") is None
            },
            "nr_dosare": len(dosare),
            "prag": prag,
            "limita": limita,
            # propunerile din campuri si adresa la care ne intoarcem dupa o rezolvare
            **_liste(s),
            # materiile propuse fiecarei activitati: doar ale orarului ei
            "materii_pe_orar": {
                pid: materii_din_orar(s, pid) for pid in {o.perioada_id for o in candidate}
            },
            # ce spune orarul profesorilor despre fiecare activitate din coada
            "propuneri": profesori_potriviti(s, candidate),
            "inapoi": f"/admin/review?sesiune={aleasa.director if aleasa else ''}&prag={prag}&limita={limita}",
            "total_nesigure": s.scalar(
                select(func.count()).select_from(Ora).where(Ora.campuri_nesigure.is_not(None))
            ),
            "total_cu_provenienta": s.scalar(
                select(func.count()).select_from(Ora).where(Ora.sursa_bbox.is_not(None))
            ),
            "director_lipsa": not DIRECTOR_CAPTURI.is_dir(),
        },
    )


@router.post("/review/toate")
def confirma_toate(
    request: Request,
    sesiune: str = Form(""),
    prag: float = Form(0.75),
    limita: int = Form(100),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    admin: Cont = Depends(cere_permisiune("editare")),
) -> RedirectResponse:
    """Confirma deodata tot ce e in coada, cu valorile propuse de citirile sigure.

    Doar dosarele care au o propunere completa (materia nu e goala) se confirma; cele care
    cer o mana de om raman in coada. Ca la confirmarea pe rand, ce se confirma ramane si
    dupa incarcarea unui orar nou."""
    inapoi = f"/admin/review?sesiune={sesiune}&prag={prag}&limita={limita}"
    if not verifica_csrf(request, csrf):
        return RedirectResponse(f"{inapoi}&eroare={quote('Formularul a expirat. Încearcă din nou.')}", status_code=303)

    # Aceeasi coada pe care o afiseaza pagina: sesiunea aleasa, pragul si limita curente.
    toate = list(
        s.scalars(
            select(Ora)
            .where(Ora.sursa_bbox.is_not(None))
            .where((Ora.campuri_nesigure.is_not(None)) | (Ora.confidence < prag))
        )
    )
    pe_sesiuni: dict[str, list[Ora]] = {}
    for o in toate:
        pe_sesiuni.setdefault(Sesiune.din_sursa(o.sursa_pagina or "").director, []).append(o)
    sesiuni = sorted(
        (Sesiune.din_sursa(f"{d}/x") if d else Sesiune(director="") for d in pe_sesiuni),
        key=lambda x: (x.moment is None, -(x.moment.timestamp() if x.moment else 0), x.director),
    )
    aleasa = next((x for x in sesiuni if x.director == sesiune), sesiuni[0] if sesiuni else None)
    candidate = sorted(
        pe_sesiuni.get(aleasa.director, []) if aleasa else [],
        key=lambda o: (o.sursa_pagina or "", o.campuri_nesigure is None, o.confidence or 0),
    )[:limita]

    confirmate = 0
    ramase = 0
    for d in grupeaza(s, candidate):
        p = d.propunere()
        if not p["materie"].strip():
            ramase += len(d.membri)
            continue
        profesori = _uneste_profesori([p["profesor"]])
        for o in d.membri:
            valori = {
                **_valori_ora(o),
                "materie": p["materie"],
                "profesor": profesori,
                "sala": p["sala"],
                "saptamani": p["saptamani"],
            }
            v, _mesaj = _valideaza(s, valori)
            if v is None:
                ramase += 1
                continue
            corectii.modifica(s, o, v, de_catre=admin.nume)
            corectii.confirma(s, o, de_catre=admin.nume)
            confirmate += 1
    s.commit()
    mesaj = "" if ramase == 0 else f"&ramase={ramase}"
    return RedirectResponse(f"{inapoi}&toate={confirmate}{mesaj}", status_code=303)


def _gaseste_captura(sursa: str) -> Path | None:
    """Imaginea din care s-a citit activitatea.

    `SURSA_PAGINA` tine calea relativa la radacina capturilor, cu directorul sesiunii de
    incarcare (`2026-2027-sem1-grupe-.../pag_084.png`). Randurile foarte vechi au doar numele
    fisierului: pentru ele mai cautam o data in subdirectoare. Pentru celelalte nu -- o pagina
    cu acelasi nume dintr-o alta sesiune ar fi un decupaj gresit, nu unul lipsa.
    """
    cale = DIRECTOR_CAPTURI / sursa
    if cale.is_file():
        return cale
    if "/" in sursa:
        return None
    return next((p for p in DIRECTOR_CAPTURI.rglob(sursa) if p.is_file()), None)


@router.get("/decupaj/{ora_id}")
def decupaj(
    ora_id: int, s: Session = Depends(get_db), _admin: Cont = Depends(cere_permisiune("review"))
) -> Response:
    """Decupajul celulei din care a fost citita activitatea."""
    ora = s.get(Ora, ora_id)
    if ora is None or not ora.sursa_bbox or not ora.sursa_pagina:
        raise HTTPException(404, "activitatea nu are provenienta pastrata")

    cale = _gaseste_captura(ora.sursa_pagina)
    if cale is None:
        raise HTTPException(
            404,
            f"captura {ora.sursa_pagina} nu mai e pe disc; ruleaza din nou `orar sincronizeaza`",
        )
    try:
        cutie = tuple(int(v) for v in ora.sursa_bbox.split(","))
    except ValueError as e:
        raise HTTPException(500, f"bbox stricat: {ora.sursa_bbox!r}") from e
    if len(cutie) != 4:
        raise HTTPException(500, f"bbox stricat: {ora.sursa_bbox!r}")

    from PIL import Image

    tampon = io.BytesIO()
    with Image.open(cale) as im:
        im.crop(cutie).save(tampon, format="PNG")
    return Response(tampon.getvalue(), media_type="image/png")
