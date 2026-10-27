"""Abonarea la calendar: crearea tokenului, feed-ul ICS, pagina de ajutor si revocarea.

Crearea se face doar prin POST, cu CSRF: un GET ar putea fi declansat de prefetch-ul
browserului, de crawlere sau de antivirus si ar umple tabela de tokenuri. Feed-ul insa e
public -- tokenul de 256 de biti din URL e singura lui credentiala, iar clientul de calendar
nu are sesiune si nu poate trimite cookie-uri.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from orar.db.abonamente import (
    PreferinteAbonament,
    atinge,
    creeaza_sau_actualizeaza,
    dupa_token,
    preferinte_din_json,
    revoca,
)
from orar.db.conturi import grupa_in_orar, orarul_contului
from orar.db.models import AbonamentCalendar, Perioada
from orar.db.orare import perioada_implicita
from orar.db.queries import gaseste_grupa, gaseste_profesor
from orar.db.tutoriate import alese_din_cont
from orar.domain.weeks import an_universitar_al
from orar.web.abonamente import (
    COOKIE_CLIENT,
    client_din_cookie,
    domeniu_ics,
    linkuri,
    scrie_client,
    tinta_valida,
    token_nou,
)
from orar.web.afisare import ascunse_din_cont, ascunse_pe_pagina, semigrupe_salvate
from orar.web.auth import Cont, cont_curent, verifica_csrf
from orar.web.deps import get_db, templates
from orar.web.feed import intrari_grupa, intrari_profesor
from orar.web.ics import IntrareCalendar, scrie_calendar
from orar.web.routers.tutoriate import tutoriate_alese_din_cookie

router = APIRouter(tags=["abonamente"])

#: Ce face un buton din meniul „Exportă”.
TINTE = ("google", "webcal", "ajutor")
_RE_FISIER = re.compile(r"[^a-z0-9-]+")


def _semestru_implicit() -> int:
    luna = date.today().month
    return 1 if luna >= 9 or luna == 1 else 2


def _an_implicit(s: Session) -> str:
    perioada = perioada_implicita(s)
    return perioada.an_univ if perioada is not None else an_universitar_al(date.today())


def _orar_pentru(
    s: Session, an_univ: str | None, referinta: Perioada | None
) -> Perioada | None:
    """Orarul (an, semestru) pentru subiect, preferand ce urmeaza contul sau implicitul."""
    if referinta is not None and (an_univ is None or referinta.an_univ == an_univ):
        return referinta
    if an_univ is None:
        return perioada_implicita(s)
    implicita = perioada_implicita(s)
    if implicita is not None and implicita.an_univ == an_univ:
        return implicita
    return s.scalar(
        select(Perioada)
        .where(Perioada.an_univ == an_univ)
        .order_by(Perioada.semestru.desc())
    )


def _preferinte_cont(user) -> PreferinteAbonament:  # noqa: ANN001 -- models.User
    return PreferinteAbonament(
        semigrupa=user.semigrupa,
        ascunse=frozenset(ascunse_din_cont(user)),
        tutoriate=frozenset(alese_din_cont(user)),
    )


def _fisier(slug: str | None) -> str:
    curat = _RE_FISIER.sub("-", (slug or "").lower()).strip("-")
    return curat or "orar"


def _tinta(request: Request, token: str, target: str) -> str:
    if target == "google":
        return linkuri(request, token)["google"]
    if target == "webcal":
        return linkuri(request, token)["webcal"]
    return f"/calendar/{token}/ajutor"


def _e_proprietar(request: Request, cont: Cont | None, abonament: AbonamentCalendar) -> bool:
    if (
        abonament.user_id is not None
        and cont is not None
        and cont.user is not None
        and cont.user.id == abonament.user_id
    ):
        return True
    client = client_din_cookie(request)
    return bool(abonament.client and client and abonament.client == client)


# ------------------------------------------------------------------ crearea tokenului


@router.post("/calendar/abonament")
def creeaza_abonament(
    request: Request,
    csrf: str = Form(""),
    fel: str = Form(""),
    inapoi: str = Form(""),
    target: str = Form("ajutor"),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    if not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Token CSRF invalid.")
    tinta = tinta_valida(inapoi)
    if tinta is None or (fel and fel != tinta[0]):
        raise HTTPException(
            status_code=400, detail="Abonamentul se poate face doar de pe o pagină de orar."
        )
    fel_adresa, identificator = tinta
    target = target if target in TINTE else "ajutor"

    if fel_adresa == "grupa":
        grupa = gaseste_grupa(s, identificator)
        if grupa is None:
            raise HTTPException(status_code=404, detail="Formațiunea nu există.")
        al_meu = (
            cont is not None
            and cont.user is not None
            and not cont.e_admin
            and cont.user.grupa_id == grupa.id
        )
        if al_meu:
            perioada = orarul_contului(s, cont.user)
            efectiva = grupa_in_orar(s, grupa, perioada) or grupa
            abonament = creeaza_sau_actualizeaza(
                s,
                fel="grupa",
                mod="cont",
                user=cont.user,
                grupa_slug=efectiva.slug,
                an_univ=perioada.an_univ if perioada is not None else efectiva.an_universitar,
                semestru=perioada.semestru if perioada is not None else _semestru_implicit(),
                preferinte=_preferinte_cont(cont.user),
            )
            s.commit()
            return RedirectResponse(_tinta(request, abonament.token, target), status_code=303)

        client = client_din_cookie(request) or token_nou()
        perioada = _orar_pentru(s, grupa.an_universitar, perioada_implicita(s))
        preferinte = PreferinteAbonament(
            semigrupa=semigrupe_salvate(request).get(grupa.slug),
            ascunse=frozenset(ascunse_pe_pagina(request, grupa.slug)),
            tutoriate=frozenset(tutoriate_alese_din_cookie(request)),
        )
        abonament = creeaza_sau_actualizeaza(
            s,
            fel="grupa",
            mod="instantaneu",
            client=client,
            grupa_slug=grupa.slug,
            an_univ=perioada.an_univ if perioada is not None else grupa.an_universitar,
            semestru=perioada.semestru if perioada is not None else _semestru_implicit(),
            preferinte=preferinte,
        )
        raspuns = RedirectResponse(_tinta(request, abonament.token, target), status_code=303)
        scrie_client(raspuns, client)
        s.commit()
        return raspuns

    profesor = gaseste_profesor(s, identificator)
    if profesor is None:
        raise HTTPException(status_code=404, detail="Profesorul nu există.")
    if cont is None or cont.user is None:
        inapoi_login = quote(request.url.path, safe="/")
        raise HTTPException(status_code=303, headers={"Location": f"/login?inapoi={inapoi_login}"})
    if not (cont.user.rol == "profesor" and cont.user.profesor == profesor.nume):
        raise HTTPException(
            status_code=403,
            detail="Abonamentul la orarul unui profesor se poate face doar din contul lui.",
        )
    perioada = orarul_contului(s, cont.user)
    abonament = creeaza_sau_actualizeaza(
        s,
        fel="profesor",
        mod="cont",
        user=cont.user,
        profesor_nume=profesor.nume,
        semestru=perioada.semestru if perioada is not None else _semestru_implicit(),
        preferinte=PreferinteAbonament(),
    )
    s.commit()
    return RedirectResponse(_tinta(request, abonament.token, target), status_code=303)


# ------------------------------------------------------------------ feed-ul


def _continut(
    s: Session, abonament: AbonamentCalendar, request: Request
) -> tuple[str, str, list[IntrareCalendar], str] | None:
    """Calendarul abonamentului, sau None cand subiectul nu mai exista / nu mai are voie."""
    domeniu = domeniu_ics()
    if abonament.mod == "cont":
        user = abonament.user
        if user is None:
            return None
        perioada = orarul_contului(s, user)
        if abonament.fel == "grupa":
            if user.grupa is None:
                return None
            grupa = grupa_in_orar(s, user.grupa, perioada) or user.grupa
            an_univ = perioada.an_univ if perioada is not None else grupa.an_universitar
            semestru = perioada.semestru if perioada is not None else abonament.semestru
            intrari = intrari_grupa(
                s,
                grupa,
                an_univ=an_univ,
                semestru=semestru,
                preferinte=_preferinte_cont(user),
                domeniu=domeniu,
                tutor=user,
            )
            return (
                f"Orar · {grupa.nume}",
                f"Orarul formațiunii {grupa.nume}, semestrul {semestru}, {an_univ}. "
                "Se actualizează când se schimbă orarul sau alegerile din cont.",
                intrari,
                _fisier(grupa.slug),
            )
        # profesor: permisiunea se re-verifica la fiecare acces
        if user.rol != "profesor" or user.profesor != abonament.profesor_nume:
            return None
        an_univ = perioada.an_univ if perioada is not None else _an_implicit(s)
        semestru = perioada.semestru if perioada is not None else abonament.semestru
        nume = abonament.profesor_nume or ""
        intrari = intrari_profesor(
            s,
            nume,
            an_univ=an_univ,
            semestru=semestru,
            domeniu=domeniu,
            tutor=user,
        )
        return (
            f"Orar · {nume}",
            f"Orarul profesorului {nume}, semestrul {semestru}, {an_univ}.",
            intrari,
            _fisier(nume),
        )

    # instantaneu: preferintele inghetate in token
    preferinte = preferinte_din_json(abonament.optiuni)
    an_univ = abonament.an_univ or _an_implicit(s)
    semestru = abonament.semestru
    if abonament.fel == "grupa":
        grupa = gaseste_grupa(s, abonament.grupa_slug or "", an_universitar=an_univ)
        if grupa is None:
            return None
        intrari = intrari_grupa(
            s,
            grupa,
            an_univ=an_univ,
            semestru=semestru,
            preferinte=preferinte,
            domeniu=domeniu,
        )
        return (
            f"Orar · {grupa.nume}",
            f"Orarul formațiunii {grupa.nume}, semestrul {semestru}, {an_univ}. "
            "Abonament fără cont: alegerile sunt cele de la data abonării.",
            intrari,
            _fisier(grupa.slug),
        )
    nume = abonament.profesor_nume or ""
    if gaseste_profesor(s, nume) is None:
        return None
    intrari = intrari_profesor(s, nume, an_univ=an_univ, semestru=semestru, domeniu=domeniu)
    return (
        f"Orar · {nume}",
        f"Orarul profesorului {nume}, semestrul {semestru}, {an_univ}.",
        intrari,
        _fisier(nume),
    )


@router.get("/calendar/{token}.ics", response_class=Response, response_model=None)
def feed_calendar(request: Request, token: str, s: Session = Depends(get_db)) -> Response:
    abonament = dupa_token(s, token)
    if abonament is None:
        # Text simplu, nu pagina globala de 404: Google ar marca abonamentul ca invalid.
        return PlainTextResponse("Calendar inexistent.\n", status_code=404)
    if abonament.revocat_la is not None:
        return PlainTextResponse("Calendar revocat.\n", status_code=410)
    continut = _continut(s, abonament, request)
    if continut is None:
        return PlainTextResponse("Calendarul nu mai are un orar disponibil.\n", status_code=410)
    nume, descriere, intrari, fisier = continut
    corp = scrie_calendar(nume, descriere, intrari)
    atinge(s, abonament)
    s.commit()
    return Response(
        corp,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="{fisier}.ics"',
            "Cache-Control": "private, max-age=300",
            "X-Robots-Tag": "noindex, nofollow",
            "Referrer-Policy": "no-referrer",
        },
    )


# ------------------------------------------------------------------ pagina de ajutor


def _intoarcere(s: Session, abonament: AbonamentCalendar) -> str:
    if abonament.fel == "grupa" and abonament.grupa_slug:
        return f"/grupa/{abonament.grupa_slug}"
    if abonament.fel == "profesor" and abonament.profesor_nume:
        profesor = gaseste_profesor(s, abonament.profesor_nume)
        if profesor is not None:
            return f"/profesor/{profesor.slug}"
    return "/"


@router.get("/calendar/{token}/ajutor", response_class=HTMLResponse, response_model=None)
def ajutor_abonament(
    request: Request,
    token: str,
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> HTMLResponse:
    abonament = dupa_token(s, token)
    if abonament is None:
        raise HTTPException(status_code=404, detail="Abonamentul nu există.")
    if abonament.revocat_la is not None:
        raise HTTPException(status_code=410, detail="Abonamentul a fost revocat.")
    raspuns = templates.TemplateResponse(
        request=request,
        name="ajutor_abonament.html",
        context={
            "token": token,
            **linkuri(request, token),
            "nume": abonament.grupa_slug or abonament.profesor_nume or "orar",
            "proprietar": _e_proprietar(request, cont, abonament),
            "intoarcere": _intoarcere(s, abonament),
        },
    )
    raspuns.headers["X-Robots-Tag"] = "noindex, nofollow"
    raspuns.headers["Referrer-Policy"] = "no-referrer"
    raspuns.headers["Cache-Control"] = "private, max-age=60"
    return raspuns


@router.post("/calendar/{token}/revoca")
def revoca_abonament(
    request: Request,
    token: str,
    csrf: str = Form(""),
    inapoi: str = Form("/"),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    if not verifica_csrf(request, csrf):
        raise HTTPException(status_code=400, detail="Token CSRF invalid.")
    abonament = dupa_token(s, token)
    if abonament is None:
        raise HTTPException(status_code=404, detail="Abonamentul nu există.")
    if not _e_proprietar(request, cont, abonament):
        raise HTTPException(status_code=403, detail="Abonamentul nu e al acestui vizitator.")
    revoca(s, abonament)
    s.commit()
    tinta = (
        inapoi
        if inapoi.startswith("/") and "//" not in inapoi and "\\" not in inapoi
        else "/"
    )
    raspuns = RedirectResponse(tinta, status_code=303)
    if abonament.user_id is None:
        raspuns.delete_cookie(COOKIE_CLIENT, path="/")
    return raspuns
