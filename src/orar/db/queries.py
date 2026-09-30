"""Interogarile care dau continut rutelor /grupa/{id} si /sala/{id}.

Nucleul e regula de mostenire ierarhica: orarul unei grupe = orele proprii + cele ale
**stramosilor** ei (seria, anul de specializare) + cele ale **descendentilor**
(semigrupele) + optionalele legate prin ORA_GRUPA.

    INFO an 2          <- curs comun pe tot anul   => se vede la 244
    +-- Seria 24       <- curs de serie            => se vede la 244
        +-- 244        <- seminar propriu
            +-- 244/1  <- laborator de semigrupa   => se vede la 244, marcat "Gr_1"
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.orm import Session, joinedload

from orar.db.models import (
    Grupa,
    Ora,
    OraArhivata,
    OraGrupa,
    OraGrupaArhivata,
    Profesor,
    Sala,
)

__all__ = [
    "ids_stramosi",
    "ids_descendenti",
    "ids_relevante",
    "ore_pentru_grupa",
    "ore_pentru_sala",
    "ore_pentru_profesor",
    "gaseste_profesor",
    "profesori_de_ales",
    "OraAfisata",
    "gaseste_grupa",
    "gaseste_sala",
]


# CTE recursiv in sus: nodul + toti stramosii lui.
_SQL_STRAMOSI = text(
    """
    WITH RECURSIVE sus(id) AS (
        SELECT :gid
        UNION
        SELECT g."PARINTE" FROM "GRUPA" g JOIN sus ON g."ID_GRUPA" = sus.id
        WHERE g."PARINTE" IS NOT NULL
    )
    SELECT id FROM sus
    """
)

# CTE recursiv in jos: nodul + toti descendentii lui.
_SQL_DESCENDENTI = text(
    """
    WITH RECURSIVE jos(id) AS (
        SELECT :gid
        UNION
        SELECT g."ID_GRUPA" FROM "GRUPA" g JOIN jos ON g."PARINTE" = jos.id
    )
    SELECT id FROM jos
    """
)


def ids_stramosi(s: Session, grupa_id: int) -> set[int]:
    """Nodul + lantul de parinti pana la radacina."""
    return {r[0] for r in s.execute(_SQL_STRAMOSI, {"gid": grupa_id})}


def ids_descendenti(s: Session, grupa_id: int) -> set[int]:
    """Nodul + tot subarborele de sub el."""
    return {r[0] for r in s.execute(_SQL_DESCENDENTI, {"gid": grupa_id})}


def ids_relevante(s: Session, grupa_id: int) -> set[int]:
    """Toate nodurile ale caror ore trebuie sa apara in orarul acestei grupe."""
    return ids_stramosi(s, grupa_id) | ids_descendenti(s, grupa_id)


def _cu_relatii(stmt: Select, model: type[Ora] | type[OraArhivata] = Ora) -> Select:
    return stmt.options(
        joinedload(model.materie),
        joinedload(model.profesor),
        joinedload(model.sala),
        joinedload(model.grupa),
    )


def ore_pentru_grupa(
    s: Session,
    grupa_id: int,
    *,
    perioada_id: int | None = None,
    include_optionale: bool = True,
    versiune_id: int | None = None,
) -> list[Ora] | list[OraArhivata]:
    """Orarul complet al unei grupe, cu mostenire pe verticala.

    Cu `versiune_id`, orarul dintr-o publicare anterioara (VERSIUNE_ORAR), dupa aceeasi
    regula de mostenire: arhiva are aceleasi coloane, deci se schimba doar tabelele.

    Intoarce **toate** optionalele legate de grupa; ce alege studentul sa vada se aplica
    deasupra, din cookie (`web/afisare.py`).

    Atentie la ce inseamna fiecare coloana: in `ORA_GRUPA`, `ID_GRUPA` e **tinta** legaturii
    (seria careia i se ofera pachetul), iar pachetul propriu-zis e `ORA.ID_GRUPA`.
    """
    ids = ids_relevante(s, grupa_id)
    ora, legatura = (Ora, OraGrupa) if versiune_id is None else (OraArhivata, OraGrupaArhivata)

    conditii = [ora.grupa_id.in_(ids)]
    if include_optionale:
        legate = select(legatura.ora_id).where(legatura.grupa_id.in_(ids))
        conditii.append(ora.id.in_(legate))

    stmt = _cu_relatii(select(ora).where(or_(*conditii)), ora)
    if versiune_id is not None:
        stmt = stmt.where(OraArhivata.versiune_id == versiune_id)
    if perioada_id is not None:
        stmt = stmt.where(ora.perioada_id == perioada_id)

    return list(s.execute(stmt).unique().scalars())


def ore_pentru_sala(
    s: Session,
    sala_id: int,
    *,
    perioada_id: int | None = None,
    versiune_id: int | None = None,
) -> list[Ora] | list[OraArhivata]:
    """Tot ce ocupa o sala, indiferent de grupa. Cu `versiune_id`, intr-o publicare
    anterioara a orarului."""
    ora = Ora if versiune_id is None else OraArhivata
    stmt = _cu_relatii(select(ora).where(ora.sala_id == sala_id), ora)
    if versiune_id is not None:
        stmt = stmt.where(OraArhivata.versiune_id == versiune_id)
    if perioada_id is not None:
        stmt = stmt.where(ora.perioada_id == perioada_id)
    return list(s.execute(stmt).unique().scalars())


def ore_pentru_profesor(s: Session, nume: str, *, perioada_id: int | None = None) -> list[Ora]:
    """Activitatile unui profesor. Un rand PROFESOR poate tine mai multi oameni (`A / B`),
    asa cum sunt scrisi in celula: le luam si pe cele tinute impreuna cu altcineva."""
    randuri = [
        p.id
        for p in s.scalars(select(Profesor).where(Profesor.nume.contains(nume)))
        if nume in (parte.strip() for parte in p.nume.split("/"))
    ]
    if not randuri:
        return []
    stmt = _cu_relatii(select(Ora).where(Ora.profesor_id.in_(randuri)))
    if perioada_id is not None:
        stmt = stmt.where(Ora.perioada_id == perioada_id)
    return list(s.execute(stmt).unique().scalars())


def gaseste_profesor(s: Session, identificator: str) -> Profesor | None:
    """Rezolva /profesor/{identificator} dupa slug sau dupa nume."""
    ident = identificator.strip()
    return s.scalar(select(Profesor).where(Profesor.slug == ident)) or s.scalar(
        select(Profesor).where(Profesor.nume == ident)
    )


def profesori_de_ales(s: Session) -> list[str]:
    """Numele dintre care se alege un profesor: cei din orarul profesorilor -- oameni reali,
    cu numele intreg. Pana se citeste el prima data, cei din orarul grupelor, cu campurile
    de mai multi oameni (`A / B`) despartite."""
    nume = list(
        s.scalars(
            select(Profesor.nume).where(Profesor.din_orar).order_by(func.lower(Profesor.nume))
        )
    )
    if not nume:
        campuri = s.scalars(select(Profesor.nume))
        nume = sorted(
            {p.strip() for c in campuri for p in c.split("/") if p.strip()}, key=str.lower
        )
    return nume


def gaseste_grupa(
    s: Session, identificator: str, *, an_universitar: str | None = None
) -> Grupa | None:
    """Rezolva /grupa/{identificator} dupa id numeric, slug sau nume.

    Asa merg si /grupa/244 (slug) si /grupa/17 (id intern), fara ca utilizatorul sa
    trebuiasca sa stie care e care. Grupa `244` exista in fiecare an universitar incarcat:
    `an_universitar` o alege pe cea din orarul afisat; daca in anul acela nu exista, cade
    pe oricare (un link vechi nu trebuie sa dea 404 doar din cauza anului).
    """
    ident = identificator.strip()

    def cauta(*conditii) -> Grupa | None:  # noqa: ANN002
        stmt = select(Grupa).where(*conditii)
        if an_universitar is not None:
            in_an = s.scalar(stmt.where(Grupa.an_universitar == an_universitar).limit(1))
            if in_an is not None:
                return in_an
        return s.scalar(stmt.order_by(Grupa.an_universitar.desc()).limit(1))

    if ident.isdigit():
        # Slug-ul are prioritate: "244" e numarul grupei, mult mai probabil decat un id intern.
        if g := cauta(Grupa.slug == ident):
            return g
        if g := s.get(Grupa, int(ident)):
            return g
    return cauta(or_(Grupa.slug == ident, Grupa.nume == ident))


def gaseste_sala(s: Session, identificator: str) -> Sala | None:
    """Rezolva /sala/{identificator} dupa id, slug, nume canonic sau forma nenormalizata."""
    from orar.domain.rooms import normalizeaza_sala

    ident = identificator.strip()
    if g := s.scalar(select(Sala).where(or_(Sala.slug == ident, Sala.nume == ident))):
        return g

    # "/sala/701" -> "Amf.701"; "/sala/L-507" -> "L.507"
    norm = normalizeaza_sala(ident)
    if g := s.scalar(select(Sala).where(or_(Sala.slug == norm.slug, Sala.nume == norm.nume))):
        return g

    if ident.isdigit():
        if g := s.scalar(select(Sala).where(Sala.nume.like(f"%.{ident}"))):
            return g
        return s.get(Sala, int(ident))
    return None


@dataclass
class OraAfisata:
    """O ora pregatita pentru randare, cu contextul de care are nevoie sablonul."""

    ora: Ora
    #: True daca ora vine de la un stramos (serie/an), nu de la grupa ceruta.
    mostenita: bool
    #: Numele nodului de la care vine, ex. "Seria 24".
    provenienta: str
