"""Tutoriatele: panoul responsabilului, pagina publica si alegerea lor pe orar.

Cine ce face
------------
* **Responsabilul de tutoriate** (un cont cu permisiunea "tutoriate", sau un admin):
  planifica -- gaseste intervalele in care publicul e liber si salile libere saptamanal --,
  stabileste tutoriatele, asigneaza tutorii pe materii, adauga salile din afara orarului,
  importa / exporta foaia tutoriatelor.
* **Tutorul**: isi vede tutoriatele pe "Orarul meu" si isi editeaza singur linkurile si
  descrierea; poate anula o sedinta anume. Ziua, ora si sala le schimba doar responsabilul.
* **Oricine**: vede pagina publica `/tutoriate` si isi alege tutoriatele care il intereseaza;
  apar apoi pe orarul grupei, langa optionale si facultative.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from orar.db.evenimente import cheie_an
from orar.db.models import (
    Grupa,
    Perioada,
    Sala,
    Tutor,
    Tutoriat,
    TutoriatAnulare,
    TutoriatTutor,
    User,
)
from orar.db.orare import orare_vii, perioada_implicita
from orar.db.structura import perioade_anului
from orar.db.tutoriate import (
    DURATA_ASIGNARE,
    alese_din_cont,
    conflicte_tutori,
    pune_alese_in_cont,
    sali_libere_saptamanal,
    sedintele,
    tutori_activi,
)
from orar.domain.grid import ORA_MAX, ORA_MIN, ZILE
from orar.domain.hierarchy import DENUMIRI_SPECIALIZARE
from orar.domain.rooms import normalizeaza_sala
from orar.web.afisare import _citeste_cookie, _scrie_cookie
from orar.web.auth import Cont, cere_tutoriate, cont_curent, verifica_csrf
from orar.web.deps import context_saptamana, get_db, templates

router = APIRouter(tags=["tutoriate"])

#: Cookie-ul cu tutoriatele alese de cine nu are cont: {"alese": [id, ...]}.
COOKIE = "orar_tutoriate"
TABURI = (
    ("planificare", "Planificare"),
    ("tutoriate", "Tutoriate"),
    ("tutori", "Tutori"),
    ("sali", "Săli"),
    ("import", "Import / export"),
)
ZILE_RO = {
    "Luni": "Luni",
    "Marti": "Marți",
    "Miercuri": "Miercuri",
    "Joi": "Joi",
    "Vineri": "Vineri",
}


# ------------------------------------------------------------------ ajutoare


def _lista(valori: list[str], permise: list[str]) -> str | None:
    """Valorile alese, doar dintre cele permise, ca text `A,B` (None = fara restrictie)."""
    alese = [v for v in permise if v in valori]
    return ",".join(alese) or None


def _orarul(s: Session, perioada: str | int | None = None) -> Perioada | None:
    vii = {o.perioada.id: o.perioada for o in orare_vii(s)}
    if str(perioada or "").isdigit() and int(perioada) in vii:
        return vii[int(perioada)]
    return perioada_implicita(s)


def _interval_semestru(s: Session, orar: Perioada | None) -> tuple[date, date]:
    """Perioada propusa pentru un tutoriat: de luni viitoare pana la sfarsitul activitatii
    didactice a semestrului (din structura anului); fara structura, 12 saptamani."""
    azi = date.today()
    luni_viitoare = azi + timedelta(days=7 - azi.weekday())
    if orar is not None:
        didactice = [
            p
            for p in perioade_anului(s, orar.an_univ)
            if p.fel == "didactica" and p.semestru == orar.semestru and p.pentru != "terminali"
        ]
        if didactice:
            inceput = max(min(p.data_inceput for p in didactice), luni_viitoare)
            return inceput, max(p.data_sfarsit for p in didactice)
    return luni_viitoare, luni_viitoare + timedelta(weeks=12)


def _optiuni_public(s: Session, orar: Perioada | None) -> dict[str, object]:
    """Specializarile, anii si seriile din orar, pentru bifele de public."""
    if orar is None:
        return {"specializari": [], "ani": [], "serii": []}
    grupe = list(
        s.scalars(
            select(Grupa)
            .options(joinedload(Grupa.parinte))
            .where(Grupa.an_universitar == orar.an_univ, Grupa.tip == "grupa")
        )
    )
    coduri = {g.specializare for g in grupe if g.specializare}
    cunoscute = [c for c in DENUMIRI_SPECIALIZARE if c in coduri]
    ani = sorted({cheie_an(g) for g in grupe} - {None})
    serii = sorted(
        {(g.parinte.slug, g.parinte.nume) for g in grupe if g.parinte and g.parinte.tip == "serie"},
        key=lambda x: x[1],
    )
    return {
        "specializari": [
            (c, DENUMIRI_SPECIALIZARE.get(c, c))
            for c in (*cunoscute, *sorted(coduri - set(cunoscute)))
        ],
        "ani": [(a, f"{'Master' if a[0] == 'M' else 'Licență'} · anul {a[1:]}") for a in ani],
        "serii": serii,
    }


def eticheta_public(t: Tutoriat) -> str:
    """Pentru cine e un tutoriat, in cuvinte: `Informatică · licență anul 1`."""
    parti = []
    if t.coduri:
        parti.append(", ".join(DENUMIRI_SPECIALIZARE.get(c, c) for c in t.coduri))
    if t.ani_alesi:
        parti.append(
            ", ".join(f"{'master' if a[0] == 'M' else 'licență'} anul {a[1:]}" for a in t.ani_alesi)
        )
    if t.serii_alese:
        parti.append(", ".join(x.replace("seria-", "seria ") for x in t.serii_alese))
    return " · ".join(parti) or "toți studenții"


def tutoriate_alese_de(request: Request, cont: Cont | None) -> set[int]:
    """Tutoriatele alese: din cont, daca e cineva intrat; altfel din cookie."""
    if cont is not None and cont.user is not None:
        return alese_din_cont(cont.user)
    return tutoriate_alese_din_cookie(request)


def tutoriate_alese_din_cookie(request: Request) -> set[int]:
    """Ce e ales in browser, indiferent de cont.

    Abonamentul anonim la calendar trebuie sa inghete exact ce vede vizitatorul acum, chiar
    daca e logat pe un cont care urmareste alta grupa -- de aceea nu trece prin cont.
    """
    date_ = _citeste_cookie(request, COOKIE).get("alese", [])
    return {x for x in date_ if isinstance(x, int)} if isinstance(date_, list) else set()


def _salveaza_alese(
    s: Session, request: Request, raspuns: Response, cont: Cont | None, alese: set[int]
) -> None:
    if cont is not None and cont.user is not None:
        pune_alese_in_cont(cont.user, alese)
        s.commit()
    else:
        _scrie_cookie(raspuns, COOKIE, {"alese": sorted(alese)} if alese else {})


def _inapoi_sigur(inapoi: str, implicit: str) -> str:
    return (
        inapoi if inapoi.startswith("/") and "//" not in inapoi and "\\" not in inapoi else implicit
    )


# ------------------------------------------------------------------ pagina publica


@router.get("/tutoriate", response_class=HTMLResponse)
def tutoriate_publice(
    request: Request, s: Session = Depends(get_db), cont: Cont | None = Depends(cont_curent)
) -> HTMLResponse:
    """Tutoriatele publicate, ca foaia de pe site-ul facultatii: pe public si pe materie."""
    azi = date.today()
    lista = list(
        s.scalars(
            select(Tutoriat)
            .options(
                joinedload(Tutoriat.sala),
                selectinload(Tutoriat.echipa).joinedload(TutoriatTutor.user),
            )
            .where(Tutoriat.stare == "publicat", Tutoriat.data_sfarsit >= azi)
            .order_by(Tutoriat.materie, Tutoriat.zi_saptamana, Tutoriat.ora_inceput)
        ).unique()
    )
    grupate: dict[str, list[Tutoriat]] = {}
    for t in lista:
        grupate.setdefault(eticheta_public(t), []).append(t)
    return templates.TemplateResponse(
        request=request,
        name="tutoriate_public.html",
        context={
            **context_saptamana(s=s),
            "grupate": dict(sorted(grupate.items())),
            "alese": tutoriate_alese_de(request, cont),
            "zile_ro": ZILE_RO,
        },
    )


@router.post("/tutoriate/alegere")
def alege_tutoriate(
    request: Request,
    oferite: list[int] = Form([]),
    alese: list[int] = Form([]),
    inapoi: str = Form("/tutoriate"),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    """Salveaza ce tutoriate vrea cineva pe orar, dintre cele `oferite` pe pagina de unde
    vine (restul alegerilor raman cum erau)."""
    raspuns = RedirectResponse(_inapoi_sigur(inapoi, "/tutoriate"), status_code=303)
    if not verifica_csrf(request, csrf):
        return raspuns
    existente = set(s.scalars(select(Tutoriat.id).where(Tutoriat.id.in_(oferite or [-1]))))
    noi = (tutoriate_alese_de(request, cont) - set(oferite)) | (set(alese) & existente)
    _salveaza_alese(s, request, raspuns, cont, noi)
    return raspuns


# ------------------------------------------------------------------ panoul responsabilului


def _context_panou(s: Session, orar: Perioada | None) -> dict[str, object]:
    return {
        "orar": orar,
        "orare": orare_vii(s),
        "taburi": TABURI,
        "zile": ZILE,
        "zile_ro": ZILE_RO,
        "ore": list(range(ORA_MIN, ORA_MAX + 1)),
        **_optiuni_public(s, orar),
    }


@router.get("/tutoriate/panou", response_class=HTMLResponse)
def panou(
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    """Panoul vechi a fost inlocuit de campanii: aceeasi adresa duce acum acolo, ca
    linkurile vechi sa nu ramana in aer. Permisiunea se verifica inainte de redirect."""
    return RedirectResponse("/tutoriate/campanii", status_code=303)


def _numara_alegerile(s: Session) -> dict[int, int]:
    """Cate conturi au ales fiecare tutoriat (cei fara cont nu se pot numara)."""
    numar: dict[int, int] = {}
    for (text,) in s.execute(select(User.tutoriate_alese).where(User.tutoriate_alese.is_not(None))):
        try:
            for x in json.loads(text):
                if isinstance(x, int):
                    numar[x] = numar.get(x, 0) + 1
        except ValueError:
            continue
    return numar


# -- salile libere, saptamanal (htmx)


@router.get("/tutoriate/panou/sali-libere", response_class=HTMLResponse)
def sali_libere_tutoriat(
    request: Request,
    zi_saptamana: str = Query(""),
    ora_inceput: str = Query(""),
    ora_sfarsit: str = Query(""),
    data_inceput: str = Query(""),
    data_sfarsit: str = Query(""),
    frecventa: str = Query(""),
    tutoriat: str = Query(""),
    sala: str = Query(""),
    s: Session = Depends(get_db),
    _r: Cont = Depends(cere_tutoriate),
) -> HTMLResponse:
    ctx = _context_sali(
        s,
        zi_saptamana,
        ora_inceput,
        ora_sfarsit,
        data_inceput,
        data_sfarsit,
        frecventa,
        tutoriat,
        sala,
    )
    ctx["sala_aleasa"] = sala
    return templates.TemplateResponse(request=request, name="_tut_sali_libere.html", context=ctx)


def _context_sali(s, zi, a, b, de_la, pana_la, frecventa, fara, aleasa="") -> dict[str, object]:  # noqa: ANN001
    try:
        inceput, sfarsit = time.fromisoformat(a), time.fromisoformat(b)
        prima, ultima = date.fromisoformat(de_la), date.fromisoformat(pana_la)
    except ValueError:
        return {
            "sali": None,
            "eroare_sali": "Alege ziua, orele și perioada, apoi caută sălile libere.",
        }
    if zi not in ZILE or inceput >= sfarsit or prima > ultima:
        return {"sali": None, "eroare_sali": "Ziua, orele sau perioada nu sunt bune."}
    sali, zile = sali_libere_saptamanal(
        s, zi, inceput, sfarsit, prima, ultima,
        frecventa=frecventa or None, fara=int(fara) if str(fara).isdigit() else None,
    )  # fmt: skip
    # primele 40, plus sala aleasa deja, oriunde ar fi in lista
    primele = sali[:40] + [x for x in sali[40:] if x.sala.slug == aleasa]
    return {"sali": primele, "zile_sedinte": zile, "eroare_sali": ""}


# -- un tutoriat


def _valori(t: Tutoriat | None, **implicite) -> dict[str, object]:  # noqa: ANN003
    if t is None:
        return {
            "materie": "", "zi_saptamana": "Luni", "ora_inceput": "18", "ora_sfarsit": "20",
            "data_inceput": "", "data_sfarsit": "", "frecventa": "", "sala": "", "loc": "",
            "specializari": [], "ani": [], "serii": [], "tutori": [], "principal": "",
            "link_whatsapp": "", "link_teams": "", "descriere": "", **implicite,
        }  # fmt: skip
    return {
        "materie": t.materie,
        "zi_saptamana": t.zi_saptamana,
        "ora_inceput": str(t.ora_inceput.hour),
        "ora_sfarsit": str(t.ora_sfarsit.hour),
        "data_inceput": t.data_inceput.isoformat(),
        "data_sfarsit": t.data_sfarsit.isoformat(),
        "frecventa": t.frecventa or "",
        "sala": t.sala.slug if t.sala else "",
        "loc": t.loc or "",
        "specializari": t.coduri,
        "ani": t.ani_alesi,
        "serii": t.serii_alese,
        "tutori": [str(m.user_id) for m in t.echipa],
        "principal": next((str(m.user_id) for m in t.echipa if m.principal), ""),
        "link_whatsapp": t.link_whatsapp or "",
        "link_teams": t.link_teams or "",
        "descriere": t.descriere or "",
    }


def _formular(
    request: Request,
    s: Session,
    *,
    t: Tutoriat | None,
    v: dict[str, object],
    orar: Perioada | None,
    eroare: str = "",
    stare: int = 200,
) -> HTMLResponse:
    azi = date.today()
    ctx_sali: dict[str, object] = {"sali": None, "eroare_sali": "", "sala_aleasa": v["sala"]}
    if (
        v["data_inceput"]
        and v["data_sfarsit"]
        and str(v["ora_inceput"]).isdigit()
        and str(v["ora_sfarsit"]).isdigit()
    ):
        ctx_sali |= _context_sali(
            s, v["zi_saptamana"], f"{int(v['ora_inceput']):02d}:00", f"{int(v['ora_sfarsit']):02d}:00",
            v["data_inceput"], v["data_sfarsit"], v["frecventa"], t.id if t else "", str(v["sala"]),
        )  # fmt: skip
    # tutorii activi la materia tutoriatului intai, apoi toti ceilalti tutori activi
    activi = tutori_activi(s, azi)
    materie = str(v["materie"]).strip().lower()
    la_materie = {x.user_id for x in activi if x.materie.strip().lower() == materie}
    oameni = {x.user_id: x.user for x in activi}
    for m in t.echipa if t else []:
        oameni.setdefault(m.user_id, m.user)
    conflicte = []
    if t is not None and orar is not None:
        conflicte = conflicte_tutori(
            s, [m.user_id for m in t.echipa], t.zi_saptamana, t.ora_inceput, t.ora_sfarsit, orar
        )
    return templates.TemplateResponse(
        request=request,
        name="tutoriat_formular.html",
        context={
            **context_saptamana(),
            **_context_panou(s, orar),
            **ctx_sali,
            "t": t,
            "v": v,
            "eroare": eroare,
            "tutori": sorted(
                oameni.items(), key=lambda x: (x[0] not in la_materie, x[1].nume.lower())
            ),
            "la_materie": la_materie,
            "materii_tutori": sorted({x.materie for x in activi}, key=str.lower),
            "conflicte": conflicte,
            "sedinte": sedintele(s, t) if t else [],
            "anulate": {a.data: a for a in t.anulari} if t else {},
            "eticheta_public": eticheta_public,
        },
        status_code=stare,
    )


@router.get("/tutoriate/panou/tutoriat/nou", response_class=HTMLResponse)
def tutoriat_nou(
    request: Request,
    perioada: str = Query(""),
    zi: str = Query(""),
    ora: int | None = Query(None),
    durata: int = Query(2),
    sala: str = Query(""),
    materie: str = Query(""),
    specializari: list[str] = Query([]),
    ani: list[str] = Query([]),
    serii: list[str] = Query([]),
    de_la: str = Query(""),
    pana_la: str = Query(""),
    s: Session = Depends(get_db),
    _r: Cont = Depends(cere_tutoriate),
) -> HTMLResponse:
    """Formularul unui tutoriat nou, completat din planificare (zi, ora, sala, public)."""
    orar = _orarul(s, perioada)
    prima, ultima = _interval_semestru(s, orar)
    v = _valori(
        None,
        zi_saptamana=zi if zi in ZILE else "Luni",
        ora_inceput=str(ora or 18),
        ora_sfarsit=str((ora or 18) + durata),
        sala=sala,
        materie=materie,
        specializari=specializari,
        ani=ani,
        serii=serii,
        data_inceput=de_la or prima.isoformat(),
        data_sfarsit=pana_la or ultima.isoformat(),
    )
    return _formular(request, s, t=None, v=v, orar=orar)


def _tutoriat(s: Session, tutoriat_id: int) -> Tutoriat:
    t = s.get(Tutoriat, tutoriat_id)
    if t is None:
        raise HTTPException(404, "Tutoriatul nu există.")
    return t


@router.get("/tutoriate/panou/tutoriat/{tutoriat_id}", response_class=HTMLResponse)
def tutoriat_existent(
    request: Request,
    tutoriat_id: int,
    s: Session = Depends(get_db),
    _r: Cont = Depends(cere_tutoriate),
) -> HTMLResponse:
    t = _tutoriat(s, tutoriat_id)
    orar = s.scalar(
        select(Perioada).where(Perioada.an_univ == t.an_univ, Perioada.semestru == t.semestru)
    )
    return _formular(request, s, t=t, v=_valori(t), orar=orar)


def _din_formular(
    perioada: str = Form(""),
    materie: str = Form(""),
    zi_saptamana: str = Form(""),
    ora_inceput: str = Form(""),
    ora_sfarsit: str = Form(""),
    data_inceput: str = Form(""),
    data_sfarsit: str = Form(""),
    frecventa: str = Form(""),
    sala: str = Form(""),
    loc: str = Form(""),
    specializari: list[str] = Form([]),
    ani: list[str] = Form([]),
    serii: list[str] = Form([]),
    tutori: list[str] = Form([]),
    principal: str = Form(""),
    link_whatsapp: str = Form(""),
    link_teams: str = Form(""),
    descriere: str = Form(""),
) -> dict[str, object]:
    return {
        "perioada": perioada, "materie": " ".join(materie.split()), "zi_saptamana": zi_saptamana,
        "ora_inceput": ora_inceput, "ora_sfarsit": ora_sfarsit, "data_inceput": data_inceput,
        "data_sfarsit": data_sfarsit, "frecventa": frecventa, "sala": sala, "loc": loc.strip(),
        "specializari": specializari, "ani": ani, "serii": serii, "tutori": tutori,
        "principal": principal, "link_whatsapp": link_whatsapp.strip(),
        "link_teams": link_teams.strip(), "descriere": descriere.strip(),
    }  # fmt: skip


def _link_valid(link: str) -> bool:
    return not link or link.startswith(("https://", "http://"))


def _aplica(s: Session, t: Tutoriat, v: dict[str, object], orar: Perioada | None) -> str:
    """Pune valorile din formular pe tutoriat. Intoarce mesajul de eroare, sau ""."""
    if not v["materie"]:
        return "Scrie materia."
    if v["zi_saptamana"] not in ZILE:
        return "Alege ziua."
    if not (str(v["ora_inceput"]).isdigit() and str(v["ora_sfarsit"]).isdigit()):
        return "Alege orele."
    a, b = int(v["ora_inceput"]), int(v["ora_sfarsit"])
    if not ORA_MIN <= a < b <= ORA_MAX:
        return "Ora de sfârșit trebuie să fie după cea de început, între 8 și 20."
    try:
        prima, ultima = (
            date.fromisoformat(str(v["data_inceput"])),
            date.fromisoformat(str(v["data_sfarsit"])),
        )
    except ValueError:
        return "Alege perioada (prima și ultima zi)."
    if prima > ultima:
        return "Prima zi e după ultima."
    if v["frecventa"] not in ("", "SI", "SP"):
        return "Frecvența e săptămânal, impar sau par."
    if not (_link_valid(str(v["link_whatsapp"])) and _link_valid(str(v["link_teams"]))):
        return "Linkurile trebuie să înceapă cu https://."
    sala = (
        s.scalar(select(Sala).where(Sala.slug == v["sala"], Sala.tip.in_(("fizica", "externa"))))
        if v["sala"]
        else None
    )
    if v["sala"] and sala is None:
        return "Alege sala din listă."
    if orar is None:
        return "Nu e niciun orar încărcat: tutoriatul trebuie să țină de un semestru."
    if sala is not None:
        # sala trebuie sa fie libera la toate sedintele -- altfel, pe alta sala sau alt interval
        sali, _ = sali_libere_saptamanal(
            s, str(v["zi_saptamana"]), time(a), time(b), prima, ultima,
            frecventa=v["frecventa"] or None, fara=t.id,
        )  # fmt: skip
        # (o sala din afara facultatii nu are orar: nu se poate verifica, deci se accepta)
        gasita = next((x for x in sali if x.sala.id == sala.id), None)
        if gasita is not None and not gasita.mereu:
            ocupata = ", ".join(f"{d:%d.%m}" for d in gasita.ocupata_in[:6])
            return f"{sala.nume} nu e liberă la toate ședințele" + (
                f" (ocupată pe {ocupata})." if ocupata else "."
            )
    optiuni = _optiuni_public(s, orar)
    echipa_ids = [int(x) for x in dict.fromkeys(v["tutori"]) if str(x).isdigit()]
    oameni = {u.id: u for u in s.scalars(select(User).where(User.id.in_(echipa_ids or [-1])))}

    t.an_univ, t.semestru = orar.an_univ, orar.semestru
    t.materie = str(v["materie"])[:160]
    t.zi_saptamana = str(v["zi_saptamana"])
    t.ora_inceput, t.ora_sfarsit = time(a), time(b)
    t.data_inceput, t.data_sfarsit = prima, ultima
    t.frecventa = v["frecventa"] or None
    t.sala, t.loc = sala, (str(v["loc"])[:160] or None) if sala is None else None
    t.specializari = _lista(v["specializari"], [c for c, _ in optiuni["specializari"]])
    t.ani = _lista(v["ani"], [c for c, _ in optiuni["ani"]])
    t.serii = _lista(v["serii"], [c for c, _ in optiuni["serii"]])
    t.link_whatsapp = v["link_whatsapp"] or None
    t.link_teams = v["link_teams"] or None
    t.descriere = v["descriere"] or None
    principal = (
        int(v["principal"])
        if str(v["principal"]).isdigit()
        else (echipa_ids[0] if echipa_ids else None)
    )
    t.echipa = [
        TutoriatTutor(user=oameni[i], principal=i == principal) for i in echipa_ids if i in oameni
    ]
    return ""


@router.post("/tutoriate/panou/tutoriat/nou", response_model=None)
def adauga_tutoriat(
    request: Request,
    v: dict[str, object] = Depends(_din_formular),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> HTMLResponse | RedirectResponse:
    orar = _orarul(s, v["perioada"])
    if not verifica_csrf(request, csrf):
        return _formular(
            request, s, t=None, v=v, orar=orar, eroare="Formularul a expirat.", stare=400
        )
    t = Tutoriat(creat_de=responsabil.nume, creat_la=datetime.now(), stare="ciorna")
    with s.no_autoflush:
        eroare = _aplica(s, t, v, orar)
    if eroare:
        s.expunge(t) if t in s else None
        return _formular(request, s, t=None, v=v, orar=orar, eroare=eroare, stare=400)
    s.add(t)
    s.commit()
    return RedirectResponse(f"/tutoriate/panou/tutoriat/{t.id}?salvat=1", status_code=303)


@router.post("/tutoriate/panou/tutoriat/{tutoriat_id}", response_model=None)
def modifica_tutoriat(
    request: Request,
    tutoriat_id: int,
    v: dict[str, object] = Depends(_din_formular),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _r: Cont = Depends(cere_tutoriate),
) -> HTMLResponse | RedirectResponse:
    t = _tutoriat(s, tutoriat_id)
    orar = _orarul(s, v["perioada"])
    if not verifica_csrf(request, csrf):
        return _formular(request, s, t=t, v=v, orar=orar, eroare="Formularul a expirat.", stare=400)
    with s.no_autoflush:
        eroare = _aplica(s, t, v, orar)
    if eroare:
        s.rollback()
        return _formular(
            request, s, t=_tutoriat(s, tutoriat_id), v=v, orar=orar, eroare=eroare, stare=400
        )
    s.commit()
    return RedirectResponse(f"/tutoriate/panou/tutoriat/{t.id}?salvat=1", status_code=303)


@router.post("/tutoriate/panou/tutoriat/{tutoriat_id}/stare")
def schimba_starea(
    request: Request,
    tutoriat_id: int,
    stare: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _r: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    """Publica (apare pe sit), trece inapoi in ciorna, sau incheie un tutoriat."""
    t = _tutoriat(s, tutoriat_id)
    if verifica_csrf(request, csrf) and stare in ("ciorna", "publicat", "incheiat"):
        t.stare = stare
        s.commit()
    return RedirectResponse("/tutoriate/panou?tab=tutoriate", status_code=303)


@router.post("/tutoriate/panou/tutoriat/{tutoriat_id}/duplica")
def duplica(
    request: Request,
    tutoriat_id: int,
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    """Inca un tutoriat la aceeasi materie, pentru acelasi public -- ziua, ora si sala se
    aleg din nou, deci se deschide ca formular nou, completat cu restul."""
    t = _tutoriat(s, tutoriat_id)
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/tutoriate/panou?tab=tutoriate", status_code=303)
    parametri = [
        ("materie", t.materie),
        ("de_la", t.data_inceput.isoformat()),
        ("pana_la", t.data_sfarsit.isoformat()),
    ]
    parametri += (
        [("specializari", c) for c in t.coduri]
        + [("ani", a) for a in t.ani_alesi]
        + [("serii", x) for x in t.serii_alese]
    )
    from urllib.parse import urlencode

    return RedirectResponse(
        f"/tutoriate/panou/tutoriat/nou?{urlencode(parametri)}", status_code=303
    )


@router.post("/tutoriate/panou/tutoriat/{tutoriat_id}/sterge")
def sterge_tutoriat(
    request: Request,
    tutoriat_id: int,
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _r: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    if verifica_csrf(request, csrf):
        s.delete(_tutoriat(s, tutoriat_id))
        s.commit()
    return RedirectResponse("/tutoriate/panou?tab=tutoriate", status_code=303)


# -- anularea unei sedinte: responsabilul sau un tutor din echipa


def _poate_tutorul(cont: Cont | None, t: Tutoriat) -> bool:
    if cont is None:
        return False
    if cont.poate("tutoriate"):
        return True
    return cont.user is not None and any(m.user_id == cont.user.id for m in t.echipa)


@router.post("/tutoriate/{tutoriat_id}/anulare")
def anuleaza_sedinta(
    request: Request,
    tutoriat_id: int,
    data: str = Form(""),
    motiv: str = Form(""),
    reface: str = Form(""),
    inapoi: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    """Anuleaza (sau reface) o sedinta anume. Pot responsabilul si tutorii din echipa."""
    t = _tutoriat(s, tutoriat_id)
    if not _poate_tutorul(cont, t):
        raise HTTPException(403, "Doar tutorii tutoriatului sau responsabilul pot anula o ședință.")
    tinta = _inapoi_sigur(inapoi, f"/tutoriate/al-meu/{t.id}")
    if not verifica_csrf(request, csrf):
        return RedirectResponse(tinta, status_code=303)
    try:
        zi = date.fromisoformat(data)
    except ValueError:
        return RedirectResponse(tinta, status_code=303)
    existenta = s.scalar(
        select(TutoriatAnulare).where(
            TutoriatAnulare.tutoriat_id == t.id, TutoriatAnulare.data == zi
        )
    )
    if reface:
        if existenta is not None:
            s.delete(existenta)
    elif existenta is None and zi in sedintele(s, t):
        s.add(
            TutoriatAnulare(
                tutoriat=t, data=zi, motiv=motiv.strip()[:200] or None, de_catre=cont.nume
            )
        )
    s.commit()
    return RedirectResponse(tinta, status_code=303)


# -- pagina tutorului: linkuri, descriere, anulari


@router.get("/tutoriate/al-meu/{tutoriat_id}", response_class=HTMLResponse)
def tutoriatul_meu(
    request: Request,
    tutoriat_id: int,
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> HTMLResponse:
    t = _tutoriat(s, tutoriat_id)
    if not _poate_tutorul(cont, t):
        raise HTTPException(403, "Doar tutorii tutoriatului îl pot edita.")
    return templates.TemplateResponse(
        request=request,
        name="tutoriat_al_meu.html",
        context={
            **context_saptamana(s=s),
            "t": t,
            "sedinte": sedintele(s, t),
            "anulate": {a.data: a for a in t.anulari},
            "azi": date.today(),
            "zile_ro": ZILE_RO,
            "eticheta_public": eticheta_public,
        },
    )


@router.post("/tutoriate/al-meu/{tutoriat_id}", response_model=None)
def salveaza_tutoriatul_meu(
    request: Request,
    tutoriat_id: int,
    link_whatsapp: str = Form(""),
    link_teams: str = Form(""),
    descriere: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    cont: Cont | None = Depends(cont_curent),
) -> RedirectResponse:
    """Tutorii isi schimba singuri doar linkurile si descrierea."""
    t = _tutoriat(s, tutoriat_id)
    if not _poate_tutorul(cont, t):
        raise HTTPException(403, "Doar tutorii tutoriatului îl pot edita.")
    if not verifica_csrf(request, csrf):
        return RedirectResponse(f"/tutoriate/al-meu/{t.id}", status_code=303)
    if not (_link_valid(link_whatsapp.strip()) and _link_valid(link_teams.strip())):
        return RedirectResponse(f"/tutoriate/al-meu/{t.id}?eroare=link", status_code=303)
    t.link_whatsapp = link_whatsapp.strip()[:300] or None
    t.link_teams = link_teams.strip()[:300] or None
    t.descriere = descriere.strip()[:2000] or None
    s.commit()
    return RedirectResponse(f"/tutoriate/al-meu/{t.id}?salvat=1", status_code=303)


# -- tutorii


@router.post("/tutoriate/panou/tutori")
def asigneaza_tutor(
    request: Request,
    cine: str = Form("", description="id-ul unui cont sau un email"),
    materie: str = Form(""),
    specializari: list[str] = Form([]),
    ani: list[str] = Form([]),
    de_la: str = Form(""),
    pana_la: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    """Asigneaza un tutor la o materie, pe 6 luni (implicit). Un email fara cont devine
    invitatie: asignarea trece pe cont cand cineva se inregistreaza cu el."""
    inapoi = "/tutoriate/panou?tab=tutori"

    def eroare(text: str) -> RedirectResponse:
        return RedirectResponse(f"{inapoi}&eroare={quote(text)}", status_code=303)

    if not verifica_csrf(request, csrf):
        return eroare("Formularul a expirat.")
    materie = " ".join(materie.split())[:160]
    if not materie:
        return eroare("Scrie materia.")
    user, email = None, None
    cine = cine.strip()
    if cine.isdigit():
        user = s.get(User, int(cine))
    elif "@" in cine:
        email = cine.lower()
        user = s.scalar(select(User).where(func.lower(User.email) == email))
    if user is None and email is None:
        return eroare("Alege contul tutorului sau scrie emailul lui.")
    try:
        prima = date.fromisoformat(de_la) if de_la else date.today()
        ultima = date.fromisoformat(pana_la) if pana_la else prima + DURATA_ASIGNARE
    except ValueError:
        return eroare("Datele nu sunt bune.")
    if ultima < prima:
        return eroare("Asignarea se termină înainte să înceapă.")
    orar = perioada_implicita(s)
    optiuni = _optiuni_public(s, orar)
    s.add(
        Tutor(
            user=user, email=None if user else email, materie=materie,
            specializari=_lista(specializari, [c for c, _ in optiuni["specializari"]]),
            ani=_lista(ani, [c for c, _ in optiuni["ani"]]),
            valabil_de_la=prima, valabil_pana_la=ultima,
            creat_de=responsabil.nume, creat_la=datetime.now(),
        )
    )  # fmt: skip
    s.commit()
    return RedirectResponse(f"{inapoi}&salvat=1", status_code=303)


def _tutor(s: Session, tutor_id: int) -> Tutor:
    t = s.get(Tutor, tutor_id)
    if t is None:
        raise HTTPException(404, "Asignarea nu există.")
    return t


@router.post("/tutoriate/panou/tutori/{tutor_id}/prelungeste")
def prelungeste(
    request: Request,
    tutor_id: int,
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _r: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    """Inca 6 luni, de la sfarsitul asignarii (sau de azi, daca a expirat)."""
    t = _tutor(s, tutor_id)
    if verifica_csrf(request, csrf):
        t.valabil_pana_la = max(t.valabil_pana_la, date.today()) + DURATA_ASIGNARE
        s.commit()
    return RedirectResponse("/tutoriate/panou?tab=tutori&salvat=1", status_code=303)


@router.post("/tutoriate/panou/tutori/{tutor_id}/retrage")
def retrage(
    request: Request,
    tutor_id: int,
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _r: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    """Incheie asignarea azi (ramane in istoric)."""
    t = _tutor(s, tutor_id)
    if verifica_csrf(request, csrf):
        ieri = date.today() - timedelta(days=1)
        t.valabil_pana_la = max(t.valabil_de_la, ieri)
        if t.valabil_de_la > ieri:
            s.delete(t)
        s.commit()
    return RedirectResponse("/tutoriate/panou?tab=tutori&salvat=1", status_code=303)


# -- salile din afara orarului


@router.post("/tutoriate/panou/sali")
def adauga_sala(
    request: Request,
    nume: str = Form(""),
    locuri: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _r: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    inapoi = "/tutoriate/panou?tab=sali"
    if not verifica_csrf(request, csrf) or not nume.strip():
        return RedirectResponse(inapoi, status_code=303)
    sala = _sala_noua(s, nume, int(locuri) if locuri.strip().isdigit() else None)
    s.commit()
    return RedirectResponse(f"{inapoi}&salvat={quote(sala.nume)}", status_code=303)


def _sala_noua(s: Session, nume: str, locuri: int | None = None) -> Sala:
    """Sala cu numele dat; daca nu exista, o adauga ca sala pentru tutoriate. Numele se
    normalizeaza ca la orar ("L-321" si "L.321" sunt aceeasi sala)."""
    norm = normalizeaza_sala(nume)
    sala = s.scalar(select(Sala).where(Sala.slug == norm.slug))
    if sala is None:
        sala = Sala(
            nume=norm.nume[:80], slug=norm.slug, tip="fizica", nr_locuri=locuri, doar_tutoriate=True
        )
        s.add(sala)
        s.flush()
    elif locuri and sala.doar_tutoriate:
        sala.nr_locuri = locuri
    return sala


@router.post("/tutoriate/panou/sali/{sala_id}/sterge")
def sterge_sala(
    request: Request,
    sala_id: int,
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _r: Cont = Depends(cere_tutoriate),
) -> RedirectResponse:
    """Scoate o sala adaugata pentru tutoriate, daca nu o mai foloseste niciun tutoriat."""
    sala = s.get(Sala, sala_id)
    if verifica_csrf(request, csrf) and sala is not None and sala.doar_tutoriate:
        if s.scalar(select(func.count()).select_from(Tutoriat).where(Tutoriat.sala_id == sala.id)):
            return RedirectResponse(
                f"/tutoriate/panou?tab=sali&eroare={quote(sala.nume + ' e folosită de un tutoriat.')}",
                status_code=303,
            )
        s.delete(sala)
        s.commit()
    return RedirectResponse("/tutoriate/panou?tab=sali", status_code=303)


# -- importul si exportul foii tutoriatelor

_RE_INTERVAL = re.compile(
    r"^\s*(?P<zi>[a-zăâîșțA-ZĂÂÎȘȚ]+)\s+(?P<a>\d{1,2})\s*[-–]\s*(?P<b>\d{1,2})\s*$"
)
_RE_TITLU = re.compile(r"^(?P<spec>.+?)\s+an(?:ul)?\s+(?P<an>[IV]+|\d)\b", re.IGNORECASE)
_SPECIALIZARI_DUPA_NUME = {
    "matematica": "MATE", "mate": "MATE", "informatica": "INFO", "info": "INFO", "cti": "CTI",
    "matematica-informatica": "MATE-INFO", "mate-info": "MATE-INFO",
    "matematici aplicate": "MATE-APL", "mate apl": "MATE-APL",
}  # fmt: skip
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}


def _fara_diacritice(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _zi_din_text(text: str) -> str | None:
    cuvant = _fara_diacritice(text).strip().capitalize()
    return cuvant if cuvant in ZILE else None


def importa_foaia(
    s: Session, text: str, *, orar: Perioada, de_la: date, pana_la: date, de_catre: str
) -> list[str]:
    """Citeste foaia tutoriatelor (CSV, formatul de pe site-ul FMI) si creeaza tutoriatele
    ca ciorne, cu tutorii lor. Intoarce raportul, rand cu rand."""
    raport: list[str] = []
    public: tuple[str | None, str | None] = (None, None)
    curent: Tutoriat | None = None
    acum = datetime.now()

    def tutor(email: str, nume: str) -> None:
        email = email.strip().lower()
        if not email or "@" not in email or curent is None:
            return
        user = s.scalar(select(User).where(func.lower(User.email) == email))
        exista = s.scalar(
            select(Tutor.id).where(
                (Tutor.user_id == user.id) if user else (func.lower(Tutor.email) == email),
                func.lower(Tutor.materie) == curent.materie.lower(),
                Tutor.valabil_pana_la >= de_la,
            )
        )
        if exista is None:
            s.add(
                Tutor(
                    user=user, email=None if user else email, materie=curent.materie,
                    specializari=public[0], ani=public[1], valabil_de_la=de_la,
                    valabil_pana_la=max(pana_la, de_la + DURATA_ASIGNARE), creat_de=de_catre, creat_la=acum,
                )
            )  # fmt: skip
        if user is not None:
            if all(m.user_id != user.id for m in curent.echipa):
                curent.echipa.append(TutoriatTutor(user=user, principal=not curent.echipa))
        else:
            raport.append(
                f"  {nume or email} ({email}) n-are cont: asignarea așteaptă înregistrarea lui"
            )

    for rand in csv.reader(io.StringIO(text)):
        rand = [c.strip() for c in rand] + [""] * 7
        materie, nume, email, interval, sala, whatsapp, teams = rand[:7]
        if materie and not any(rand[1:7]):
            if m := _RE_TITLU.match(_fara_diacritice(materie)):
                spec = _SPECIALIZARI_DUPA_NUME.get(m["spec"].strip().lower())
                an = _ROMAN.get(m["an"].upper()) if not m["an"].isdigit() else int(m["an"])
                nivel = "M" if "master" in materie.lower() else "L"
                public = (spec, f"{nivel}{an}" if an else None)
                raport.append(f"{materie}:")
            continue
        if materie.lower() == "materia":
            continue
        if materie:
            potrivire = _RE_INTERVAL.match(interval)
            zi = _zi_din_text(potrivire["zi"]) if potrivire else None
            if (
                not potrivire
                or zi is None
                or not ORA_MIN <= int(potrivire["a"]) < int(potrivire["b"]) <= ORA_MAX
            ):
                raport.append(f"  sărit: {materie} -- intervalul {interval!r} nu se înțelege")
                curent = None
                continue
            sala_db = _sala_noua(s, sala) if sala else None
            curent = Tutoriat(
                an_univ=orar.an_univ, semestru=orar.semestru, materie=materie[:160], zi_saptamana=zi,
                ora_inceput=time(int(potrivire["a"])), ora_sfarsit=time(int(potrivire["b"])),
                sala=sala_db, data_inceput=de_la, data_sfarsit=pana_la,
                specializari=public[0], ani=public[1],
                link_whatsapp=whatsapp if _link_valid(whatsapp) else None,
                link_teams=teams if _link_valid(teams) else None,
                stare="ciorna", creat_de=de_catre, creat_la=acum,
            )  # fmt: skip
            s.add(curent)
            raport.append(
                f"  {materie} · {ZILE_RO[zi]} {interval.split(' ', 1)[-1].strip()} · {sala_db.nume if sala_db else '-'}"
            )
        tutor(email, nume)
    s.flush()
    return raport


@router.post("/tutoriate/panou/import", response_class=HTMLResponse, response_model=None)
async def importa(
    request: Request,
    fisier: UploadFile | None = File(None),
    text: str = Form(""),
    de_la: str = Form(""),
    pana_la: str = Form(""),
    perioada: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    responsabil: Cont = Depends(cere_tutoriate),
) -> HTMLResponse | RedirectResponse:
    if not verifica_csrf(request, csrf):
        return RedirectResponse("/tutoriate/panou?tab=import", status_code=303)
    orar = _orarul(s, perioada)
    if orar is None:
        return RedirectResponse("/tutoriate/panou?tab=import", status_code=303)
    continut = text
    if fisier is not None and fisier.filename:
        continut = (await fisier.read()).decode("utf-8-sig", errors="replace")
    prima, ultima = _interval_semestru(s, orar)
    try:
        prima = date.fromisoformat(de_la) if de_la else prima
        ultima = date.fromisoformat(pana_la) if pana_la else ultima
    except ValueError:
        pass
    raport = importa_foaia(
        s, continut, orar=orar, de_la=prima, pana_la=ultima, de_catre=responsabil.nume
    )
    s.commit()
    return templates.TemplateResponse(
        request=request,
        name="tutoriate_panou.html",
        context={**context_saptamana(), **_context_panou(s, orar), "tab": "import", "raport": raport,
                 "de_la": prima, "pana_la": ultima},
    )  # fmt: skip


@router.get("/tutoriate/panou/export.csv")
def exporta(
    perioada: str = Query(""), s: Session = Depends(get_db), _r: Cont = Depends(cere_tutoriate)
) -> Response:
    """Tutoriatele publicate, in formatul foii de pe site-ul facultatii."""
    orar = _orarul(s, perioada)
    lista = list(
        s.scalars(
            select(Tutoriat)
            .options(
                joinedload(Tutoriat.sala),
                selectinload(Tutoriat.echipa).joinedload(TutoriatTutor.user),
            )
            .where(
                Tutoriat.stare == "publicat",
                *(
                    [Tutoriat.an_univ == orar.an_univ, Tutoriat.semestru == orar.semestru]
                    if orar
                    else []
                ),
            )
            .order_by(Tutoriat.materie)
        ).unique()
    )
    iesire = io.StringIO()
    w = csv.writer(iesire)
    grupate: dict[str, list[Tutoriat]] = {}
    for t in lista:
        grupate.setdefault(eticheta_public(t), []).append(t)
    for public, tutoriate in sorted(grupate.items()):
        w.writerow([public])
        w.writerow(["Materia", "Nume", "Adresa", "Interval", "Sala", "Link WhatsApp", "Link Teams"])
        for t in tutoriate:
            echipa = t.echipa or [None]
            for i, m in enumerate(echipa):
                prima = i == 0
                w.writerow([
                    t.materie if prima else "", m.user.nume if m else "", (m.user.email or "") if m else "",
                    f"{ZILE_RO[t.zi_saptamana]} {t.interval.replace('-', ' - ')}" if prima else "",
                    t.unde if prima else "", (t.link_whatsapp or "") if prima else "", (t.link_teams or "") if prima else "",
                ])  # fmt: skip
        w.writerow([])
    return Response(
        iesire.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="tutoriate.csv"'},
    )
