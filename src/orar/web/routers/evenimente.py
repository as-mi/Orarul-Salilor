"""Evenimente: activitati ASMI si alte activitati cu data, in afara orarului saptamanal.

Un admin spune cand are nevoie de o sala; situl ii arata salile libere atunci, de la cea mai
incapatoare, iar el alege una -- sau niciuna: un eveniment poate fi si in alt loc (Balul
Bobocilor) sau doar o perioada (formularul de recrutari). Evenimentele ASMI apar in
calendarul public `/calendar-asmi`; pe orare apar dupa vizibilitatea aleasa.
"""

from __future__ import annotations

import calendar as cal
import re
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from orar.db.evenimente import cheie_an, evenimente_asmi, sali_libere
from orar.db.models import (
    CULORI_EVENIMENT,
    FELURI_EVENIMENT,
    VIZIBILITATI_EVENIMENT,
    Eveniment,
    Grupa,
    Sala,
)
from orar.db.orare import perioada_implicita
from orar.db.structura import perioade_anului
from orar.domain.hierarchy import DENUMIRI_SPECIALIZARE
from orar.domain.weeks import FELURI_PERIOADA, an_universitar_al
from orar.web.auth import Cont, cere_admin, verifica_csrf
from orar.web.deps import calendar, context_saptamana, get_db, templates

router = APIRouter(tags=["evenimente"])

LUNI = (
    "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
)  # fmt: skip
ZILE_RO = ("luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică")
ETICHETE_FEL = {"asmi": "Activitate ASMI", "alta": "Altă activitate"}
#: Valoarea din formular pentru "altă culoare": culoarea vine in campul `culoare_proprie`.
CULOARE_PROPRIE = "proprie"
_RE_CULOARE = re.compile(r"#[0-9a-f]{6}")


# ------------------------------------------------------------------ afisare


def cand(e: Eveniment) -> str:
    """Cand e evenimentul, in cuvinte: `sâmbătă, 17 octombrie 2026 · 18:00-21:00`."""

    def zi(d: date, *, cu_an: bool = True, cu_luna: bool = True) -> str:
        text = str(d.day)
        if cu_luna:
            text += f" {LUNI[d.month - 1]}"
        return f"{text} {d.year}" if cu_an else text

    a, b = e.data_inceput, e.data_sfarsit
    if a == b:
        text = f"{ZILE_RO[a.weekday()]}, {zi(a)}"
    elif (a.year, a.month) == (b.year, b.month):
        text = f"{zi(a, cu_an=False, cu_luna=False)}-{zi(b)}"
    else:
        text = f"{zi(a, cu_an=a.year != b.year)} - {zi(b)}"
    if e.ora_inceput is not None:
        text += f" · {e.ora_inceput:%H:%M}-{e.ora_sfarsit:%H:%M}"
    return text


def _specializari(s: Session) -> list[tuple[str, str]]:
    """(cod, denumire) pentru specializarile din orar: cele cunoscute intai, in ordinea lor."""
    coduri = set(s.scalars(select(Grupa.specializare).where(Grupa.specializare.is_not(None))))
    cunoscute = [c for c in DENUMIRI_SPECIALIZARE if c in coduri]
    return [
        (c, DENUMIRI_SPECIALIZARE.get(c, c)) for c in (*cunoscute, *sorted(coduri - set(cunoscute)))
    ]


def _ani(s: Session) -> list[tuple[str, str]]:
    """(cheie, eticheta) pentru anii de studiu din orar: `("L2", "Licență · anul 2")`."""
    chei = {
        cheie_an(g)
        for g in s.scalars(
            select(Grupa).where(Grupa.tip == "specializare", Grupa.an_studiu.is_not(None))
        )
    }
    return [
        (c, f"{'Master' if c[0] == 'M' else 'Licență'} · anul {c[1:]}")
        for c in sorted(chei - {None})
    ]


def descrie_orarele(e: Eveniment) -> str:
    """Pe ce orare apare un eveniment cu vizibilitatea `specializari`, in cuvinte."""
    parti = []
    if e.coduri:
        parti.append(", ".join(DENUMIRI_SPECIALIZARE.get(c, c) for c in e.coduri))
    if e.ani_alesi:
        ani = [f"{'master' if a[0] == 'M' else 'licență'} anul {a[1:]}" for a in e.ani_alesi]
        parti.append(", ".join(ani))
    return " · ".join(parti)


