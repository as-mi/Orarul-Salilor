"""Versiunile anterioare ale orarului: arhivarea la reingest si citirea lor pe grupe.

Cand facultatea publica un orar nou, ingestul sterge orele semestrului si le incarca pe
cele noi. Chiar inainte de stergere, `arhiveaza_semestrul` copiaza orele de pana atunci in
ORA_ARHIVA, sub o VERSIUNE_ORAR datata cu publicarea lor. Copierea e un singur
`INSERT ... SELECT` pe tabel, deci nu costa nimic fata de restul ingestului.

O versiune = o **publicare FMI**. Daca reluam aceeasi publicare (`sincronizeaza --forteaza`,
dupa o imbunatatire a extragerii), ce se inlocuieste nu e un orar mai vechi, ci o citire mai
veche a aceluiasi orar -- iar ca "versiune" ar arata diferente pe care facultatea nu le-a
facut niciodata. Atunci nu arhivam.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, inspect, literal, select
from sqlalchemy.orm import Session

from orar.db.models import (
    Grupa,
    Ora,
    OraArhivata,
    OraGrupa,
    OraGrupaArhivata,
    Perioada,
    Sala,
    VersiuneOrar,
)

__all__ = [
    "COLOANE_COPIATE",
    "VersiuneAfisata",
    "arhiveaza_semestrul",
    "versiuni_generale",
    "versiuni_pentru_grupa",
    "versiuni_pentru_sala",
]

#: Coloanele din ORA copiate in ORA_ARHIVA -- toate, in afara de cheia primara si de
#: legatura spre corectia manuala (arhiva tine rezultatul, nu cum s-a ajuns la el). Un test
#: verifica lista fata de model, ca o coloana noua pe ORA sa nu dispara tacut din arhiva.
COLOANE_COPIATE = (
    "profesor_id",
    "materie_id",
    "sala_id",
    "grupa_id",
    "perioada_id",
    "tip_ora_materie",
    "ora_inceput",
    "ora_sfarsit",
    "zi_saptamana",
    "frecventa",
    "saptamani",
    "semigrupa",
    "sursa_pagina",
    "confidence",
    "sursa_bbox",
    "campuri_nesigure",
)


def arhiveaza_semestrul(
    s: Session,
    *,
    an_universitar: str,
    semestru: int,
    publicat: datetime | None,
    inlocuit_de: datetime | None,
) -> VersiuneOrar | None:
    """Copiaza orele actuale ale semestrului intr-o versiune noua, inainte de a fi sterse.

    `publicat` e data publicarii orelor din baza (`SURSA_ORAR.INGESTAT_LA`), `inlocuit_de`
    cea a orarului care urmeaza sa le ia locul. Intoarce None cand nu e nimic de arhivat:
    semestrul e gol, e aceeasi publicare, sau versiunea exista deja.
    """
    if publicat is not None and publicat == inlocuit_de:
        return None
    if publicat is not None and s.scalar(
        select(VersiuneOrar.id).where(
            VersiuneOrar.an_univ == an_universitar,
            VersiuneOrar.semestru == semestru,
            VersiuneOrar.publicat == publicat,
        )
    ):
        return None

    perioade = select(Perioada.id).where(
        Perioada.an_univ == an_universitar, Perioada.semestru == semestru
    )
    nr_ore = s.scalar(select(func.count(Ora.id)).where(Ora.perioada_id.in_(perioade))) or 0
    if not nr_ore:
        return None

    versiune = VersiuneOrar(
        an_univ=an_universitar,
        semestru=semestru,
        publicat=publicat,
        arhivat_la=datetime.now(),
        nr_ore=nr_ore,
    )
    s.add(versiune)
    s.flush()

    arhiva = inspect(OraArhivata).columns
    actuale = inspect(Ora).columns
    s.execute(
        OraArhivata.__table__.insert().from_select(
            [arhiva["versiune_id"], arhiva["ora_originala"], *(arhiva[c] for c in COLOANE_COPIATE)],
            select(
                literal(versiune.id),
                actuale["id"],
                *(actuale[c] for c in COLOANE_COPIATE),
            ).where(actuale["perioada_id"].in_(perioade)),
        )
    )
    # Legaturile se refac prin ID_ORA_ORIGINALA: arhiva a primit chei noi.
    s.execute(
        OraGrupaArhivata.__table__.insert().from_select(
            [
                inspect(OraGrupaArhivata).columns["ora_id"],
                inspect(OraGrupaArhivata).columns["grupa_id"],
            ],
            select(OraArhivata.id, OraGrupa.grupa_id)
            .join(OraArhivata, OraArhivata.ora_originala == OraGrupa.ora_id)
            .where(OraArhivata.versiune_id == versiune.id),
        )
    )
    s.flush()
    return versiune


# ---------------------------------------------------------------------------
# Afisarea pe grupa
# ---------------------------------------------------------------------------


@dataclass
class VersiuneAfisata:
    versiune: VersiuneOrar
    #: Activitati care erau in versiunea asta si nu mai sunt in orarul actual al grupei.
    scoase: int
    #: Activitati din orarul actual al grupei care nu erau in versiunea asta.
    adaugate: int
    #: False cand versiunea e a altui orar (alt semestru sau alt an) decat cel afisat.
    comparabila: bool = True

    @property
    def identica(self) -> bool:
        return self.comparabila and not (self.scoase or self.adaugate)


#: Ce vede cineva dintr-o activitate. Fara proprietar (ID_GRUPA): daca o ora de serie a
#: ajuns, la alta consolidare, legata altfel in ierarhie, pentru cine o urmareste nu s-a
#: schimbat nimic.
_COLOANE_SEMNATURA = (
    "zi_saptamana",
    "ora_inceput",
    "ora_sfarsit",
    "materie_id",
    "profesor_id",
    "sala_id",
    "tip_ora_materie",
    "frecventa",
    "saptamani",
    "semigrupa",
)


def _semnatura(o: Ora | OraArhivata) -> tuple:
    return tuple(getattr(o, c) for c in _COLOANE_SEMNATURA)


def _versiuni(s: Session) -> list[VersiuneOrar]:
    """Versiunile anterioare, cele mai noi primele."""
    return list(
        s.scalars(
            select(VersiuneOrar).order_by(
                VersiuneOrar.publicat.desc().nullslast(), VersiuneOrar.arhivat_la.desc()
            )
        )
    )


def _compara(
    versiuni: list[VersiuneOrar],
    actual: Callable[[], Counter],
    vechi: Callable[[VersiuneOrar], Counter],
    perioada: Perioada | None,
) -> list[VersiuneAfisata]:
    """Fiecare versiune, cu ce s-a schimbat fata de orarul viu afisat (`perioada`).

    Doar versiunile **aceluiasi** semestru se compara: fata de alt semestru "s-a schimbat
    totul", ceea ce nu spune nimic. `actual` se calculeaza doar daca e ceva de comparat.
    """
    acum: Counter | None = None
    rezultat = []
    for v in versiuni:
        aceeasi = perioada is not None and (v.an_univ, v.semestru) == (
            perioada.an_univ,
            perioada.semestru,
        )
        if not aceeasi:
            rezultat.append(VersiuneAfisata(v, 0, 0, comparabila=False))
            continue
        if acum is None:
            acum = actual()
        atunci = vechi(v)
        rezultat.append(
            VersiuneAfisata(v, scoase=(atunci - acum).total(), adaugate=(acum - atunci).total())
        )
    return rezultat


def _id(perioada: Perioada | None) -> int | None:
    return perioada.id if perioada is not None else None


def versiuni_pentru_grupa(
    s: Session, grupa: Grupa, *, perioada: Perioada | None
) -> list[VersiuneAfisata]:
    """Versiunile anterioare, cu diferentele fata de orarul grupei din `perioada`.
    Diferentele se numara pe orarul complet, fara alegerile din cookie sau din cont."""
    from orar.db.queries import ore_pentru_grupa

    return _compara(
        _versiuni(s),
        lambda: Counter(
            _semnatura(o) for o in ore_pentru_grupa(s, grupa.id, perioada_id=_id(perioada))
        ),
        lambda v: Counter(_semnatura(o) for o in ore_pentru_grupa(s, grupa.id, versiune_id=v.id)),
        perioada,
    )


def versiuni_pentru_sala(
    s: Session, sala: Sala, *, perioada: Perioada | None
) -> list[VersiuneAfisata]:
    """Versiunile anterioare, cu ce s-a schimbat in ocuparea salii."""
    from orar.db.queries import ore_pentru_sala

    return _compara(
        _versiuni(s),
        lambda: Counter(
            _semnatura(o) for o in ore_pentru_sala(s, sala.id, perioada_id=_id(perioada))
        ),
        lambda v: Counter(_semnatura(o) for o in ore_pentru_sala(s, sala.id, versiune_id=v.id)),
        perioada,
    )


def versiuni_generale(s: Session, *, perioada: Perioada | None) -> list[VersiuneAfisata]:
    """Versiunile anterioare, cu ce s-a schimbat in tot orarul -- pentru pagina principala
    si lista de sali. Doar coloanele semnaturii, fara obiecte ORM: sunt toate orele."""

    def toate(model: type[Ora] | type[OraArhivata], *conditii) -> Counter:  # noqa: ANN002
        coloane = [getattr(model, c) for c in _COLOANE_SEMNATURA]
        return Counter(tuple(r) for r in s.execute(select(*coloane).where(*conditii)))

    return _compara(
        _versiuni(s),
        lambda: toate(Ora, Ora.perioada_id == _id(perioada)),
        lambda v: toate(OraArhivata, OraArhivata.versiune_id == v.id),
        perioada,
    )
