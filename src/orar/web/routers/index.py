"""Pagina principala: cautare si navigare in arborele de formatiuni."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orar.db.evenimente import evenimente_asmi
from orar.db.models import Grupa, Ora, OraArhivata, OraGrupa, OraGrupaArhivata, Sala
from orar.db.versiuni import versiuni_generale
from orar.domain.hierarchy import DENUMIRI_SPECIALIZARE, este_master
from orar.web.deps import (
    Selectie,
    context_saptamana,
    context_versiuni,
    get_db,
    selectie,
    templates,
)

router = APIRouter(tags=["index"])

LUNI_SCURTE = ("ian", "feb", "mar", "apr", "mai", "iun", "iul", "aug", "sep", "oct", "noi", "dec")


@router.get("/acasa", include_in_schema=False)
def acasa_vechi() -> RedirectResponse:
    """Adresa veche a listei complete (cand `/` ducea la grupa din cont). Acum `/` e lista."""
    return RedirectResponse("/", status_code=308)


def _formatiuni_prezente(s: Session, sel: Selectie) -> set[int]:
    """Nodurile GRUPA care au un orar in selectie: cele care detin sau primesc ore, plus
    stramosii lor (pagina anului arata orele grupelor) si descendentii (grupa mosteneste
    cursurile seriei)."""
    if sel.versiune is not None:
        ora, legatura = OraArhivata, OraGrupaArhivata
        filtru = OraArhivata.versiune_id == sel.versiune_id
    else:
        ora, legatura = Ora, OraGrupa
        filtru = Ora.perioada_id == sel.perioada_id
    cu_ore = set(s.scalars(select(ora.grupa_id).where(filtru).distinct()))
    cu_ore |= set(
        s.scalars(
            select(legatura.grupa_id).join(ora, ora.id == legatura.ora_id).where(filtru).distinct()
        )
    )

    parinte = dict(s.execute(select(Grupa.id, Grupa.parinte_id)).all())
    copii: dict[int, list[int]] = {}
    for nod, p in parinte.items():
        if p is not None:
            copii.setdefault(p, []).append(nod)

    prezente = set(cu_ore)
    for nod in cu_ore:  # in sus
        p = parinte.get(nod)
        while p is not None and p not in prezente:
            prezente.add(p)
            p = parinte.get(p)
    de_vizitat = list(cu_ore)  # in jos
    while de_vizitat:
        for c in copii.get(de_vizitat.pop(), []):
            if c not in prezente:
                prezente.add(c)
                de_vizitat.append(c)
    return prezente


@dataclass
class An:
    an: int | None
    #: Nodul anului (pagina cu orarul intregului an).
    nod: Grupa
    grupe: list[Grupa]
    #: Pachetele de optionale, doar cand anul n-are nicio pagina de grupa.
    pachete: list[Grupa]


@dataclass
class Domeniu:
    nume: str
    ani: list[An] = field(default_factory=list)


@dataclass
class NivelStudii:
    nume: str
    #: `licenta` / `master`, pentru stilul paginii principale.
    cheie: str = ""
    domenii: list[Domeniu] = field(default_factory=list)

    @property
    def ani(self) -> list[int | None]:
        """Anii de studiu ai nivelului, in ordine: coloanele tabelului de pe pagina
        principala, ca "Anul 2" sa cada in acelasi loc la toate domeniile."""
        return sorted({a.an for d in self.domenii for a in d.ani}, key=lambda an: an or 0)


def _niveluri(
    specializari: list[Grupa],
    grupe: dict[int, list[Grupa]],
    pachete: dict[int, list[Grupa]],
) -> list[NivelStudii]:
    """Formatiunile, grupate pe licenta / master, apoi pe domeniu, an si grupa.

    Nimic nu e scris de mana: un domeniu sau un an nou aparut in orar isi gaseste singur
    locul, iar unul disparut nu lasa o rubrica goala. Domeniile cunoscute vin in ordinea din
    `DENUMIRI_SPECIALIZARE`, cele noi dupa ele, alfabetic.
    """
    ordine = {cod: i for i, cod in enumerate(DENUMIRI_SPECIALIZARE)}
    niveluri = {False: NivelStudii("Licență", "licenta"), True: NivelStudii("Master", "master")}
    domenii: dict[tuple[bool, str], Domeniu] = {}
    for spec in sorted(
        specializari,
        key=lambda g: (
            ordine.get(g.specializare, len(ordine)),
            g.specializare or "",
            g.an_studiu or 0,
        ),
    ):
        if not (grupe.get(spec.id) or pachete.get(spec.id)):
            continue
        master = este_master(spec.nume)
        cod = spec.specializare or spec.nume
        domeniu = domenii.get((master, cod))
        if domeniu is None:
            domeniu = domenii[master, cod] = Domeniu(DENUMIRI_SPECIALIZARE.get(cod, cod))
            niveluri[master].domenii.append(domeniu)
        domeniu.ani.append(
            An(spec.an_studiu, spec, grupe.get(spec.id, []), pachete.get(spec.id, []))
        )
    return [n for n in niveluri.values() if n.domenii]


#: Felul salii, dupa prefixul numelui (`Amf.701`, `L.410`, `S.102`): coloanele tabelului.
FELURI_SALA = {"amf": "Amfiteatre", "l": "Laboratoare", "s": "Săli de seminar"}
ALTE_SALI = "Altele"


@dataclass
class Etaj:
    nume: str
    #: Salile etajului, pe feluri.
    sali: dict[str, list[Sala]] = field(default_factory=dict)


def _pe_etaje(sali: list[Sala]) -> tuple[list[str], list[Etaj]]:
    """Salile grupate pe etaje: etajul e prima cifra din nume (`L.410` -> etajul 4).

    Intoarce si felurile de sali intalnite, in ordinea coloanelor. O sala fara nicio cifra
    in nume ajunge pe un rand separat, la sfarsit.
    """
    etaje: dict[int, Etaj] = {}
    for sala in sali:
        cifra = re.search(r"\d", sala.nume)
        nr = int(cifra.group()) if cifra else 99
        if nr not in etaje:
            nume = "Parter" if nr == 0 else "Alte săli" if nr == 99 else f"Etajul {nr}"
            etaje[nr] = Etaj(nume)
        prefix = re.match(r"[^\W\d_]+", sala.nume)
        fel = FELURI_SALA.get(prefix.group().lower() if prefix else "", ALTE_SALI)
        etaje[nr].sali.setdefault(fel, []).append(sala)
    intalnite = {fel for e in etaje.values() for fel in e.sali}
    feluri = [f for f in (*FELURI_SALA.values(), ALTE_SALI) if f in intalnite]
    return feluri, [etaje[nr] for nr in sorted(etaje)]


@router.get("/", response_class=HTMLResponse)
def acasa(
    request: Request,
    versiune: int | None = Query(None, description="o versiune anterioara a orarului"),
    perioada: int | None = Query(None, description="alt orar decat cel implicit"),
    s: Session = Depends(get_db),
) -> HTMLResponse:
    # Cu alt orar ales (sau o versiune anterioara), grupele si salile de pe pagina se
    # deschid in el.
    sel = selectie(s, perioada, versiune)
    arhiva = sel.versiune
    # Doar formatiunile care au ceva in orarul ales: un pachet de optionale care exista
    # numai in semestrul 1 n-are ce cauta pe pagina semestrului 2.
    prezente = _formatiuni_prezente(s, sel)

    def din_orar(tip: str, *ordine) -> list[Grupa]:  # noqa: ANN002
        return [
            g
            for g in s.execute(select(Grupa).where(Grupa.tip == tip).order_by(*ordine)).scalars()
            if g.id in prezente
        ]

    specializari = din_orar("specializare", Grupa.specializare, Grupa.an_studiu)
    # Grupele reale, gata de afisat sub fiecare specializare.
    grupe = din_orar("grupa", Grupa.nume)
    pachete = din_orar("optional", Grupa.nume)
    sali = list(s.execute(select(Sala).where(Sala.tip == "fizica").order_by(Sala.nume)).scalars())

    pe_specializare: dict[int, list[Grupa]] = {}
    for g in grupe:
        # urcam la nodul de specializare (grupa -> serie -> specializare)
        nod = g
        while nod.parinte is not None and nod.tip != "specializare":
            nod = nod.parinte
        if nod.tip == "specializare":
            pe_specializare.setdefault(nod.id, []).append(g)

    # Anii fara nicio pagina de grupa in orar (CTI anul 4: doar optionale) se afiseaza cu
    # pachetele lor, ca pagina anului sa nu ramana fara niciun drum spre ea.
    pachete_an: dict[int, list[Grupa]] = {}
    for p in pachete:
        if p.parinte_id is not None and p.parinte_id not in pe_specializare:
            pachete_an.setdefault(p.parinte_id, []).append(p)

    niveluri = _niveluri(specializari, pe_specializare, pachete_an)

    feluri_sali, etaje = _pe_etaje(sali)
    ctx_sapt = context_saptamana(s=s)
    versiuni = versiuni_generale(s, perioada=sel.perioada)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            **ctx_sapt,
            **context_versiuni(s, sel, versiuni),
            "niveluri": niveluri,
            # urmatoarele evenimente ASMI, pentru caseta calendarului
            "evenimente_asmi": evenimente_asmi(s, date.today(), date.max)[:3],
            "luni_scurte": LUNI_SCURTE,
            # pentru selectorul in trepte: nivel -> domeniu -> an -> grupa
            "arbore": [
                {
                    "nume": n.nume,
                    "domenii": [
                        {
                            "nume": d.nume,
                            "ani": [
                                {
                                    "an": a.an,
                                    "slug": a.nod.slug,
                                    "grupe": [
                                        {"slug": g.slug, "nume": g.nume}
                                        for g in (*a.grupe, *a.pachete)
                                    ],
                                }
                                for a in d.ani
                            ],
                        }
                        for d in n.domenii
                    ],
                }
                for n in niveluri
            ],
            "pachete": pachete,
            "sali": sali,
            "feluri_sali": feluri_sali,
            "etaje": etaje,
            "total_ore": arhiva.nr_ore
            if arhiva
            else s.scalar(
                select(func.count()).select_from(Ora).where(Ora.perioada_id == sel.perioada_id)
            ),
        },
    )


@router.get("/cauta", response_class=HTMLResponse)
def cauta(
    request: Request,
    q: str = Query("", min_length=0),
    s: Session = Depends(get_db),
) -> HTMLResponse:
    """Cautare incrementala (HTMX) in grupe si sali."""
    termen = q.strip()
    grupe: list[Grupa] = []
    sali: list[Sala] = []
    if termen:
        tipar = f"%{termen}%"
        grupe = list(
            s.execute(
                select(Grupa)
                # si paginile de an ("Calculatoare ... anul 4"), nu doar grupe/serii/pachete
                .where(
                    Grupa.nume.ilike(tipar),
                    Grupa.tip.in_(("grupa", "optional", "serie", "specializare")),
                )
                .order_by(Grupa.tip.desc(), Grupa.nume)
                .limit(12)
            ).scalars()
        )
        sali = list(
            s.execute(
                select(Sala).where(Sala.nume.ilike(tipar)).order_by(Sala.nume).limit(8)
            ).scalars()
        )

    return templates.TemplateResponse(
        request=request,
        name="_rezultate.html",
        context={"grupe": grupe, "sali": sali, "termen": termen},
    )