# ------------------------------------------------------------ calendarul public


@router.get("/calendar-asmi", response_class=HTMLResponse)
def calendar_asmi(
    request: Request,
    luna: str = Query("", description="luna afisata, AAAA-LL; implicit cea curenta"),
    s: Session = Depends(get_db),
) -> HTMLResponse:
    azi = date.today()
    try:
        an, nr = (int(x) for x in luna.split("-"))
        prima = date(an, nr, 1)
    except ValueError:
        prima = azi.replace(day=1)
    ultima = prima.replace(day=cal.monthrange(prima.year, prima.month)[1])
    # saptamani intregi, de luni pana duminica
    start = prima - timedelta(days=prima.weekday())
    sfarsit = ultima + timedelta(days=6 - ultima.weekday())

    in_luna = evenimente_asmi(s, start, sfarsit)
    saptamani = []
    zi = start
    while zi <= sfarsit:
        saptamani.append(
            [
                (d, [e for e in in_luna if e.data_inceput <= d <= e.data_sfarsit])
                for d in (zi + timedelta(days=i) for i in range(7))
            ]
        )
        zi += timedelta(days=7)

    # saptamanile academice si perioadele anului (vacante, sesiuni), din structura anului
    academic = calendar(s)
    zile: dict[date, dict[str, object]] = {}
    #: saptamana academica a fiecarui rand al grilei (prima gasita), pentru coloana din stanga
    sapt_randuri = []
    for rand in saptamani:
        sapt_randuri.append(next((s_ for d, _ in rand if (s_ := academic.saptamana(d))), None))
        vazute: set[int] = set()
        for d, _ in rand:
            sapt = academic.saptamana(d)
            perioada = academic.perioada(d)
            zile[d] = {
                "sapt": sapt,
                # eticheta saptamanii apare o data pe rand, in prima ei zi
                "nou": sapt is not None and sapt.numar not in vazute,
                "perioada": perioada if perioada and perioada.fel != "didactica" else None,
            }
            if sapt is not None:
                vazute.add(sapt.numar)
    an = an_universitar_al(prima)

    urmeaza = evenimente_asmi(s, azi, date.max)
    trecute = evenimente_asmi(s, date.min, azi - timedelta(days=1))
    trecute = [e for e in trecute if e.data_sfarsit < azi][::-1][:30]
    inainte = (prima - timedelta(days=1)).replace(day=1)
    dupa = ultima + timedelta(days=1)
    return templates.TemplateResponse(
        request=request,
        name="calendar_asmi.html",
        context={
            **context_saptamana(s=s),
            "azi_real": azi,
            "prima": prima,
            "titlu_luna": f"{LUNI[prima.month - 1]} {prima.year}",
            "saptamani": saptamani,
            "zile": zile,
            "sapt_randuri": sapt_randuri,
            "an": an,
            # perioadele de aratat in lista: fara completarile de saptamana
            "structura": [
                p for p in perioade_anului(s, an) if p.fel != "didactica" or p.saptamana is None
            ],
            "feluri_perioada": FELURI_PERIOADA,
            "zile_scurte": ("Lu", "Ma", "Mi", "Jo", "Vi", "Sâ", "Du"),
            "luna_inainte": f"{inainte:%Y-%m}",
            "luna_dupa": f"{dupa:%Y-%m}",
            "e_luna_curenta": (prima.year, prima.month) == (azi.year, azi.month),
            "urmeaza": urmeaza,
            "trecute": trecute,
            "cand": cand,
        },
    )


# ------------------------------------------------------------------ admin


@router.get("/admin/evenimente", response_class=HTMLResponse)
def lista(
    request: Request, s: Session = Depends(get_db), _admin: Cont = Depends(cere_admin)
) -> HTMLResponse:
    toate = list(
        s.scalars(
            select(Eveniment)
            .options(joinedload(Eveniment.sala))
            .order_by(Eveniment.data_inceput, Eveniment.ora_inceput.nulls_first())
        )
    )
    azi = date.today()
    return templates.TemplateResponse(
        request=request,
        name="admin_evenimente.html",
        context={
            **context_saptamana(),
            "urmeaza": [e for e in toate if e.data_sfarsit >= azi],
            "trecute": [e for e in toate if e.data_sfarsit < azi][::-1],
            "cand": cand,
            "etichete_fel": ETICHETE_FEL,
            "descrie_orarele": descrie_orarele,
        },
    )


