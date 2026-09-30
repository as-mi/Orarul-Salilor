"""Incarca un set de pagini de orar in baza de date.

Sursa e o lista de pagini in formatul descris de `docs/formatul-orarului.md` -- azi
`tests/golden/date.json`, maine iesirea parserului determinist (Etapa 5). Formatul e
acelasi, deci loader-ul nu se schimba cand inlocuim sursa.

Loader-ul e **idempotent**: rulat de doua ori pe aceleasi date, produce aceeasi baza.
Cheile naturale (numele profesorului, slug-ul salii, slug-ul grupei) fac deduplicarea.

Constructia ierarhiei
---------------------
Pentru fiecare pagina, titlul e decodat de `domain.hierarchy.parse_titlu`, iar nodurile
lipsa din lantul Specializare -> Serie -> Grupa se creeaza la nevoie. Semigrupele apar
din etichetele `Gr_N` gasite in celule, nu din titlu.

Repartizarea orelor
-------------------
  - Ora dintr-o pagina de grupa cu `semigrupa=Gr_N`  -> nodul semigrupei
  - Ora dintr-o pagina de grupa fara semigrupa       -> nodul grupei
  - Ora dintr-o pagina de optionale/facultative      -> nodul pachetului (tip='optional'),
    plus legaturi ORA_GRUPA catre toate grupele-tinta
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orar.db.models import Grupa, Materie, Ora, OraGrupa, Perioada, Profesor, Sala
from orar.domain.hierarchy import (
    DENUMIRI_SPECIALIZARE,
    Nivel,
    TipPagina,
    TitluOrar,
    este_master,
    normalizeaza_semigrupa,
    parse_titlu,
    specializari_vizate,
)
from orar.domain.rooms import normalizeaza_sala

log = logging.getLogger(__name__)

ZILE_JSON = ("Luni", "Marti", "Miercuri", "Joi", "Vineri")

__all__ = ["Raport", "incarca_pagini", "incarca_din_json"]


@dataclass
class Raport:
    """Ce s-a intamplat la incarcare -- afisat la final si folosit in teste."""

    pagini: int = 0
    ore: int = 0
    grupe: int = 0
    profesori: int = 0
    materii: int = 0
    sali: int = 0
    legaturi_partajate: int = 0
    ignorate: int = 0
    avertismente: list[str] = field(default_factory=list)
    #: Slug-urile nodurilor GRUPA pe care le contine publicarea incarcata. Ce nu e aici a
    #: disparut din orar (o serie desfiintata, un pachet redenumit) -- vezi
    #: `worker.sync.curata_grupele_disparute`.
    noduri: set[str] = field(default_factory=set)

    def __str__(self) -> str:
        linii = [
            f"pagini procesate : {self.pagini}",
            f"ore inserate     : {self.ore}",
            f"noduri GRUPA     : {self.grupe}",
            f"profesori        : {self.profesori}",
            f"materii          : {self.materii}",
            f"sali             : {self.sali}",
            f"legaturi ORA_GRUPA: {self.legaturi_partajate}",
        ]
        if self.ignorate:
            linii.append(f"activitati ignorate: {self.ignorate}")
        if self.avertismente:
            linii.append(f"avertismente     : {len(self.avertismente)}")
        return "\n".join(linii)


# ---------------------------------------------------------------------------
# Cache de entitati, ca sa nu lovim baza pentru fiecare celula
# ---------------------------------------------------------------------------


class _Cache:
    def __init__(self, s: Session, an_universitar: str) -> None:
        self.s = s
        self.an = an_universitar
        #: Nodurile cerute de paginile incarcate acum, noi sau existente.
        self.atinse: set[str] = set()
        self.profesori: dict[str, Profesor] = {
            p.nume: p for p in s.execute(select(Profesor)).scalars()
        }
        self.materii: dict[str, Materie] = {m.slug: m for m in s.execute(select(Materie)).scalars()}
        self.sali: dict[str, Sala] = {r.slug: r for r in s.execute(select(Sala)).scalars()}
        self.grupe: dict[str, Grupa] = (
            {g.slug: g for g in s.execute(select(Grupa)).scalars() if g.an_universitar == an}
            if (an := an_universitar)
            else {}
        )

    # -- profesor ---------------------------------------------------------
    def profesor(self, nume: str | None) -> Profesor | None:
        nume = (nume or "").strip()
        if not nume:
            return None
        if p := self.profesori.get(nume):
            return p
        p = Profesor(nume=nume, slug=_slug(nume))
        # Slug-urile pot coliziona ("Popa A" vs "Popa-A"); dezambiguizam cu un sufix.
        if any(x.slug == p.slug for x in self.profesori.values()):
            p.slug = f"{p.slug}-{len(self.profesori)}"
        self.s.add(p)
        self.profesori[nume] = p
        return p

    # -- materie ----------------------------------------------------------
    def materie(self, nume: str | None) -> Materie | None:
        nume = (nume or "").strip()
        if not nume:
            return None
        slug = _slug(nume)
        if m := self.materii.get(slug):
            return m
        m = Materie(nume=nume, slug=slug)
        self.s.add(m)
        self.materii[slug] = m
        return m

    # -- sala -------------------------------------------------------------
    def sala(self, raw: str | None) -> Sala:
        norm = normalizeaza_sala(raw)
        if r := self.sali.get(norm.slug):
            return r
        r = Sala(nume=norm.nume, slug=norm.slug, tip=norm.tip.value)
        self.s.add(r)
        self.sali[norm.slug] = r
        return r

    # -- grupa ------------------------------------------------------------
    def grupa(
        self,
        slug: str,
        nume: str,
        nivel: Nivel,
        *,
        parinte: Grupa | None = None,
        specializare: str | None = None,
        an_studiu: int | None = None,
    ) -> Grupa:
        self.atinse.add(slug)
        if g := self.grupe.get(slug):
            # Completam parintele daca nodul a fost creat mai devreme fara el.
            if parinte is not None and g.parinte_id is None and g is not parinte:
                g.parinte = parinte
            return g
        g = Grupa(
            slug=slug,
            nume=nume,
            tip=nivel.value,
            an_universitar=self.an,
            parinte=parinte,
            specializare=specializare,
            an_studiu=an_studiu,
        )
        self.s.add(g)
        self.grupe[slug] = g
        return g


def _slug(text: str) -> str:
    text = text.lower()
    for a, b in (
        ("ă", "a"),
        ("â", "a"),
        ("î", "i"),
        ("ș", "s"),
        ("ş", "s"),
        ("ț", "t"),
        ("ţ", "t"),
    ):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


# ---------------------------------------------------------------------------
# Ierarhia
# ---------------------------------------------------------------------------


def _lant_ierarhic(cache: _Cache, t: TitluOrar) -> Grupa:
    """Creeaza (sau regaseste) nodul paginii, impreuna cu stramosii lui."""
    spec = t.specializare
    parinte: Grupa | None = None

    if spec and t.an:
        den = DENUMIRI_SPECIALIZARE.get(spec, spec)
        eticheta = f"{den} - anul {t.an}"
        if t.tip is TipPagina.MASTER:
            eticheta = f"{den} - master, anul {t.an}"
        parinte = cache.grupa(
            _slug(f"{spec}-an-{t.an}" + ("-master" if t.tip is TipPagina.MASTER else "")),
            eticheta,
            Nivel.SPECIALIZARE,
            specializare=spec,
            an_studiu=t.an,
        )

    if t.tip is TipPagina.GRUPA:
        if t.serie:
            parinte = cache.grupa(
                _slug(f"seria-{t.serie}"),
                f"Seria {t.serie}",
                Nivel.SERIE,
                parinte=parinte,
                specializare=spec,
                an_studiu=t.an,
            )
        return cache.grupa(
            t.slug,
            t.grupa or t.raw,
            Nivel.GRUPA,
            parinte=parinte,
            specializare=spec,
            an_studiu=t.an,
        )

    if t.tip is TipPagina.MASTER:
        return cache.grupa(
            t.slug,
            t.eticheta,
            Nivel.GRUPA,
            parinte=parinte,
            specializare=spec,
            an_studiu=t.an,
        )

    # Optionale / facultative / limbi / special / necunoscut -> pachet.
    return cache.grupa(
        t.slug,
        t.eticheta or t.raw,
        Nivel.OPTIONAL,
        parinte=parinte,
        specializare=spec,
        an_studiu=t.an,
    )


#: Paginile care se aplica altor formatiuni. SPECIAL (Fizica/Robotica) si NECUNOSCUT
#: (`Conferinte si Seminarii`) nu: n-au cui sa se aplice, iar caderea pe tot anul le-ar
#: arata unor studenti care n-au nicio legatura cu ele.
_PACHETE = (TipPagina.OPTIONAL_SERII, TipPagina.OPTIONAL, TipPagina.FACULTATIV, TipPagina.LIMBI)


def _grupe_tinta(cache: _Cache, t: TitluOrar) -> list[Grupa]:
    """Grupele carora li se aplica o pagina de optionale/facultative/limbi.

    Le legam prin ORA_GRUPA, nu prin PARINTE: un pachet de optionale nu e parintele
    nimanui, doar se *aplica* mai multor formatiuni.

    Ordinea, de la cel mai precis la cel mai larg:
      1. seriile numite (`INFO Seriile 33,34,35: ...`);
      2. specializarile numite in titlu, cautate permisiv (`Mate-lnfo`, `Mate Info`,
         `Mate Apl.`) printre cele care **exista in anul paginii** -- deci si o specializare
         aparuta anul acesta, fara sa fie trecuta in vreo lista;
      3. altfel, tot anul (licenta sau master, dupa titlu). Un pachet pe care nu-l putem
         lega precis e mai bine vazut de tot anul decat de nimeni.
    """
    if t.tip not in _PACHETE:
        return []

    serii = [g for s in t.serii_tinta if (g := cache.grupe.get(_slug(f"seria-{s}")))]
    if serii:
        return serii
    if not t.an:
        return []

    master = t.program == "master" or este_master(t.raw)
    anul = {
        g.specializare: g
        for g in cache.grupe.values()
        if g.tip == Nivel.SPECIALIZARE.value
        and g.an_studiu == t.an
        and g.specializare
        and g.slug.endswith("-master") == master
    }
    coduri = specializari_vizate(t.raw, set(anul))
    if not coduri and t.specializare in anul:
        coduri = [t.specializare]
    return [anul[c] for c in coduri] if coduri else list(anul.values())


# ---------------------------------------------------------------------------
# Orele
# ---------------------------------------------------------------------------

_RE_ORE = re.compile(r"^\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$")


def _interval(ore: str) -> tuple[time, time] | None:
    """ "14-17" -> (14:00, 17:00). Capat exclusiv, vezi docs §3."""
    m = _RE_ORE.match(ore or "")
    if not m:
        return None
    inceput, sfarsit = int(m.group(1)), int(m.group(2))
    if not (0 <= inceput < sfarsit <= 24):
        return None
    return time(hour=inceput), time(
        hour=sfarsit if sfarsit < 24 else 23, minute=0 if sfarsit < 24 else 59
    )


def _nod_activitate(cache: _Cache, nod_pagina: Grupa, semigrupa: str | None, t: TitluOrar) -> Grupa:
    """Nodul caruia ii apartine activitatea.

    Daca celula are eticheta `Gr_N` si pagina e o grupa reala, ora apartine semigrupei --
    asa `/grupa/244` le poate marca vizual, iar un student cu semigrupa setata le filtreaza.
    """
    if not semigrupa or not t.este_formatiune:
        return nod_pagina
    numar = semigrupa.removeprefix("Gr_")
    return cache.grupa(
        f"{nod_pagina.slug}-{numar}",
        f"{nod_pagina.nume}/{numar}",
        Nivel.SEMIGRUPA,
        parinte=nod_pagina,
        specializare=nod_pagina.specializare,
        an_studiu=nod_pagina.an_studiu,
    )


def incarca_pagini(
    s: Session,
    pagini: list[dict[str, Any]],
    *,
    an_universitar: str = "2025-2026",
    semestru: int = 2,
) -> Raport:
    """Incarca paginile in baza de date si intoarce un raport."""
    rap = Raport()
    cache = _Cache(s, an_universitar)

    perioada = s.scalar(
        select(Perioada).where(Perioada.semestru == semestru, Perioada.an_univ == an_universitar)
    )
    if perioada is None:
        perioada = Perioada(semestru=semestru, an_univ=an_universitar)
        s.add(perioada)
        s.flush()

    # Doua treceri: intai toate nodurile de ierarhie, ca `_grupe_tinta` sa gaseasca
    # seriile si specializarile deja create cand ajungem la paginile de optionale.
    titluri: list[tuple[dict[str, Any], TitluOrar, Grupa]] = []
    for pag in pagini:
        t = parse_titlu(pag.get("grupa", ""))
        if t.tip is TipPagina.NECUNOSCUT and not t.raw:
            rap.ignorate += 1
            continue
        if t.note:
            rap.avertismente.append(f"{pag.get('_source', '?')}: {t.raw!r} -- {'; '.join(t.note)}")
        titluri.append((pag, t, _lant_ierarhic(cache, t)))
    s.flush()

    # Legaturile ORA_GRUPA au nevoie de ID_ORA, deci le scriem abia dupa un singur flush
    # la final. Un flush pe fiecare ora ar insemna ~1100 de drumuri separate la baza.
    legaturi: list[tuple[Ora, list[Grupa]]] = []
    for pag, t, nod_pagina in titluri:
        rap.pagini += 1
        sursa = pag.get("_source")
        tinte = [] if t.este_formatiune else _grupe_tinta(cache, t)

        for zi in ZILE_JSON:
            for act in pag.get(zi, []):
                interval = _interval(act.get("ore", ""))
                if interval is None:
                    rap.ignorate += 1
                    rap.avertismente.append(
                        f"{sursa}: interval invalid {act.get('ore')!r} ({t.raw})"
                    )
                    continue

                semigrupa = normalizeaza_semigrupa(act.get("semigrupa", ""))
                nod = _nod_activitate(cache, nod_pagina, semigrupa, t)

                ora = Ora(
                    profesor=cache.profesor(act.get("profesor")),
                    materie=cache.materie(act.get("materie")),
                    sala=cache.sala(act.get("sala")),
                    grupa=nod,
                    perioada=perioada,
                    tip_ora_materie=(act.get("tip") or "").strip() or None,
                    ora_inceput=interval[0],
                    ora_sfarsit=interval[1],
                    zi_saptamana=zi,
                    frecventa=(act.get("frecventa") or "").strip() or None,
                    saptamani=(act.get("saptamani") or "").strip() or None,
                    semigrupa=semigrupa,
                    sursa_pagina=sursa,
                    # Fara `_confidence` inseamna date validate manual (JSON-ul de referinta).
                    confidence=float(act.get("_confidence", 1.0)),
                    sursa_bbox=act.get("_bbox"),
                    campuri_nesigure=act.get("_nesigure") or None,
                )
                s.add(ora)
                rap.ore += 1
                if tinte:
                    legaturi.append((ora, tinte))

    s.flush()
    s.add_all(
        OraGrupa(ora_id=ora.id, grupa_id=tinta.id) for ora, tinte in legaturi for tinta in tinte
    )
    rap.legaturi_partajate = sum(len(tinte) for _, tinte in legaturi)
    s.flush()
    rap.grupe = len(cache.grupe)
    rap.noduri = set(cache.atinse)
    rap.profesori = len(cache.profesori)
    rap.materii = len(cache.materii)
    rap.sali = len(cache.sali)
    return rap


def incarca_din_json(
    s: Session, cale: Path | str, *, an_universitar: str = "2025-2026", semestru: int = 2
) -> Raport:
    """Incarca dintr-un fisier in formatul `date.json`."""
    date = json.loads(Path(cale).read_text(encoding="utf-8"))
    if isinstance(date, dict):
        date = [date]
    return incarca_pagini(s, date, an_universitar=an_universitar, semestru=semestru)
