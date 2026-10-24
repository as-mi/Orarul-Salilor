"""Inregistrare, autentificare, grupa contului si "Orarul meu".

La ce foloseste contul
----------------------
Sa-ti gasesti orarul la fel de pe orice dispozitiv. Iti alegi grupa, iar `/orarul-meu` te
duce la orarul ei, unde semigrupa si optionalele/facultativele alese se tin **in cont**, nu
in cookie-ul browserului. Restul aplicatiei ramane public -- nu ceri cont ca sa vezi o sala
libera.

Adminii nu au "orarul meu": dupa autentificare ajung in panoul de pe /admin.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import httpx
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from orar.db.conturi import (
    grupa_in_orar,
    grupe_de_ales,
    orarul_contului,
    reseteaza_grupele_daca_e_cazul,
)
from orar.db.models import Grupa, SsoSesiune, Tutor, User
from orar.db.orare import orare_vii, perioada_implicita
from orar.db.queries import gaseste_profesor, profesori_de_ales
from orar.db.tutoriate import tutoriatele_tutorului
from orar.web import sso
from orar.web.auth import (
    Cont,
    autentifica,
    cont_curent,
    deconecteaza,
    logheaza,
    sesiunea_sso,
    verifica_csrf,
)
from orar.web.deps import context_saptamana, get_db, templates

router = APIRouter(tags=["cont"])
log = logging.getLogger(__name__)

#: Unde se intoarce cineva dupa SSO; tinut in sesiune pe durata drumului prin Keycloak.
_CHEIE_INAPOI = "sso_inapoi"


def _pagina(request: Request, sablon: str, s: Session, **extra) -> HTMLResponse:  # noqa: ANN003
    """Randeaza un sablon de cont. `sablon`, nu `nume`: `nume` e un camp din formular."""
    stare = extra.pop("status_code", 200)
    return templates.TemplateResponse(
        request=request,
        name=sablon,
        context={**context_saptamana(s=s), **extra},
        status_code=stare,
    )


def _inapoi_sigur(inapoi: str) -> str | None:
    """Dupa autentificare ne intoarcem doar intr-o pagina a sitului."""
    return inapoi if inapoi.startswith("/") and "//" not in inapoi and "\\" not in inapoi else None


def _acasa(cont: Cont) -> str:
    """Unde ajunge un cont dupa autentificare: adminul in panou, ceilalti la orarul lor."""
    return "/admin" if cont.e_admin else "/orarul-meu"


def _grupe_alegibile(s: Session, user: User | None = None) -> list[Grupa]:
    """Grupele din care se poate alege: ale orarului pe care il urmeaza contul (pentru un
    cont nou, orarul implicit al sitului) -- vezi `db/conturi.py`."""
    return grupe_de_ales(s, orarul_contului(s, user))


def _grupa_valida(s: Session, grupa_id: str, user: User | None = None) -> Grupa | None:
    """Grupa, daca id-ul e chiar al unei grupe din care se poate alege; altfel un `<option>`
    fabricat ar lega contul de orice nod din arbore, sau de o grupa din alt an."""
    if not grupa_id.isdigit():
        return None
    g = s.get(Grupa, int(grupa_id))
    return g if g is not None and g in _grupe_alegibile(s, user) else None


# ------------------------------------------------------------------ intrare
#
# Conturile intra prin SSO-ul ASMI (`web/sso.py`): /login si /signup trimit la Keycloak, care
# se intoarce pe /auth/callback. Formularul cu parola ramane doar pentru adminul de urgenta
# din mediu (`/login?admin=1`), si e singurul cand SSO-ul nu e configurat.


def _url_callback(request: Request) -> str:
    return str(request.url_for("callback_sso"))


async def _spre_sso(request: Request, inapoi: str, **extra: str) -> RedirectResponse | None:
    f = sso.furnizor()
    if f is None:
        return None
    request.session[_CHEIE_INAPOI] = _inapoi_sigur(inapoi) or ""
    return await f.redirect(request, _url_callback(request), **extra)


@router.get("/login", response_class=HTMLResponse, response_model=None)
async def formular_intrare(
    request: Request,
    inapoi: str = Query(""),
    admin: bool = Query(False, description="formularul adminului de urgenta"),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> HTMLResponse | RedirectResponse:
    if cont is not None:
        return RedirectResponse(_inapoi_sigur(inapoi) or _acasa(cont), status_code=303)
    if not admin and (spre := await _spre_sso(request, inapoi)):
        return spre
    return _pagina(
        request,
        "intrare.html",
        s,
        inapoi=_inapoi_sigur(inapoi) or "",
        sso_activ=sso.furnizor() is not None,
    )


@router.post("/login", response_model=None)
def intra(
    request: Request,
    email: str = Form(""),
    parola: str = Form(""),
    inapoi: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Intrarea adminului de urgenta, din mediu."""
    inapoi = _inapoi_sigur(inapoi) or ""

    def eroare(mesaj: str, stare: int) -> HTMLResponse:
        return _pagina(
            request, "intrare.html", s, eroare=mesaj, email=email, inapoi=inapoi,
            sso_activ=sso.furnizor() is not None, status_code=stare,
        )  # fmt: skip

    if not verifica_csrf(request, csrf):
        return eroare("Formularul a expirat. Încearcă din nou.", 400)
    cont = autentifica(email, parola)
    if cont is None:
        return eroare("Nume sau parolă greșită.", 401)
    logheaza(request, cont)
    return RedirectResponse(inapoi or _acasa(cont), status_code=303)


