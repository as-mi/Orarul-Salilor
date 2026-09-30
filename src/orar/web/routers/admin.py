"""Panoul de administrare, pe /admin, si coada de verificare.

Tot ce e aici cere un cont cu rol de admin (`auth.cere_admin`): adminul principal, din
mediu, sau un cont din USER ridicat la admin. Aici nu se face autentificare -- exista un
singur formular, `/login`; cine vine neautentificat e trimis acolo. Panoul listeaza conturile si le poate seta
rolul: student, voluntar, profesor sau admin.

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
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from orar.db.models import ROLURI, Corectie, Ora, Sesizare, User
from orar.db.orare import fa_implicita, orare_vii
from orar.db.queries import profesori_de_ales
from orar.ingest.crosscheck import profesori_potriviti
from orar.web.auth import Cont, cere_admin, verifica_csrf
from orar.web.deps import context_saptamana, get_db, templates
from orar.web.routers.editare import _liste
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
    admin: Cont = Depends(cere_admin),
) -> HTMLResponse:
    """Utilizatorii si rolurile lor."""
    utilizatori = list(
        s.scalars(select(User).options(joinedload(User.grupa)).order_by(func.lower(User.nume)))
    )
    orare = orare_vii(s)
    implicit = orare[0].perioada if orare else None

    def numara(model, *conditii) -> int:  # noqa: ANN001, ANN002
        return s.scalar(select(func.count()).select_from(model).where(*conditii)) or 0

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            **context_saptamana(),
            "admin": admin,
            "utilizatori": utilizatori,
            "cereri": [u for u in utilizatori if u.cerere_profesor],
            "profesori": profesori_de_ales(s),
            "roluri": ROLURI,
            "incarcare": incarcare.stare_curenta(),
            "nr_sesizari": numara(Sesizare, Sesizare.stare == "noua"),
            "nr_corectii": numara(Corectie),
            "nr_neaplicate": numara(Corectie, Corectie.aplicata.is_(False)),
            "nr_nesigure": numara(Ora, Ora.campuri_nesigure.is_not(None)),
            "orare": orare,
            # valorile propuse in formular: semestrul si anul orarului implicit
            "semestru_curent": implicit.semestru if implicit else 2,
            "an_curent": implicit.an_univ if implicit else AN_IMPLICIT,
        },
    )


# ------------------------------------------------------- incarcarea unui orar


@router.post("/orar", response_class=HTMLResponse, response_model=None)
def incarca_orar(
    request: Request,
    url_grupe: str = Form(""),
    url_profesori: str = Form(""),
    semestru: str = Form(""),
    an_universitar: str = Form(""),
    csrf: str = Form(""),
    admin: Cont = Depends(cere_admin),
) -> RedirectResponse:
    """Porneste, in fundal, incarcarea unui orar dintr-un link -- vezi `worker.incarcare`."""
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/admin", status_code=303)

    url_grupe, url_profesori = url_grupe.strip(), url_profesori.strip()
    eroare = (
        incarcare.valideaza_link(url_grupe)
        # orarul profesorilor e obligatoriu: fara el nu se face verificarea incrucisata
        or (
            incarcare.valideaza_link(url_profesori)
            if url_profesori
            else "Lipsește linkul orarului profesorilor."
        )
        or (None if semestru in ("1", "2") else "Semestrul e 1 sau 2.")
        or incarcare.valideaza_an(an_universitar)
    )
    if eroare is None:
        pornita = incarcare.porneste(
            incarcare.Cerere(
                url_grupe=url_grupe,
                url_profesori=url_profesori,
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
    _admin: Cont = Depends(cere_admin),
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
def starea_incarcarii(request: Request, _admin: Cont = Depends(cere_admin)) -> HTMLResponse:
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
    _admin: Cont = Depends(cere_admin),
) -> RedirectResponse:
    """Seteaza rolul unui cont: student, voluntar, profesor sau admin. Rolul de profesor
    cere si profesorul: al cui orar devine "orarul meu" al contului."""
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/admin", status_code=303)
    if rol not in ROLURI:
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


@router.post("/utilizatori/{user_id}/cerere")
def trateaza_cererea(
    request: Request,
    user_id: int,
    actiune: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
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


# ------------------------------------------------------- coada de verificare


@router.get("/review", response_class=HTMLResponse)
def review(
    request: Request,
    prag: float = Query(0.75, ge=0.0, le=1.0),
    limita: int = Query(100, ge=1, le=500),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> HTMLResponse:
    """Activitatile de verificat, cele mai indoielnice intai."""
    candidate = list(
        s.scalars(
            select(Ora)
            .where(Ora.sursa_bbox.is_not(None))
            .where((Ora.campuri_nesigure.is_not(None)) | (Ora.confidence < prag))
            .order_by(Ora.campuri_nesigure.is_(None), Ora.confidence)
            .limit(limita)
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={
            **context_saptamana(s=s),
            "ore": candidate,
            "prag": prag,
            # propunerile din campuri si adresa la care ne intoarcem dupa o rezolvare
            **_liste(s),
            # ce spune orarul profesorilor despre fiecare activitate din coada
            "propuneri": profesori_potriviti(s, candidate),
            "inapoi": f"/admin/review?prag={prag}&limita={limita}",
            "total_nesigure": s.scalar(
                select(func.count()).select_from(Ora).where(Ora.campuri_nesigure.is_not(None))
            ),
            "total_cu_provenienta": s.scalar(
                select(func.count()).select_from(Ora).where(Ora.sursa_bbox.is_not(None))
            ),
            "director_lipsa": not DIRECTOR_CAPTURI.is_dir(),
        },
    )


def _gaseste_captura(sursa: str) -> Path | None:
    """Imaginea din care s-a citit activitatea.

    `SURSA_PAGINA` tine calea relativa la radacina capturilor (`sem2-grupe/pag_016.png`),
    dar randurile scrise inainte de asta au doar numele fisierului -- pentru ele mai cautam
    o data in subdirectoare, ca sa nu ramana fara decupaj dupa o actualizare.
    """
    cale = DIRECTOR_CAPTURI / sursa
    if cale.is_file():
        return cale
    nume = Path(sursa).name
    return next((p for p in DIRECTOR_CAPTURI.rglob(nume) if p.is_file()), None)


@router.get("/decupaj/{ora_id}")
def decupaj(
    ora_id: int, s: Session = Depends(get_db), _admin: Cont = Depends(cere_admin)
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