def _ora(text: str) -> time | None:
    try:
        return time.fromisoformat(text.strip()) if text.strip() else None
    except ValueError:
        return None


def _data(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip()) if text.strip() else None
    except ValueError:
        return None


def _libere(s: Session, zi: date, inceput: time, sfarsit: time, fara: int | None):  # noqa: ANN202
    orar = perioada_implicita(s)
    libere = sali_libere(
        s,
        zi,
        inceput,
        sfarsit,
        perioada_id=orar.id if orar else None,
        saptamana=calendar(s).saptamana(zi),
        fara_eveniment=fara,
    )
    return libere, orar


def _context_sali(
    s: Session, zi: date, inceput: time, sfarsit: time, fara: int | None
) -> dict[str, object]:
    """Ce are nevoie `_sali_libere.html` ca sa arate salile libere intr-un interval."""
    libere, orar = _libere(s, zi, inceput, sfarsit, fara)
    return {
        "libere": libere,
        "orar": orar,
        "zi_cautata": f"{ZILE_RO[zi.weekday()]}, {zi:%d.%m.%Y}",
        "interval": f"{inceput:%H:%M}-{sfarsit:%H:%M}",
        "weekend": zi.weekday() >= 5,
        "in_semestru_cautat": calendar(s).saptamana(zi) is not None,
    }


