"""Ce opționale, facultative și limbi străine se afișează pe pagina unei grupe.

Alegerea se face din dropdown-ul de pe /grupa/{id} și se ține **în cookie**, nu în cont:
merge și fără autentificare, iar fiecare pagină (grupă, serie, an) își are alegerea ei.

Se poate alege pe materie sau pe **activitate**: doar seminarul unui opțional, fără curs;
doar laboratorul de marți, nu și pe cel de joi.

Tot aici, într-un cookie separat (`orar_semigrupa`), se ține minte semigrupa aleasă pe
fiecare pagină, ca la întoarcere să fie din nou cea aleasă.

Ce se ține e lista a ce e **ascuns**, nu a ce e bifat. Diferența contează când facultatea
publică un orar nou: o materie sau o activitate apărută între timp e vizibilă din prima,
în loc să fie ascunsă tacit doar pentru că nu exista când ai făcut alegerea.

Cheile nu sunt id-uri din baza, care se pot schimba de la un ingest la altul:
  - o materie intreaga: slug-ul ei (`ia`, `sistoplinux`);
  - o activitate: `@` + amprenta a ce o identifica pentru student (materie, tip, zi, ore,
    frecventa, saptamani, semigrupa). Nu intra sala si profesorul: daca doar sala se muta,
    alegerea ramane valabila.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from fastapi import Request, Response

from orar.domain.hierarchy import CategoriePachet, categorie_pachet

__all__ = [
    "COOKIE",
    "COOKIE_SEMIGRUPA",
    "GrupOptiuni",
    "ascunse_din_cont",
    "pune_ascunse_in_cont",
    "salveaza_semigrupa",
    "semigrupa_valida",
    "semigrupe_salvate",
    "OptiuneActivitate",
    "OptiuneMaterie",
    "ascunse_pe_pagina",
    "cheie_activitate",
    "citeste",
    "e_ascunsa",
    "grupeaza_optiunile",
    "salveaza",
    "cheie_valida",
]

COOKIE = "orar_afisare"
#: Semigrupa aleasa, tot pe pagina: cand revii pe /grupa/244, vezi din nou doar Gr_1.
COOKIE_SEMIGRUPA = "orar_semigrupa"
#: Browserele refuza cookie-urile de peste 4096 de octeti. Ramanem sub, cu margine pentru
#: nume si atribute; la depasire renuntam la paginile alese cel mai demult.
MAX_OCTETI = 3600
#: Cat tine alegerea. 400 de zile e plafonul impus oricum de Chrome.
DURATA = 400 * 24 * 3600

_RE_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,179}$")
_RE_CHEIE_ACTIVITATE = re.compile(r"^@[0-9a-f]{10}$")
_RE_SEMIGRUPA = re.compile(r"^Gr_[1-9]$")
_ORDINE = (
    CategoriePachet.OPTIONAL,
    CategoriePachet.FACULTATIV,
    CategoriePachet.LIMBI,
    CategoriePachet.ALTELE,
)
_ZILE = ("Luni", "Marti", "Miercuri", "Joi", "Vineri", "Sambata", "Duminica")
#: Ordinea in care apar activitatile unei materii: intai cursul, apoi aplicatiile.
_TIPURI = ("curs", "curs+seminar", "seminar", "sem", "sem+lab", "lab", "proiect")


def slug_valid(text: str) -> bool:
    return bool(_RE_SLUG.match(text or ""))


def cheie_valida(text: str) -> bool:
    """Slug de pagina/materie, sau cheie de activitate (`@` + 10 hex)."""
    return slug_valid(text) or bool(_RE_CHEIE_ACTIVITATE.match(text or ""))


def _fara_diacritice(text: str) -> str:
    fara = unicodedata.normalize("NFKD", text)
    return "".join(c for c in fara if not unicodedata.combining(c)).strip().lower()


def cheie_activitate(o) -> str:  # noqa: ANN001 -- Ora sau OraArhivata
    """Amprenta stabila a unei activitati, asa cum o vede studentul."""
    parti = (
        o.materie.slug if o.materie else "",
        (o.tip_ora_materie or "").lower(),
        o.zi_saptamana,
        f"{o.ora_inceput:%H%M}-{o.ora_sfarsit:%H%M}",
        o.frecventa or "",
        o.saptamani or "",
        o.semigrupa or "",
    )
    return "@" + hashlib.sha1("|".join(parti).encode()).hexdigest()[:10]


def e_ascunsa(o, ascunse: set[str]) -> bool:  # noqa: ANN001
    """O activitate de pachet ascunsa: ea insasi, sau toata materia ei."""
    if not ascunse or o.grupa.tip != "optional" or o.materie is None:
        return False
    return o.materie.slug in ascunse or cheie_activitate(o) in ascunse


# ---------------------------------------------------------------------------
# Cookie
# ---------------------------------------------------------------------------


def _citeste_cookie(request: Request, nume: str) -> dict:
    """Obiectul JSON din cookie, sau {} daca lipseste ori e stricat."""
    brut = request.cookies.get(nume, "")
    if not brut:
        return {}
    try:
        date = json.loads(base64.urlsafe_b64decode(brut + "=" * (-len(brut) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return {}
    return date if isinstance(date, dict) else {}


def _codifica(date: dict) -> str:
    text = json.dumps(date, separators=(",", ":"))
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _scrie_cookie(response: Response, nume: str, date: dict) -> None:
    """Scrie {pagina: alegere}. Peste plafon renunta la paginile alese cel mai demult (cele
    de la inceputul dictionarului); gol -> sterge cookie-ul."""
    valoare = _codifica(date)
    while len(valoare) > MAX_OCTETI and date:
        date.pop(next(iter(date)))
        valoare = _codifica(date)

    if not date:
        response.delete_cookie(nume, path="/")
        return
    response.set_cookie(
        nume,
        valoare,
        max_age=DURATA,
        path="/",
        samesite="lax",
        httponly=True,
        secure=os.getenv("ORAR_HTTPS", "").strip().lower() in ("1", "true", "da", "yes"),
    )


# -- optionalele ascunse --------------------------------------------------------


def citeste(request: Request) -> dict[str, list[str]]:
    """{slug pagina: [ce e ascuns, ...]}. Un cookie stricat inseamna "nimic ascuns"."""
    return {
        pagina: [m for m in ascunse if isinstance(m, str) and cheie_valida(m)]
        for pagina, ascunse in _citeste_cookie(request, COOKIE).items()
        if isinstance(pagina, str) and slug_valid(pagina) and isinstance(ascunse, list)
    }


def ascunse_pe_pagina(request: Request, pagina: str) -> set[str]:
    return set(citeste(request).get(pagina, []))


def salveaza(request: Request, response: Response, pagina: str, ascunse: Iterable[str]) -> None:
    """Scrie alegerea paginii, pastrand-o pe a celorlalte."""
    date = citeste(request)
    date.pop(pagina, None)
    ascunse = sorted({m for m in ascunse if cheie_valida(m)})
    if ascunse:
        date[pagina] = ascunse  # ultima aleasa ajunge la coada, deci e ultima renuntata
    _scrie_cookie(response, COOKIE, date)


# -- aceleasi alegeri, tinute in cont -------------------------------------------
#
# Pe "Orarul meu" (pagina grupei contului) alegerile nu se tin in cookie, ci in USER:
# `SEMIGRUPA` si `ASCUNSE`, o lista JSON cu aceleasi chei ca in cookie.


def ascunse_din_cont(user) -> set[str]:  # noqa: ANN001 -- orar.db.models.User
    try:
        date = json.loads(user.ascunse or "[]")
    except ValueError:
        return set()
    return (
        {m for m in date if isinstance(m, str) and cheie_valida(m)}
        if isinstance(date, list)
        else set()
    )


def pune_ascunse_in_cont(user, ascunse: Iterable[str]) -> None:  # noqa: ANN001
    curate = sorted({m for m in ascunse if cheie_valida(m)})
    user.ascunse = json.dumps(curate, separators=(",", ":")) if curate else None


# -- semigrupa aleasa -----------------------------------------------------------


def semigrupe_salvate(request: Request) -> dict[str, str]:
    """{slug pagina: "Gr_1"}. Valorile care nu arata a semigrupa se ignora."""
    return {
        pagina: sg
        for pagina, sg in _citeste_cookie(request, COOKIE_SEMIGRUPA).items()
        if isinstance(pagina, str) and slug_valid(pagina) and semigrupa_valida(sg)
    }


def semigrupa_valida(text: object) -> bool:
    return isinstance(text, str) and bool(_RE_SEMIGRUPA.match(text))


def salveaza_semigrupa(
    request: Request, response: Response, pagina: str, semigrupa: str | None
) -> None:
    """Tine minte semigrupa paginii; None o uita (inapoi la "toate semigrupele")."""
    date = semigrupe_salvate(request)
    date.pop(pagina, None)
    if semigrupa and semigrupa_valida(semigrupa):
        date[pagina] = semigrupa
    _scrie_cookie(response, COOKIE_SEMIGRUPA, date)


# ---------------------------------------------------------------------------
# Continutul dropdown-ului
# ---------------------------------------------------------------------------


@dataclass
class OptiuneActivitate:
    cheie: str
    #: "curs", "seminar", "Lab"...
    tip: str
    #: "Luni 10–12"
    cand: str
    #: Ce le deosebeste pe doua activitati de acelasi tip: sala, semigrupa, saptamanile...
    detalii: list[str]
    vizibila: bool
    _ordine: tuple = field(default=(), repr=False, compare=False)


@dataclass
class OptiuneMaterie:
    slug: str
    nume: str
    #: Denumirea intreaga din planul de invatamant, cand e incarcat si difera de nume.
    denumire: str | None
    activitati: list[OptiuneActivitate] = field(default_factory=list)
    #: Pachetele din care vine, pentru tooltip ("Optionale an III - INFO (Curs)").
    pachete: list[str] = field(default_factory=list)

    @property
    def vizibile(self) -> int:
        return sum(a.vizibila for a in self.activitati)

    @property
    def vizibila(self) -> bool:
        """Macar o activitate vizibila."""
        return self.vizibile > 0


@dataclass
class GrupOptiuni:
    categorie: CategoriePachet
    materii: list[OptiuneMaterie]


def _ordine_tip(tip: str) -> int:
    t = tip.lower()
    return _TIPURI.index(t) if t in _TIPURI else len(_TIPURI)


def grupeaza_optiunile(ore: Sequence, ascunse: set[str]) -> list[GrupOptiuni]:  # noqa: ANN001
    """Materiile venite din pachete (grupa-proprietar de tip `optional`), pe categorii, cu
    activitatile fiecareia.

    O materie apare o singura data, in prima categorie in care cade -- acelasi curs poate
    fi opțional pentru unii si facultativ pentru altii, dar bifa e una singura. La fel, doua
    randuri cu aceeasi cheie de activitate (aceeasi ora, legata de doua pachete) sunt o
    singura bifa.
    """
    pe_materie: dict[str, tuple[CategoriePachet, OptiuneMaterie]] = {}
    vazute: set[str] = set()
    for o in ore:
        if o.grupa.tip != "optional" or o.materie is None:
            continue
        categorie = categorie_pachet(o.grupa.nume)
        slug = o.materie.slug
        if slug not in pe_materie:
            denumire = o.materie.denumire
            # "Programare competitiva" / "Programare competitivă": nu repetam acelasi nume.
            if denumire and _fara_diacritice(denumire) == _fara_diacritice(o.materie.nume):
                denumire = None
            pe_materie[slug] = (
                categorie,
                OptiuneMaterie(slug=slug, nume=o.materie.nume, denumire=denumire),
            )
        cat, opt = pe_materie[slug]
        if _ORDINE.index(categorie) < _ORDINE.index(cat):
            pe_materie[slug] = (categorie, opt)
        if o.grupa.nume not in opt.pachete:
            opt.pachete.append(o.grupa.nume)

        cheie = cheie_activitate(o)
        if cheie in vazute:
            continue
        vazute.add(cheie)
        detalii = [
            x
            for x in (
                o.semigrupa.replace("_", " ") if o.semigrupa else None,
                o.frecventa,
                o.saptamani,
                o.sala.nume if o.sala else None,
            )
            if x
        ]
        opt.activitati.append(
            OptiuneActivitate(
                cheie=cheie,
                tip=o.tip_ora_materie or "activitate",
                cand=f"{o.zi_saptamana} {o.ora_inceput.hour}-{o.ora_sfarsit.hour}",
                detalii=detalii,
                vizibila=slug not in ascunse and cheie not in ascunse,
                _ordine=(
                    _ordine_tip(o.tip_ora_materie or ""),
                    _ZILE.index(o.zi_saptamana) if o.zi_saptamana in _ZILE else 9,
                    o.ora_inceput,
                    o.semigrupa or "",
                ),
            )
        )

    grupuri = []
    for categorie in _ORDINE:
        materii = sorted(
            (opt for cat, opt in pe_materie.values() if cat is categorie),
            key=lambda m: m.nume.lower(),
        )
        for m in materii:
            m.activitati.sort(key=lambda a: a._ordine)
        if materii:
            grupuri.append(GrupOptiuni(categorie, materii))
    return grupuri
