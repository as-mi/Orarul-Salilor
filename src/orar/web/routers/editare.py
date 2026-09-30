"""Editarea orarului de catre admin: schimba profesorul sau sala unei activitati, adauga o
activitate, renunta la o schimbare.

Tot ce se face aici trece prin `db.corectii`, deci se tine minte si se pune la loc dupa
urmatorul orar publicat. Din paginile de orar se ajunge aici cu "Editează orarul": in modul
acela, un click pe o activitate deschide formularul ei.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orar.db import corectii
from orar.db.models import Corectie, Grupa, Materie, Ora, Perioada, Sala, Sesizare
from orar.db.orare import perioada_implicita
from orar.db.queries import gaseste_grupa, profesori_de_ales
from orar.web.auth import Cont, cere_admin, verifica_csrf
from orar.web.deps import context_saptamana, get_db, templates
from orar.web.routers.sesizari import marcheaza

router = APIRouter(prefix="/admin", tags=["editare"])

ZILE = corectii.ZILE
ORE = tuple(range(8, 21))
#: Tipurile propuse. Pe langa ele se accepta orice tip care exista deja in orar (`sem`,
#: prescurtarea aSc): altfel o activitate existenta n-ar putea fi salvata neschimbata.
TIPURI = ("curs", "seminar", "sem", "Lab", "proiect", "curs+seminar", "sem+Lab")
SEMIGRUPE = ("Gr_1", "Gr_2", "Gr_3", "Gr_4")
TIPURI_SALA = (
    ("fizica", "Sălile facultății"),
    ("externa", "În afara facultății"),
    ("virtuala", "Fără sală"),
)
#: Valoarea din dropdown pentru "altă sală": numele ei vine in campul `sala_noua`.
ALTA_SALA = "__alta__"
FRECVENTE = ("SI", "SP")
#: Valoarea din dropdown pentru "alt nume": profesorul se scrie in campul de alaturi.
ALT_PROFESOR = "__alt__"


def _uneste_profesori(alesi: list[str]) -> str:
    """Profesorii alesi (cate un dropdown de om), intr-un singur camp: `A / B`."""
    nume: list[str] = []
    for ales in alesi:
        for parte in ales.split("/"):
            parte = " ".join(parte.split())
            if parte and parte != ALT_PROFESOR and parte not in nume:
                nume.append(parte)
    return " / ".join(nume)


def _inapoi_sigur(inapoi: str, implicit: str = "/admin") -> str:
    """Ne intoarcem doar intr-o pagina a sitului."""
    return (
        inapoi if inapoi.startswith("/") and "//" not in inapoi and "\\" not in inapoi else implicit
    )


def _liste(s: Session) -> dict[str, object]:
    """Ce se propune in formulare: profesorii, salile si materiile deja cunoscute.

    Profesorii sunt cei din orarul profesorilor -- oameni reali, cu numele intreg. Pana se
    citeste el prima data, ii luam din orarul grupelor, despartind campurile cu mai multi
    oameni (`A / B`)."""
    profesori = profesori_de_ales(s)
    return {
        "profesori": profesori,
        "alt_profesor": ALT_PROFESOR,
        # salile, pe feluri, pentru dropdown: [(eticheta grupului, [Sala, ...])]
        "sali": [
            (eticheta, sali)
            for tip, eticheta in TIPURI_SALA
            if (sali := list(s.scalars(select(Sala).where(Sala.tip == tip).order_by(Sala.nume))))
        ],
        "materii": [
            n for (n,) in s.execute(select(Materie.nume).order_by(func.lower(Materie.nume)))
        ],
    }


def _tipuri(s: Session) -> list[str]:
    existente = s.scalars(
        select(Ora.tip_ora_materie).where(Ora.tip_ora_materie.is_not(None)).distinct()
    )
    return sorted({*TIPURI, *existente}, key=str.lower)


def _orar(s: Session, perioada: str | int | None) -> Perioada | None:
    """Orarul in care se adauga activitatea: cel de pe pagina de unde a venit adminul
    (`perioada`), altfel cel implicit."""
    aleasa = s.get(Perioada, int(perioada)) if str(perioada or "").isdigit() else None
    return aleasa or perioada_implicita(s)


def _semestru(s: Session, an_universitar: str, perioada: str | int | None = None) -> int:
    """Semestrul in care intra o activitate noua a unei formatiuni din `an_universitar`."""
    orar = _orar(s, perioada)
    if orar is not None and orar.an_univ == an_universitar:
        return orar.semestru
    ultimul = s.scalar(
        select(func.max(Perioada.semestru)).where(Perioada.an_univ == an_universitar)
    )
    return ultimul or 1


# ------------------------------------------------------ formularul unei activitati
#
# Acelasi formular pentru o activitate noua si pentru una existenta: la editare se poate
# schimba tot ce se poate alege la adaugare.

CAMPURI = (
    "grupa", "zi", "ora_inceput", "ora_sfarsit", "materie", "tip",
    "profesor", "sala", "semigrupa", "frecventa", "saptamani",
)  # fmt: skip


def _valideaza(s: Session, f: dict[str, str]) -> tuple[corectii.Valori | None, str]:
    """Valorile din formular, sau mesajul pentru ce lipseste."""
    nod = gaseste_grupa(s, f["grupa"]) if f["grupa"].strip() else None
    if nod is None:
        return None, "Alege formațiunea (grupa, seria sau anul) pentru care e activitatea."
    if f["zi"] not in ZILE:
        return None, "Alege ziua."
    if not (f["ora_inceput"].isdigit() and f["ora_sfarsit"].isdigit()):
        return None, "Alege ora de început și ora de sfârșit."
    inceput, sfarsit = int(f["ora_inceput"]), int(f["ora_sfarsit"])
    if not (ORE[0] <= inceput < sfarsit <= ORE[-1]):
        return None, "Ora de sfârșit trebuie să fie după cea de început, între 8 și 20."
    if not f["materie"].strip():
        return None, "Scrie materia."
    if f["tip"] and f["tip"] not in _tipuri(s):
        return None, "Tip de activitate necunoscut."
    if f["semigrupa"] and f["semigrupa"] not in SEMIGRUPE:
        return None, "Semigrupă necunoscută."
    if f["frecventa"] and f["frecventa"] not in FRECVENTE:
        return None, "Frecvența e SI (săptămâni impare) sau SP (pare)."
    return (
        corectii.Valori(
            grupa=nod,
            zi=f["zi"],
            ora_inceput=inceput,
            ora_sfarsit=sfarsit,
            materie=f["materie"],
            tip=f["tip"] or None,
            profesor=f["profesor"],
            sala=f["sala"],
            semigrupa=f["semigrupa"] or None,
            frecventa=f["frecventa"] or None,
            saptamani=f["saptamani"],
        ),
        "",
    )


def _valori_ora(o: Ora) -> dict[str, str]:
    """Activitatea din baza, ca valori de formular."""
    return {
        "grupa": corectii.grupa_afisata(o.grupa).slug,
        "zi": o.zi_saptamana,
        "ora_inceput": str(o.ora_inceput.hour),
        "ora_sfarsit": str(o.ora_sfarsit.hour),
        "materie": o.materie.nume if o.materie else "",
        "tip": o.tip_ora_materie or "",
        "profesor": o.profesor.nume if o.profesor else "",
        "sala": o.sala.nume if o.sala else "",
        "semigrupa": o.semigrupa or "",
        "frecventa": o.frecventa or "",
        "saptamani": o.saptamani or "",
    }


def _pagina(
    request: Request,
    s: Session,
    *,
    valori: dict[str, str],
    inapoi: str,
    o: Ora | None = None,
    sesizare: Sesizare | None = None,
    eroare: str = "",
    stare: int = 200,
    perioada: str = "",
) -> HTMLResponse:
    grupe = list(
        s.scalars(
            select(Grupa)
            .where(Grupa.tip.in_(("grupa", "serie", "specializare", "optional")))
            .order_by(Grupa.tip != "grupa", Grupa.nume)
        )
    )
    corectie = s.get(Corectie, o.corectie_id) if o is not None and o.corectie_id else None
    return templates.TemplateResponse(
        request=request,
        name="admin_activitate.html",
        context={
            **context_saptamana(),
            **_liste(s),
            "o": o,
            "corectie": corectie,
            "publicat": corectii.original(corectie) if corectie else None,
            "sesizare": sesizare,
            "grupe": grupe,
            "zile": ZILE,
            # activitatile existente pot avea si alte ore decat cele propuse (o citire
            # ciudata); le pastram in lista, ca formularul sa nu le schimbe singur
            "ore": sorted(
                {
                    *ORE,
                    *(
                        int(valori[k])
                        for k in ("ora_inceput", "ora_sfarsit")
                        if valori.get(k, "").isdigit()
                    ),
                }
            ),
            "tipuri": _tipuri(s),
            "semigrupe": SEMIGRUPE,
            "frecvente": FRECVENTE,
            "alta_sala": ALTA_SALA,
            "v": valori,
            "eroare": eroare,
            "inapoi": _inapoi_sigur(inapoi),
            # doar la o activitate noua: orarul (semestrul) in care intra
            "orar": _orar(s, perioada) if o is None else None,
        },
        status_code=stare,
    )


def _din_formular(
    grupa: str = Form(""),
    zi: str = Form(""),
    ora_inceput: str = Form(""),
    ora_sfarsit: str = Form(""),
    materie: str = Form(""),
    tip: str = Form(""),
    profesor: list[str] = Form([]),
    sala: str = Form(""),
    sala_noua: str = Form(""),
    semigrupa: str = Form(""),
    frecventa: str = Form(""),
    saptamani: str = Form(""),
) -> dict[str, str]:
    # Sala se alege din dropdown; "altă sală" (sau un nume scris de mana) are intaietate.
    if sala == ALTA_SALA or sala_noua.strip():
        sala = sala_noua
    return {
        "grupa": grupa, "zi": zi, "ora_inceput": ora_inceput, "ora_sfarsit": ora_sfarsit,
        "materie": materie, "tip": tip, "profesor": _uneste_profesori(profesor), "sala": sala,
        "semigrupa": semigrupa, "frecventa": frecventa, "saptamani": saptamani,
    }  # fmt: skip


# ------------------------------------------------------ activitate noua


@router.get("/activitate/noua", response_class=HTMLResponse)
def formular_adaugare(
    request: Request,
    grupa: str = Query("", description="slug-ul formatiunii propuse"),
    sala: str = Query("", description="sala propusa"),
    perioada: str = Query("", description="orarul in care se adauga; implicit cel implicit"),
    dupa: int | None = Query(None, description="activitatea dupa care se completeaza formularul"),
    inapoi: str = Query("/admin"),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> HTMLResponse:
    valori = {**dict.fromkeys(CAMPURI, ""), "grupa": grupa, "sala": sala, "ora_sfarsit": "10"}
    if dupa is not None:
        # Inca o parte a aceleiasi activitati -- alt profesor, in alte saptamani: totul
        # ramane la fel, mai putin cine o tine si cand.
        model = _activitate(s, dupa)
        valori = {**_valori_ora(model), "profesor": "", "saptamani": ""}
        perioada = str(model.perioada_id)
    return _pagina(request, s, valori=valori, inapoi=inapoi, perioada=perioada)


@router.post("/activitate/noua", response_model=None)
def adauga(
    request: Request,
    valori: dict[str, str] = Depends(_din_formular),
    inapoi: str = Form("/admin"),
    perioada: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    admin: Cont = Depends(cere_admin),
) -> HTMLResponse | RedirectResponse:
    if not verifica_csrf(request, csrf):
        return _pagina(
            request, s, valori=valori, inapoi=inapoi, stare=400, perioada=perioada,
            eroare="Formularul a expirat. Încearcă din nou.",
        )  # fmt: skip
    v, eroare = _valideaza(s, valori)
    if v is None:
        return _pagina(
            request, s, valori=valori, inapoi=inapoi, eroare=eroare, stare=400, perioada=perioada
        )
    semestru = _semestru(s, v.grupa.an_universitar, perioada)
    o = corectii.adauga(s, v, semestru=semestru, de_catre=admin.nume)
    s.commit()
    return RedirectResponse(_inapoi_sigur(inapoi, f"/grupa/{o.grupa.slug}"), status_code=303)


# ------------------------------------------------------ activitate existenta


def _activitate(s: Session, ora_id: int) -> Ora:
    o = s.get(Ora, ora_id)
    if o is None:
        raise HTTPException(404, "Activitatea nu mai există - orarul a fost reîncărcat între timp.")
    return o


@router.get("/activitate/{ora_id}", response_class=HTMLResponse)
def formular_modificare(
    request: Request,
    ora_id: int,
    inapoi: str = Query("/admin"),
    sesizare: int | None = Query(None, description="sesizarea de la care s-a ajuns aici"),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> HTMLResponse:
    o = _activitate(s, ora_id)
    return _pagina(
        request,
        s,
        o=o,
        valori=_valori_ora(o),
        inapoi=inapoi,
        sesizare=s.get(Sesizare, sesizare) if sesizare else None,
    )


@router.post("/activitate/{ora_id}", response_model=None)
def modifica(
    request: Request,
    ora_id: int,
    valori: dict[str, str] = Depends(_din_formular),
    inapoi: str = Form("/admin"),
    sesizare_id: str = Form(""),
    rezolva: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    admin: Cont = Depends(cere_admin),
) -> HTMLResponse | RedirectResponse:
    o = _activitate(s, ora_id)
    sesizare = s.get(Sesizare, int(sesizare_id)) if sesizare_id.isdigit() else None

    def din_nou(eroare: str) -> HTMLResponse:
        return _pagina(
            request, s, o=o, valori=valori, inapoi=inapoi, sesizare=sesizare,
            eroare=eroare, stare=400,
        )  # fmt: skip

    if not verifica_csrf(request, csrf):
        return din_nou("Formularul a expirat. Încearcă din nou.")
    v, eroare = _valideaza(s, valori)
    if v is None:
        return din_nou(eroare)
    corectii.modifica(s, o, v, de_catre=admin.nume)
    # Un admin a vazut-o si a salvat-o: nu mai are ce cauta in coada de verificare.
    if corectii.de_verificat(o):
        corectii.confirma(s, o, de_catre=admin.nume)
    if rezolva and sesizare is not None:
        marcheaza(sesizare, "rezolvata", de_catre=admin.nume)
    s.commit()
    return RedirectResponse(_inapoi_sigur(inapoi), status_code=303)


# ------------------------------------------------------ coada de verificare


@router.post("/review/{ora_id}", response_model=None)
def rezolva_din_coada(
    request: Request,
    ora_id: int,
    materie: str = Form(""),
    profesor: list[str] = Form([]),
    sala: str = Form(""),
    saptamani: str = Form(""),
    inapoi: str = Form("/admin/review"),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    admin: Cont = Depends(cere_admin),
) -> RedirectResponse:
    """Rezolva o activitate direct din coada de verificare: ce e scris in cele trei campuri
    se salveaza (schimbat sau nu), iar activitatea iese din coada."""
    inapoi = _inapoi_sigur(inapoi, "/admin/review")
    semn = "&" if "?" in inapoi else "?"

    def eroare(text: str) -> RedirectResponse:
        return RedirectResponse(f"{inapoi}{semn}eroare={quote(text)}#ora-{ora_id}", status_code=303)

    if not verifica_csrf(request, csrf):
        return eroare("Formularul a expirat. Încearcă din nou.")
    o = _activitate(s, ora_id)
    valori = {
        **_valori_ora(o),
        "materie": materie,
        "profesor": _uneste_profesori(profesor),
        "sala": sala,
        "saptamani": saptamani,
    }
    v, mesaj = _valideaza(s, valori)
    if v is None:
        return eroare(mesaj)
    corectii.modifica(s, o, v, de_catre=admin.nume)
    corectii.confirma(s, o, de_catre=admin.nume)
    s.commit()
    return RedirectResponse(f"{inapoi}{semn}rezolvat=1", status_code=303)


# ------------------------------------------------------------- corectiile


@router.get("/corectii", response_class=HTMLResponse)
def lista_corectii(
    request: Request, s: Session = Depends(get_db), _admin: Cont = Depends(cere_admin)
) -> HTMLResponse:
    lista = list(s.scalars(select(Corectie).order_by(Corectie.aplicata, Corectie.id.desc())))
    # activitatea fiecarei corectii aplicate, ca sa se poata deschide de aici
    ore = {
        cid: oid
        for cid, oid in s.execute(
            select(Ora.corectie_id, Ora.id).where(Ora.corectie_id.is_not(None))
        )
    }
    return templates.TemplateResponse(
        request=request,
        name="admin_corectii.html",
        context={
            **context_saptamana(),
            "corectii": lista,
            "ore": ore,
            # activitatea cum era in orarul publicat, pentru "ce s-a schimbat"
            "originale": {c.id: corectii.original(c) for c in lista},
            # corectiile care doar confirma o citire nesigura, fara sa schimbe ceva
            "confirmari": {c.id for c in lista if corectii.e_confirmare(c)},
        },
    )


@router.post("/corectii/{corectie_id}/anuleaza")
def anuleaza(
    request: Request,
    corectie_id: int,
    inapoi: str = Form("/admin/corectii"),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> RedirectResponse:
    inapoi = _inapoi_sigur(inapoi, "/admin/corectii")
    if not verifica_csrf(request, csrf):
        return RedirectResponse(inapoi, status_code=303)
    c = s.get(Corectie, corectie_id)
    if c is None:
        raise HTTPException(404, "corectia nu exista")
    corectii.anuleaza(s, c)
    s.commit()
    return RedirectResponse(inapoi, status_code=303)
