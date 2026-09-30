"""Corectiile manuale ale orarului: ce schimba un admin si cum supravietuieste schimbarea.

Un admin poate **adauga** o activitate sau poate **modifica** una existenta -- orice din ea:
materia, tipul, ziua si orele, sala, profesorul, pentru cine se tine. Fiecare schimbare se
aplica pe loc in ORA si se tine si ca rand in CORECTIE (vezi modelul pentru de ce). Dupa
fiecare ingest -- care sterge si reincarca orele semestrului -- `aplica_dupa_ingest` le pune
la loc; cele care nu-si mai gasesc activitatea raman marcate neaplicate.

Ce tine o corectie
------------------
Coloanele ei descriu activitatea **asa cum trebuie sa fie**. La o modificare, `ORIGINAL`
tine, ca JSON, activitatea **cum era in orarul publicat**: dupa ea o regasim intr-un orar nou
si la ea revenim la anulare. Totul e in cuvinte care supravietuiesc reingestului -- slug-ul
grupei, numele materiei, al salii -- nu in id-uri.

Cum se regaseste o activitate dupa reingest
-------------------------------------------
Dupa original: grupa, ziua, orele, materia, tipul, semigrupa, frecventa, saptamanile si sala,
care deosebeste doua laboratoare paralele altfel identice. Profesorul **nu** intra in
potrivire: numele lui se schimba intre citire (`Popescu S`) si verificarea incrucisata
(`Popescu Stefan`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from orar.db.models import Corectie, Grupa, Materie, Ora, OraGrupa, Perioada, Sala
from orar.domain.rooms import normalizeaza_sala

__all__ = [
    "RezultatAplicare",
    "Valori",
    "adauga",
    "anuleaza",
    "aplica_dupa_ingest",
    "confirma",
    "de_verificat",
    "e_confirmare",
    "descriere",
    "grupa_afisata",
    "modifica",
    "original",
]

ZILE = ("Luni", "Marti", "Miercuri", "Joi", "Vineri")


def descriere(o) -> str:  # noqa: ANN001 -- Ora sau OraArhivata
    """Activitatea in cuvinte: `AlgAvans (curs) · Luni 10-12 · 244 · Popescu Stefan · Amf.701`."""
    parti = [
        (o.materie.nume if o.materie else "fără materie")
        + (f" ({o.tip_ora_materie})" if o.tip_ora_materie else ""),
        f"{o.zi_saptamana} {o.ora_inceput.hour}-{o.ora_sfarsit.hour}",
        o.grupa.nume,
    ]
    if o.semigrupa:
        parti.append(o.semigrupa.replace("_", " "))
    if o.frecventa:
        parti.append(o.frecventa)
    if o.saptamani:
        parti.append(o.saptamani)
    if o.profesor:
        parti.append(o.profesor.nume)
    if o.sala:
        parti.append(o.sala.nume)
    return " · ".join(parti)


def _curat(text: str | None) -> str | None:
    text = " ".join((text or "").split())
    return text or None


@dataclass(frozen=True)
class Valori:
    """Tot ce se poate alege la o activitate. Validarea (zi, ore, tip) e a apelantului."""

    grupa: Grupa
    zi: str
    ora_inceput: int
    ora_sfarsit: int
    materie: str
    tip: str | None = None
    profesor: str | None = None
    sala: str | None = None
    semigrupa: str | None = None
    frecventa: str | None = None
    saptamani: str | None = None


def grupa_afisata(g: Grupa) -> Grupa:
    """Formatiunea pe care o alege un om: pentru nodul unei semigrupe (`244/1`), grupa ei.
    Semigrupa insasi e campul `semigrupa` al activitatii."""
    return g.parinte if g.tip == "semigrupa" and g.parinte is not None else g


def _proprietar(s: Session, grupa: Grupa, semigrupa: str | None) -> Grupa:
    """Nodul care detine activitatea. O activitate de semigrupa a unei grupe sta pe nodul
    semigrupei, daca exista -- la fel ca la incarcarea orarului; altfel o activitate
    deschisa si salvata fara nicio schimbare ar parea mutata de pe `244/1` pe `244`."""
    grupa = grupa_afisata(grupa)
    if semigrupa and grupa.tip == "grupa":
        copil = s.scalar(
            select(Grupa).where(
                Grupa.parinte_id == grupa.id,
                Grupa.tip == "semigrupa",
                Grupa.slug == f"{grupa.slug}-{semigrupa.removeprefix('Gr_')}",
            )
        )
        if copil is not None:
            return copil
    return grupa


def _stare(v: Valori, proprietar: Grupa) -> dict:
    """Valorile alese, in forma in care se tin si se compara."""
    return {
        "grupa_slug": proprietar.slug,
        "zi": v.zi,
        "ora_inceput": f"{v.ora_inceput:02d}:00",
        "ora_sfarsit": f"{v.ora_sfarsit:02d}:00",
        "materie": _curat(v.materie),
        "tip": _curat(v.tip),
        "semigrupa": _curat(v.semigrupa),
        "frecventa": _curat(v.frecventa),
        "saptamani": _curat(v.saptamani),
        "profesor": _curat(v.profesor),
        # fara sala = sala "nespecificata", exact cum o tine si incarcarea orarului
        "sala": normalizeaza_sala(_curat(v.sala)).nume,
    }


def _stare_ora(o: Ora) -> dict:
    """Activitatea din baza, in aceeasi forma."""
    return {
        "grupa_slug": o.grupa.slug,
        "zi": o.zi_saptamana,
        "ora_inceput": f"{o.ora_inceput:%H:%M}",
        "ora_sfarsit": f"{o.ora_sfarsit:%H:%M}",
        "materie": o.materie.nume if o.materie else None,
        "tip": o.tip_ora_materie,
        "semigrupa": o.semigrupa,
        "frecventa": o.frecventa,
        "saptamani": o.saptamani,
        "profesor": o.profesor.nume if o.profesor else None,
        "sala": o.sala.nume if o.sala else None,
    }


#: Campurile unei activitati, in forma de mai sus -- ce se compara si ce se tine in ORIGINAL.
_CAMPURI = (
    "grupa_slug",
    "zi",
    "ora_inceput",
    "ora_sfarsit",
    "materie",
    "tip",
    "semigrupa",
    "frecventa",
    "saptamani",
    "profesor",
    "sala",
)


def _ora(text: str) -> time:
    h, m = text.split(":")
    return time(hour=int(h), minute=int(m))


def original(c: Corectie) -> dict | None:
    """Activitatea cum era in orarul publicat, sau None pentru o activitate adaugata."""
    return json.loads(c.original) if c.original else None


def _entitati(s: Session, an_universitar: str):  # noqa: ANN202
    """Profesorii, salile si materiile existente, cu crearea celor noi -- aceleasi reguli de
    deduplicare (slug, normalizarea salii) ca la incarcarea orarului."""
    from orar.ingest.load import _Cache

    return _Cache(s, an_universitar)


def _fara_nesiguranta(o: Ora, *campuri: str) -> None:
    """Ce a confirmat un admin nu mai are ce cauta in coada de verificare a OCR-ului."""
    if not o.campuri_nesigure:
        return
    ramase = [c for c in o.campuri_nesigure.split(",") if c and c not in campuri]
    o.campuri_nesigure = ",".join(ramase) or None


def _grupa(s: Session, slug: str, an_universitar: str) -> Grupa | None:
    return s.scalar(select(Grupa).where(Grupa.slug == slug, Grupa.an_universitar == an_universitar))


def _pune(s: Session, o: Ora, stare: dict, entitati, *, legaturi: list[str] | None) -> bool:  # noqa: ANN001
    """Aduce activitatea din baza la `stare`. False daca formatiunea ei nu (mai) exista.

    `legaturi`: formatiunile cu care activitatea e partajata (ORA_GRUPA), de pus la loc;
    None le lasa cum sunt. Cand activitatea trece la alta formatiune, legaturile vechi nu
    mai au sens -- "pentru cine" inseamna de-acum exact formatiunea aleasa -- deci apelantul
    trimite lista goala.
    """
    an = o.grupa.an_universitar
    grupa = _grupa(s, stare["grupa_slug"], an)
    if grupa is None:
        return False
    o.grupa = grupa
    o.zi_saptamana = stare["zi"]
    o.ora_inceput, o.ora_sfarsit = _ora(stare["ora_inceput"]), _ora(stare["ora_sfarsit"])
    o.materie = entitati.materie(stare["materie"])
    o.tip_ora_materie = stare["tip"]
    o.semigrupa = stare["semigrupa"]
    o.frecventa = stare["frecventa"]
    o.saptamani = stare["saptamani"]
    o.profesor = entitati.profesor(stare["profesor"])
    o.sala = entitati.sala(stare["sala"])
    _fara_nesiguranta(o, "profesor", "materie")
    o.confidence = 1.0
    s.flush()

    if legaturi is not None:
        s.execute(delete(OraGrupa).where(OraGrupa.ora_id == o.id))
        for slug in legaturi:
            tinta = _grupa(s, slug, an)
            if tinta is not None and tinta.id != grupa.id:
                s.add(OraGrupa(ora_id=o.id, grupa_id=tinta.id))
        s.flush()
    return True


def _scrie(c: Corectie, stare: dict) -> None:
    c.grupa_slug = stare["grupa_slug"]
    c.zi = stare["zi"]
    c.ora_inceput, c.ora_sfarsit = _ora(stare["ora_inceput"]), _ora(stare["ora_sfarsit"])
    for camp in ("materie", "tip", "semigrupa", "frecventa", "saptamani", "profesor", "sala"):
        setattr(c, camp, stare[camp])


def _legaturi(s: Session, o: Ora) -> list[str]:
    return sorted(
        s.scalars(
            select(Grupa.slug)
            .join(OraGrupa, OraGrupa.grupa_id == Grupa.id)
            .where(OraGrupa.ora_id == o.id)
        )
    )


# ---------------------------------------------------------------------------
# Ce face adminul
# ---------------------------------------------------------------------------


def modifica(s: Session, o: Ora, v: Valori, *, de_catre: str) -> Corectie | None:
    """Aduce activitatea la valorile alese. Intoarce corectia ei, sau None daca activitatea
    e (din nou) exact cea din orarul publicat."""
    acum = _stare_ora(o)
    nou = _stare(v, _proprietar(s, v.grupa, v.semigrupa))
    c = s.get(Corectie, o.corectie_id) if o.corectie_id else None
    if nou == acum:
        return c

    an = o.grupa.an_universitar
    if c is None:
        c = Corectie(
            creat_la=datetime.now(),
            creat_de=de_catre,
            fel="modificare",
            an_univ=an,
            semestru=o.perioada.semestru,
            original=json.dumps({**acum, "legaturi": _legaturi(s, o)}, ensure_ascii=False),
            grupa_slug=acum["grupa_slug"],
            zi=acum["zi"],
            ora_inceput=o.ora_inceput,
            ora_sfarsit=o.ora_sfarsit,
        )
        s.add(c)
    _scrie(c, nou)
    c.aplicata = True

    publicat = original(c)
    # Trecuta la alta formatiune: nu mai e partajata cu cele de dinainte. Revenita la a ei:
    # isi recapata legaturile din orarul publicat.
    if nou["grupa_slug"] == acum["grupa_slug"]:
        legaturi = None
    elif publicat and nou["grupa_slug"] == publicat["grupa_slug"]:
        legaturi = publicat["legaturi"]
    else:
        legaturi = []
    _pune(s, o, nou, _entitati(s, an), legaturi=legaturi)

    # Adusa inapoi exact la ce era in orarul publicat: nu mai e nicio corectie de tinut.
    if publicat and all(nou[k] == publicat[k] for k in _CAMPURI):
        o.corectie_id = None
        s.flush()
        s.delete(c)
        s.flush()
        return None
    s.flush()
    o.corectie_id = c.id
    s.flush()
    return c


def de_verificat(o: Ora) -> bool:
    """Activitatea mai are ceva de confirmat de un om: un camp pe care vocabularul nu l-a
    recunoscut, sau o citire cu incredere mica."""
    return bool(o.campuri_nesigure) or (o.confidence is not None and o.confidence < 1.0)


def confirma(s: Session, o: Ora, *, de_catre: str) -> None:
    """Un admin a verificat activitatea si e buna asa cum e: iese din coada de verificare.

    Confirmarea se tine ca o corectie fara nicio schimbare (originalul = valorile de acum),
    ca sa se puna la loc dupa fiecare orar nou: altfel aceeasi citire nesigura s-ar intoarce
    in coada la fiecare reincarcare.
    """
    if o.corectie_id is None:
        acum = _stare_ora(o)
        c = Corectie(
            creat_la=datetime.now(),
            creat_de=de_catre,
            fel="modificare",
            an_univ=o.grupa.an_universitar,
            semestru=o.perioada.semestru,
            original=json.dumps({**acum, "legaturi": _legaturi(s, o)}, ensure_ascii=False),
            grupa_slug=acum["grupa_slug"],
            zi=acum["zi"],
            ora_inceput=o.ora_inceput,
            ora_sfarsit=o.ora_sfarsit,
        )
        _scrie(c, acum)
        c.aplicata = True
        s.add(c)
        s.flush()
        o.corectie_id = c.id
    o.campuri_nesigure = None
    o.confidence = 1.0
    s.flush()


def e_confirmare(c: Corectie) -> bool:
    """Corectia nu schimba nimic: doar confirma o citire nesigura (vezi `confirma`)."""
    publicat = original(c)
    if c.fel != "modificare" or publicat is None:
        return False
    acum = {camp: getattr(c, camp) for camp in _CAMPURI if not camp.startswith("ora_")}
    acum["ora_inceput"] = f"{c.ora_inceput:%H:%M}"
    acum["ora_sfarsit"] = f"{c.ora_sfarsit:%H:%M}"
    return all(acum[k] == publicat[k] for k in _CAMPURI)


def adauga(s: Session, v: Valori, *, semestru: int, de_catre: str) -> Ora:
    """Adauga o activitate care nu e in orarul publicat."""
    proprietar = _proprietar(s, v.grupa, v.semigrupa)
    c = Corectie(
        creat_la=datetime.now(),
        creat_de=de_catre,
        fel="adaugare",
        an_univ=proprietar.an_universitar,
        semestru=semestru,
        grupa_slug=proprietar.slug,
        zi=v.zi,
        ora_inceput=time(hour=v.ora_inceput),
        ora_sfarsit=time(hour=v.ora_sfarsit),
    )
    _scrie(c, _stare(v, proprietar))
    s.add(c)
    s.flush()
    return _creeaza(s, c, proprietar, _entitati(s, proprietar.an_universitar))


def _creeaza(s: Session, c: Corectie, grupa: Grupa, entitati) -> Ora:  # noqa: ANN001
    perioada = s.scalar(
        select(Perioada).where(Perioada.semestru == c.semestru, Perioada.an_univ == c.an_univ)
    )
    if perioada is None:
        perioada = Perioada(semestru=c.semestru, an_univ=c.an_univ)
        s.add(perioada)
    o = Ora(
        profesor=entitati.profesor(c.profesor),
        materie=entitati.materie(c.materie),
        sala=entitati.sala(c.sala),
        grupa=grupa,
        perioada=perioada,
        tip_ora_materie=c.tip,
        ora_inceput=c.ora_inceput,
        ora_sfarsit=c.ora_sfarsit,
        zi_saptamana=c.zi,
        frecventa=c.frecventa,
        saptamani=c.saptamani,
        semigrupa=c.semigrupa,
        confidence=1.0,  # introdusa de un om, nu citita
        corectie_id=c.id,
    )
    s.add(o)
    s.flush()
    return o


def anuleaza(s: Session, c: Corectie) -> None:
    """Renunta la o corectie: activitatea adaugata dispare, cea modificata revine la ce era."""
    ore = list(s.scalars(select(Ora).where(Ora.corectie_id == c.id)))
    publicat = original(c)
    if c.fel == "adaugare" or publicat is None:
        if ore:
            ids = [o.id for o in ore]
            s.execute(delete(OraGrupa).where(OraGrupa.ora_id.in_(ids)))
            for o in ore:
                s.delete(o)
    else:
        entitati = _entitati(s, c.an_univ)
        for o in ore:
            _pune(s, o, publicat, entitati, legaturi=publicat.get("legaturi", []))
            o.corectie_id = None
    s.flush()
    s.delete(c)
    s.flush()


# ---------------------------------------------------------------------------
# Dupa ingest
# ---------------------------------------------------------------------------


@dataclass
class RezultatAplicare:
    aplicate: int = 0
    #: Corectiile care nu si-au mai gasit activitatea sau grupa, in cuvinte.
    neaplicate: list[str] = field(default_factory=list)


def _tinte(s: Session, c: Corectie, publicat: dict) -> list[Ora]:
    """Activitatile din orarul proaspat incarcat carora li se aplica o `modificare`."""

    def egal(coloana, valoare):  # noqa: ANN001, ANN202
        return coloana.is_(None) if valoare is None else coloana == valoare

    stmt = (
        select(Ora)
        .join(Grupa, Grupa.id == Ora.grupa_id)
        .join(Perioada, Perioada.id == Ora.perioada_id)
        .where(
            Grupa.slug == publicat["grupa_slug"],
            Grupa.an_universitar == c.an_univ,
            Perioada.semestru == c.semestru,
            Ora.zi_saptamana == publicat["zi"],
            Ora.ora_inceput == _ora(publicat["ora_inceput"]),
            Ora.ora_sfarsit == _ora(publicat["ora_sfarsit"]),
            egal(Ora.tip_ora_materie, publicat["tip"]),
            egal(Ora.semigrupa, publicat["semigrupa"]),
            egal(Ora.frecventa, publicat["frecventa"]),
            egal(Ora.saptamani, publicat["saptamani"]),
            Ora.corectie_id.is_(None),
        )
    )
    if publicat["materie"] is None:
        stmt = stmt.where(Ora.materie_id.is_(None))
    else:
        stmt = stmt.join(Materie, Materie.id == Ora.materie_id).where(
            Materie.nume == publicat["materie"]
        )
    if publicat["sala"] is not None:
        stmt = stmt.join(Sala, Sala.id == Ora.sala_id).where(Sala.nume == publicat["sala"])
    return list(s.scalars(stmt))


def aplica_dupa_ingest(s: Session, *, an_universitar: str, semestru: int) -> RezultatAplicare:
    """Pune la loc corectiile manuale peste orele tocmai reincarcate ale semestrului."""
    rez = RezultatAplicare()
    corectii = list(
        s.scalars(
            select(Corectie)
            .where(Corectie.an_univ == an_universitar, Corectie.semestru == semestru)
            .order_by(Corectie.id)
        )
    )
    if not corectii:
        return rez

    entitati = _entitati(s, an_universitar)
    for c in corectii:
        publicat = original(c)
        stare = {
            camp: getattr(c, camp)
            for camp in _CAMPURI
            if camp not in ("ora_inceput", "ora_sfarsit")
        }
        stare["ora_inceput"] = f"{c.ora_inceput:%H:%M}"
        stare["ora_sfarsit"] = f"{c.ora_sfarsit:%H:%M}"
        if c.fel == "adaugare" or publicat is None:
            grupa = _grupa(s, c.grupa_slug, an_universitar)
            if grupa is not None:
                _creeaza(s, c, grupa, entitati)
            c.aplicata = grupa is not None
        else:
            mutata = stare["grupa_slug"] != publicat["grupa_slug"]
            aplicate = 0
            for o in _tinte(s, c, publicat):
                if _pune(s, o, stare, entitati, legaturi=[] if mutata else None):
                    o.corectie_id = c.id
                    aplicate += 1
            c.aplicata = aplicate > 0
        if c.aplicata:
            rez.aplicate += 1
        else:
            de_cautat = publicat or stare
            rez.neaplicate.append(
                f"{c.fel}: {de_cautat['materie'] or '?'} ({de_cautat['tip'] or '-'}), "
                f"{de_cautat['zi']} {de_cautat['ora_inceput']}-{de_cautat['ora_sfarsit']}, "
                f"{de_cautat['grupa_slug']}"
            )
    s.flush()
    return rez