@router.get("/signup", response_model=None)
async def inregistrare(request: Request, inapoi: str = Query("")) -> RedirectResponse:
    """Contul se face in SSO-ul ASMI; Keycloak deschide direct pagina de inregistrare."""
    spre = await _spre_sso(request, inapoi or "/account", prompt="create")
    return spre or RedirectResponse("/login", status_code=303)


@router.get("/auth/callback", name="callback_sso", response_model=None)
async def callback_sso(
    request: Request, s: Session = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    """Keycloak se intoarce aici cu codul de autorizare. Contul se leaga sau se creeaza,
    iar sesiunea SSO se tine pe server."""
    f = sso.furnizor()
    if f is None:
        return RedirectResponse("/login", status_code=303)
    inapoi = _inapoi_sigur(request.session.get(_CHEIE_INAPOI, "")) or ""
    if request.query_params.get("error"):
        # a renuntat pe pagina Keycloak, sau Keycloak a refuzat
        return RedirectResponse(inapoi or "/", status_code=303)
    try:
        tokenuri = await f.schimba_codul(request)
        claims = sso.verifica_access_token(f, tokenuri["access_token"])
        identitate = {**claims, **(tokenuri.get("userinfo") or {})}
        user = sso.leaga_sau_creeaza(s, identitate)
        sesiune = sso.deschide_sesiunea(s, user, tokenuri, claims, f.cfg)
        s.commit()
    except (sso.EroareSso, KeyError) as e:
        s.rollback()
        log.warning("Autentificare SSO refuzata: %s", e)
        return _pagina(
            request, "intrare.html", s, sso_activ=True, status_code=400,
            eroare="Autentificarea prin contul ASMI n-a reușit. Încearcă din nou.",
        )  # fmt: skip
    logheaza(request, sesiune_sso=sesiune.id)
    admin, _ = sso.drepturi(sso.roluri_sesiunii(sesiune), f.cfg)
    if inapoi:
        return RedirectResponse(inapoi, status_code=303)
    if admin:
        return RedirectResponse("/admin", status_code=303)
    return RedirectResponse("/orarul-meu" if user.grupa_id else "/account?nou=1", status_code=303)


@router.post("/logout")
def iese(request: Request, csrf: str = Form(""), s: Session = Depends(get_db)) -> RedirectResponse:
    """Iese din Orar si, pentru un cont ASMI, si din SSO (deci din toate siturile ASMI)."""
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/", status_code=303)
    tinta = "/"
    if (sid := sesiunea_sso(request)) and (sesiune := s.get(SsoSesiune, sid)) is not None:
        f = sso.furnizor()
        if f is not None:
            try:
                tinta = f.url_logout(sesiune.id_token, str(request.base_url)) or "/"
            except httpx.HTTPError:
                tinta = "/"
        s.delete(sesiune)
        s.commit()
    deconecteaza(request)
    return RedirectResponse(tinta, status_code=303)


@router.post("/auth/backchannel-logout")
def logout_backchannel(logout_token: str = Form(""), s: Session = Depends(get_db)) -> Response:
    """Keycloak anunta ca o sesiune SSO s-a inchis (logout din alt sit ASMI, sau din
    consola). Fara CSRF: cererea vine de la Keycloak, iar autenticitatea o da semnatura."""
    f = sso.furnizor()
    if f is None:
        return Response(status_code=501)
    try:
        claims = sso.verifica_logout_token(f, logout_token)
    except sso.EroareSso as e:
        log.warning("Logout backchannel refuzat: %s", e)
        return Response(status_code=400)
    sso.inchide_dupa_logout_token(s, claims)
    return Response(status_code=200, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------ grupa si orarul meu


@router.get("/orarul-meu", response_model=None)
def orarul_meu(
    s: Session = Depends(get_db), cont: Cont | None = Depends(cont_curent)
) -> RedirectResponse:
    """Orarul grupei contului. Preferintele (semigrupa, optionale) le aplica pagina grupei,
    din cont -- vezi `routers/grupa.py`."""
    if cont is None:
        return RedirectResponse("/login?inapoi=/orarul-meu", status_code=303)
    if cont.e_admin or cont.user is None:
        return RedirectResponse("/admin", status_code=303)
    # un profesor isi vede orarul lui, nu al unei grupe
    if cont.user.rol == "profesor" and cont.user.profesor:
        profesor = gaseste_profesor(s, cont.user.profesor)
        if profesor is not None:
            return RedirectResponse(f"/profesor/{profesor.slug}", status_code=303)
    reseteaza_grupele_daca_e_cazul(s)
    user = cont.user
    if user.grupa is None:
        return RedirectResponse("/account", status_code=303)
    orar = orarul_contului(s, user)
    # orarul urmat e din alt an decat grupa: aceeasi grupa, in anul lui, cu preferintele pastrate
    grupa = grupa_in_orar(s, user.grupa, orar)
    if grupa is None:
        return RedirectResponse("/account?grupa_lipsa=1", status_code=303)
    if grupa is not user.grupa:
        user.grupa = grupa
        s.commit()
    implicit = perioada_implicita(s)
    q = (
        f"?perioada={orar.id}"
        if orar is not None and implicit is not None and orar.id != implicit.id
        else ""
    )
    return RedirectResponse(f"/grupa/{grupa.slug}{q}", status_code=303)


@router.get("/account", response_class=HTMLResponse, response_model=None)
def formular_cont(
    request: Request,
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> HTMLResponse | RedirectResponse:
    if cont is None:
        return RedirectResponse("/login?inapoi=/account", status_code=303)
    if cont.user is None:
        return RedirectResponse("/admin", status_code=303)
    reseteaza_grupele_daca_e_cazul(s)
    grupe = _grupe_alegibile(s, cont.user)
    # grupele care se potrivesc anului si specializarii din profilul ASMI, propuse primele
    potrivite: list[Grupa] = []
    if not cont.user.grupa_id and (sid := sesiunea_sso(request)):
        sesiune = s.get(SsoSesiune, sid)
        if sesiune is not None and sesiune.profil:
            potrivite = sso.grupe_potrivite(grupe, json.loads(sesiune.profil))
    cfg = sso.configurare()
    return _pagina(
        request,
        "cont.html",
        s,
        user=cont.user,
        grupe=grupe,
        potrivite=potrivite,
        profil_asmi=cfg.cont_asmi if cfg else None,
        orare=orare_vii(s),
        orar_urmat=orarul_contului(s, cont.user),
        profesori=profesori_de_ales(s),
        asignari=list(
            s.scalars(
                select(Tutor)
                .where(Tutor.user_id == cont.user.id, Tutor.valabil_pana_la >= date.today())
                .order_by(Tutor.valabil_pana_la)
            )
        ),
        tutoriatele_mele=tutoriatele_tutorului(s, cont.user),
    )


@router.post("/account/profesor")
def cere_rolul_de_profesor(
    request: Request,
    profesor: str = Form(""),
    renunta: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    """Cererea de a primi rolul de profesor. Rolul il da un admin, din panou: pana atunci
    contul ramane cum e, doar cu cererea notata."""
    if cont is None or cont.user is None:
        return RedirectResponse("/login", status_code=303)
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/account", status_code=303)
    if renunta:
        cont.user.cerere_profesor = None
    elif cont.user.rol != "profesor" and profesor in profesori_de_ales(s):
        cont.user.cerere_profesor = profesor
    else:
        return RedirectResponse("/account?eroare_profesor=1#profesor", status_code=303)
    s.commit()
    return RedirectResponse("/account#profesor", status_code=303)


@router.post("/account")
def salveaza_cont(
    request: Request,
    grupa_id: str = Form(""),
    orar: str = Form("", description="`automat` sau id-ul unui orar"),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    if cont is None or cont.user is None:
        return RedirectResponse("/login", status_code=303)
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/account", status_code=303)

    user = cont.user
    # Intai orarul: de el depind grupele din care se poate alege.
    if orar == "automat":
        user.orar_automat, user.orar_ales = True, None
    elif orar.isdigit():
        ales = next((o.perioada for o in orare_vii(s) if o.perioada.id == int(orar)), None)
        if ales is not None:
            user.orar_automat, user.orar_ales = False, ales
    # Grupa veche, mutata in anul orarului nou, nu e o grupa noua: preferintele raman.
    if user.grupa is not None:
        mutata = grupa_in_orar(s, user.grupa, orarul_contului(s, user))
        if mutata is not None:
            user.grupa = mutata

    noua = _grupa_valida(s, grupa_id, user)
    if (noua.id if noua else None) != user.grupa_id:
        # Preferintele erau ale grupei vechi: alta grupa are alte semigrupe si alte
        # optionale, deci nu le mostenim. Prin relatie, nu prin FK: altfel `user.grupa`
        # ramane obiectul vechi pentru cine il citeste in aceeasi sesiune.
        user.grupa = noua
        user.semigrupa = None
        user.ascunse = None
    s.commit()
    return RedirectResponse("/orarul-meu" if noua else "/account", status_code=303)
