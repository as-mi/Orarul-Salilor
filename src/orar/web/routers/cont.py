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

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orar.db.models import Grupa, User
from orar.db.queries import gaseste_profesor, profesori_de_ales
from orar.web.auth import (
    PAROLA_MINIM,
    Cont,
    autentifica,
    cont_curent,
    deconecteaza,
    hash_parola,
    logheaza,
    normalizeaza_email,
    verifica_csrf,
)
from orar.web.deps import context_saptamana, get_db, templates

router = APIRouter(tags=["cont"])


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


def _grupe_alegibile(s: Session) -> list[Grupa]:
    """Formatiunile la care poate fi cineva: grupe reale, nu serii, semigrupe sau pachete."""
    return list(s.scalars(select(Grupa).where(Grupa.tip == "grupa").order_by(Grupa.nume)))


def _grupa_valida(s: Session, grupa_id: str) -> Grupa | None:
    """Grupa, daca id-ul e chiar al unei grupe; altfel un `<option>` fabricat ar lega contul
    de orice nod din arbore."""
    if not grupa_id.isdigit():
        return None
    g = s.get(Grupa, int(grupa_id))
    return g if g is not None and g.tip == "grupa" else None


# ------------------------------------------------------------------ intrare


@router.get("/login", response_class=HTMLResponse, response_model=None)
def formular_intrare(
    request: Request,
    inapoi: str = Query(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> HTMLResponse | RedirectResponse:
    if cont is not None:
        return RedirectResponse(_inapoi_sigur(inapoi) or _acasa(cont), status_code=303)
    return _pagina(request, "intrare.html", s, inapoi=_inapoi_sigur(inapoi) or "")


@router.post("/login", response_model=None)
def intra(
    request: Request,
    email: str = Form(""),
    parola: str = Form(""),
    inapoi: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    inapoi = _inapoi_sigur(inapoi) or ""

    def eroare(mesaj: str, stare: int) -> HTMLResponse:
        return _pagina(
            request, "intrare.html", s, eroare=mesaj, email=email, inapoi=inapoi, status_code=stare
        )

    if not verifica_csrf(request, csrf):
        return eroare("Formularul a expirat. Încearcă din nou.", 400)
    cont = autentifica(s, email, parola)
    if cont is None:
        # Acelasi mesaj pentru cont inexistent si parola gresita.
        return eroare("Email sau parolă greșită.", 401)
    logheaza(request, cont)
    return RedirectResponse(inapoi or _acasa(cont), status_code=303)


@router.post("/logout")
def iese(request: Request, csrf: str = Form("")) -> RedirectResponse:
    if verifica_csrf(request, csrf):
        deconecteaza(request)
    return RedirectResponse("/", status_code=303)


# ------------------------------------------------------------- inregistrare


@router.get("/signup", response_class=HTMLResponse, response_model=None)
def formular_inregistrare(
    request: Request,
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> HTMLResponse | RedirectResponse:
    if cont is not None:
        return RedirectResponse(_acasa(cont), status_code=303)
    return _pagina(request, "inregistrare.html", s, grupe=_grupe_alegibile(s))


@router.post("/signup", response_model=None)
def inregistreaza(
    request: Request,
    nume: str = Form(""),
    email: str = Form(""),
    parola: str = Form(""),
    grupa_id: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    def inapoi(eroare: str, stare: int = 400) -> HTMLResponse:
        return _pagina(
            request,
            "inregistrare.html",
            s,
            grupe=_grupe_alegibile(s),
            eroare=eroare,
            nume=nume,
            email=email,
            grupa_id=grupa_id,
            status_code=stare,
        )

    if not verifica_csrf(request, csrf):
        return inapoi("Formularul a expirat. Încearcă din nou.")
    if not nume.strip():
        return inapoi("Numele nu poate fi gol.")
    if "@" not in email:
        return inapoi("Adresa de email nu pare validă.")
    if len(parola) < PAROLA_MINIM:
        return inapoi(f"Parola trebuie să aibă cel puțin {PAROLA_MINIM} caractere.")

    exista = s.scalar(select(User).where(func.lower(User.email) == normalizeaza_email(email)))
    if exista is not None:
        return inapoi("Există deja un cont cu adresa asta.", 409)

    user = User(
        nume=nume.strip(),
        email=normalizeaza_email(email),
        parola_hash=hash_parola(parola),
        grupa=_grupa_valida(s, grupa_id),
    )
    s.add(user)
    s.commit()
    logheaza(request, Cont(nume=user.nume, rol=user.rol, user=user))
    return RedirectResponse("/orarul-meu", status_code=303)


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
    if cont.user.grupa is None:
        return RedirectResponse("/account", status_code=303)
    return RedirectResponse(f"/grupa/{cont.user.grupa.slug}", status_code=303)


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
    return _pagina(
        request,
        "cont.html",
        s,
        user=cont.user,
        grupe=_grupe_alegibile(s),
        profesori=profesori_de_ales(s),
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
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    if cont is None or cont.user is None:
        return RedirectResponse("/login", status_code=303)
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/account", status_code=303)

    noua = _grupa_valida(s, grupa_id)
    if (noua.id if noua else None) != cont.user.grupa_id:
        # Preferintele erau ale orarului vechi: alta grupa are alte semigrupe si alte
        # optionale, deci nu le mostenim. Prin relatie, nu prin FK: altfel `user.grupa`
        # ramane obiectul vechi pentru cine il citeste in aceeasi sesiune.
        cont.user.grupa = noua
        cont.user.semigrupa = None
        cont.user.ascunse = None
        s.commit()
    return RedirectResponse("/orarul-meu" if noua else "/account", status_code=303)