@router.get("/admin/evenimente/sali-libere", response_class=HTMLResponse)
def cauta_sali(
    request: Request,
    data_inceput: str = Query(""),
    ora_inceput: str = Query(""),
    ora_sfarsit: str = Query(""),
    eveniment: str = Query("", description="evenimentul editat, ca sala lui sa ramana"),
    sala: str = Query("", description="sala aleasa pana acum"),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> HTMLResponse:
    """Salile libere in ziua si la orele date, de la cea mai incapatoare."""
    zi, inceput, sfarsit = _data(data_inceput), _ora(ora_inceput), _ora(ora_sfarsit)
    ctx: dict[str, object] = {"sala_aleasa": sala, "libere": None, "eroare_sali": ""}
    if zi is None or inceput is None or sfarsit is None:
        ctx["eroare_sali"] = "Alege întâi data și orele, apoi caută sălile libere."
    elif inceput >= sfarsit:
        ctx["eroare_sali"] = "Ora de sfârșit trebuie să fie după cea de început."
    else:
        ctx |= _context_sali(
            s, zi, inceput, sfarsit, int(eveniment) if eveniment.isdigit() else None
        )
    return templates.TemplateResponse(request=request, name="_sali_libere.html", context=ctx)


def _valori(e: Eveniment | None) -> dict[str, object]:
    if e is None:
        return {
            "fel": "asmi", "titlu": "", "descriere": "", "link": "", "data_inceput": "",
            "data_sfarsit": "", "ora_inceput": "", "ora_sfarsit": "", "unde": "sala", "sala": "",
            "loc": "", "vizibilitate": "sala", "specializari": [], "ani": [], "culoare": "",
        }  # fmt: skip
    return {
        "fel": e.fel,
        "titlu": e.titlu,
        "descriere": e.descriere or "",
        "link": e.link or "",
        "data_inceput": e.data_inceput.isoformat(),
        "data_sfarsit": e.data_sfarsit.isoformat() if e.pe_mai_multe_zile else "",
        "ora_inceput": f"{e.ora_inceput:%H:%M}" if e.ora_inceput else "",
        "ora_sfarsit": f"{e.ora_sfarsit:%H:%M}" if e.ora_sfarsit else "",
        "unde": "sala" if e.sala else "alt" if e.loc else "fara",
        "sala": e.sala.slug if e.sala else "",
        "loc": e.loc or "",
        "vizibilitate": e.vizibilitate,
        "specializari": e.coduri,
        "ani": e.ani_alesi,
        "culoare": e.culoare or "",
    }


def _formular(
    request: Request,
    s: Session,
    *,
    e: Eveniment | None,
    v: dict[str, object],
    eroare: str = "",
    stare: int = 200,
) -> HTMLResponse:
    # cu data si orele deja alese, aratam direct salile libere
    ctx_sali: dict[str, object] = {"libere": None, "eroare_sali": "", "sala_aleasa": v["sala"]}
    zi, inceput, sfarsit = _data(v["data_inceput"]), _ora(v["ora_inceput"]), _ora(v["ora_sfarsit"])
    if zi and inceput and sfarsit and inceput < sfarsit:
        ctx_sali |= _context_sali(s, zi, inceput, sfarsit, e.id if e else None)
    return templates.TemplateResponse(
        request=request,
        name="admin_eveniment.html",
        context={
            **context_saptamana(),
            **ctx_sali,
            "e": e,
            "v": v,
            "eroare": eroare,
            "etichete_fel": ETICHETE_FEL,
            "specializari": _specializari(s),
            "ani": _ani(s),
            "culori": CULORI_EVENIMENT,
            "cand": cand,
        },
        status_code=stare,
    )


@router.get("/admin/evenimente/nou", response_class=HTMLResponse)
def formular_nou(
    request: Request,
    fel: str = Query("asmi"),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> HTMLResponse:
    v = _valori(None)
    # un eveniment nou porneste de la ziua de azi
    v["data_inceput"] = date.today().isoformat()
    if fel in FELURI_EVENIMENT:
        v["fel"] = fel
    return _formular(request, s, e=None, v=v)


def _eveniment(s: Session, eveniment_id: int) -> Eveniment:
    e = s.get(Eveniment, eveniment_id)
    if e is None:
        raise HTTPException(404, "Evenimentul nu există.")
    return e


@router.get("/admin/evenimente/{eveniment_id}", response_class=HTMLResponse)
def formular_editare(
    request: Request,
    eveniment_id: int,
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> HTMLResponse:
    e = _eveniment(s, eveniment_id)
    return _formular(request, s, e=e, v=_valori(e))


def _din_formular(
    fel: str = Form("asmi"),
    titlu: str = Form(""),
    descriere: str = Form(""),
    link: str = Form(""),
    data_inceput: str = Form(""),
    data_sfarsit: str = Form(""),
    ora_inceput: str = Form(""),
    ora_sfarsit: str = Form(""),
    unde: str = Form("fara"),
    sala: str = Form(""),
    loc: str = Form(""),
    vizibilitate: str = Form("sala"),
    specializari: list[str] = Form([]),
    ani: list[str] = Form([]),
    culoare: str = Form(""),
    culoare_proprie: str = Form(""),
) -> dict[str, object]:
    if culoare == CULOARE_PROPRIE:
        culoare = culoare_proprie.strip().lower()
    return {
        "fel": fel, "titlu": titlu.strip(), "descriere": descriere.strip(), "link": link.strip(),
        "data_inceput": data_inceput, "data_sfarsit": data_sfarsit, "ora_inceput": ora_inceput,
        "ora_sfarsit": ora_sfarsit, "unde": unde, "sala": sala, "loc": loc.strip(),
        "vizibilitate": vizibilitate, "specializari": specializari, "ani": ani,
        "culoare": culoare,
    }  # fmt: skip


def _aplica(s: Session, e: Eveniment, v: dict[str, object]) -> str:
    """Pune valorile din formular pe eveniment. Intoarce mesajul de eroare, sau ""."""
    if v["fel"] not in FELURI_EVENIMENT:
        return "Alege felul activității."
    if not v["titlu"]:
        return "Scrie numele evenimentului."
    if len(v["titlu"]) > 160:
        return "Numele e prea lung (cel mult 160 de caractere)."
    if v["link"] and not str(v["link"]).startswith(("https://", "http://")):
        return "Linkul trebuie să înceapă cu https://."

    prima = _data(v["data_inceput"])
    if prima is None:
        return "Alege data."
    ultima = _data(v["data_sfarsit"]) or prima
    if ultima < prima:
        return "Data de sfârșit e înaintea celei de început."

    inceput, sfarsit = _ora(v["ora_inceput"]), _ora(v["ora_sfarsit"])
    if (inceput is None) != (sfarsit is None):
        return "Scrie și ora de început, și ora de sfârșit - sau lasă-le goale pe amândouă."
    if inceput is not None and inceput >= sfarsit:
        return "Ora de sfârșit trebuie să fie după cea de început."

    sala = None
    if v["unde"] == "sala":
        if inceput is None:
            return "Pentru a rezerva o sală, scrie orele între care e nevoie de ea."
        if ultima != prima:
            return "O sală se rezervă pentru o singură zi. Pentru mai multe zile, fă câte un eveniment."
        sala = s.scalar(select(Sala).where(Sala.slug == v["sala"], Sala.tip == "fizica"))
        if sala is None:
            return "Alege o sală din lista celor libere."
        libere, _ = _libere(s, prima, inceput, sfarsit, e.id)
        if sala.id not in {x.sala.id for x in libere}:
            return f"{sala.nume} nu mai e liberă atunci. Alege alta din listă."
    elif v["unde"] == "alt" and not v["loc"]:
        return "Scrie locul în care se ține."

    if v["vizibilitate"] not in VIZIBILITATI_EVENIMENT:
        return "Alege unde apare evenimentul."
    coduri = [c for c, _ in _specializari(s) if c in v["specializari"]]
    ani = [a for a, _ in _ani(s) if a in v["ani"]]
    if v["vizibilitate"] == "specializari" and not (coduri or ani):
        return "Bifează cel puțin o specializare sau un an."

    e.fel, e.titlu = v["fel"], v["titlu"]
    e.descriere, e.link = v["descriere"] or None, v["link"] or None
    e.data_inceput, e.data_sfarsit = prima, ultima
    e.ora_inceput, e.ora_sfarsit = inceput, sfarsit
    e.sala = sala
    e.loc = v["loc"][:160] if v["unde"] == "alt" else None
    e.vizibilitate = v["vizibilitate"]
    e.specializari = ",".join(coduri) if v["vizibilitate"] == "specializari" and coduri else None
    e.ani = ",".join(ani) if v["vizibilitate"] == "specializari" and ani else None
    # o culoare necunoscuta (formular vechi, valoare scrisa de mana) inseamna "cea obisnuita"
    # sau una proprie, dar numai in forma `#rrggbb`: ajunge intr-un atribut `style`
    proprie = _RE_CULOARE.fullmatch(str(v["culoare"]))
    e.culoare = v["culoare"] if proprie or v["culoare"] in dict(CULORI_EVENIMENT) else None
    return ""


@router.post("/admin/evenimente/nou", response_model=None)
def adauga(
    request: Request,
    v: dict[str, object] = Depends(_din_formular),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    admin: Cont = Depends(cere_admin),
) -> HTMLResponse | RedirectResponse:
    if not verifica_csrf(request, csrf):
        return _formular(request, s, e=None, v=v, stare=400, eroare="Formularul a expirat.")
    e = Eveniment(creat_de=admin.nume, creat_la=datetime.now())
    if eroare := _aplica(s, e, v):
        return _formular(request, s, e=None, v=v, eroare=eroare, stare=400)
    s.add(e)
    s.commit()
    return RedirectResponse("/admin/evenimente?salvat=1", status_code=303)


@router.post("/admin/evenimente/{eveniment_id}", response_model=None)
def modifica(
    request: Request,
    eveniment_id: int,
    v: dict[str, object] = Depends(_din_formular),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> HTMLResponse | RedirectResponse:
    e = _eveniment(s, eveniment_id)
    if not verifica_csrf(request, csrf):
        return _formular(request, s, e=e, v=v, stare=400, eroare="Formularul a expirat.")
    if eroare := _aplica(s, e, v):
        s.rollback()
        return _formular(request, s, e=_eveniment(s, eveniment_id), v=v, eroare=eroare, stare=400)
    s.commit()
    return RedirectResponse("/admin/evenimente?salvat=1", status_code=303)


@router.post("/admin/evenimente/{eveniment_id}/sterge")
def sterge(
    request: Request,
    eveniment_id: int,
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> RedirectResponse:
    if verifica_csrf(request, csrf):
        s.delete(_eveniment(s, eveniment_id))
        s.commit()
    return RedirectResponse("/admin/evenimente", status_code=303)
