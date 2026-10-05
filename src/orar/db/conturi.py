"""Orarul unui cont: ce orar urmeaza, din ce grupe isi poate alege, si ce se intampla cu
grupa lui cand se schimba orarul sau anul universitar.

Trei reguli:

* **Orarul contului.** Implicit, "Orarul meu" urmeaza orarul implicit al sitului si se
  actualizeaza singur cand adminul il schimba. Cine vrea, ramane pe un orar anume
  (`USER.ORAR_AUTOMAT` = False, `USER.ORAR_ALES`).
* **Aceeasi grupa, in alt orar.** Grupele sunt noduri ale unui an universitar. Cand orarul
  urmat e din alt an decat grupa contului, grupa se muta pe cea cu acelasi nume din anul
  acela -- 1 la 1, cu semigrupa si optionalele alese pastrate.
* **Anul nou.** Cand structura anului universitar curent e completa, conturile care urmeaza
  orarul implicit isi pierd grupa, o singura data pe an: studentii au trecut in alt an si
  trebuie sa-si aleaga din nou grupa. Cine a ramas pe un orar anume nu e atins.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from orar.db.models import Grupa, Perioada, Setare, User
from orar.db.orare import orare_vii, perioada_implicita
from orar.db.structura import perioade_anului
from orar.domain.weeks import an_universitar_al, ce_lipseste

__all__ = [
    "grupa_in_orar",
    "grupe_de_ales",
    "orarul_contului",
    "reseteaza_grupele_daca_e_cazul",
]

#: Cheia din SETARE: anul universitar pentru care s-au resetat deja grupele.
CHEIE_RESETARE = "grupe_resetate_pentru"


def orarul_contului(s: Session, user: User | None) -> Perioada | None:
    """Orarul pe care il urmeaza contul: cel ales, daca a ramas pe unul care inca exista,
    altfel cel implicit al sitului."""
    if (
        user is not None
        and not user.orar_automat
        and user.orar_ales is not None
        and any(o.perioada.id == user.orar_ales.id for o in orare_vii(s))
    ):
        return user.orar_ales
    return perioada_implicita(s)


def grupe_de_ales(s: Session, perioada: Perioada | None) -> list[Grupa]:
    """Grupele din care se poate alege, in orarul dat: grupe reale (nu serii, semigrupe sau
    pachete) ale anului universitar al orarului."""
    stmt = select(Grupa).where(Grupa.tip == "grupa").order_by(Grupa.nume)
    if perioada is not None:
        stmt = stmt.where(Grupa.an_universitar == perioada.an_univ)
    return list(s.scalars(stmt))


def grupa_in_orar(s: Session, grupa: Grupa, perioada: Perioada | None) -> Grupa | None:
    """Aceeasi grupa, in anul universitar al orarului: dupa slug (`244`), apoi dupa nume.
    None cand anul acela nu are o grupa cu acelasi nume."""
    if perioada is None or grupa.an_universitar == perioada.an_univ:
        return grupa
    for coloana, valoare in ((Grupa.slug, grupa.slug), (Grupa.nume, grupa.nume)):
        gasita = s.scalar(
            select(Grupa).where(
                Grupa.tip == "grupa", Grupa.an_universitar == perioada.an_univ, coloana == valoare
            )
        )
        if gasita is not None:
            return gasita
    return None


def reseteaza_grupele_daca_e_cazul(s: Session, azi: date | None = None) -> int | None:
    """La un an universitar nou, odata ce structura lui e completa, scoate grupa conturilor
    care urmeaza orarul implicit -- o singura data pe an. Intoarce cate conturi au fost
    resetate, sau None daca nu era cazul."""
    an = an_universitar_al(azi or date.today())
    marcaj = s.get(Setare, CHEIE_RESETARE)
    if marcaj is not None and marcaj.valoare == an:
        return None
    if ce_lipseste([p.ca_perioada() for p in perioade_anului(s, an)]):
        return None
    rezultat = s.execute(
        update(User)
        .where(User.orar_automat.is_(True), User.grupa_id.is_not(None))
        .values(grupa_id=None, semigrupa=None, ascunse=None)
    )
    if marcaj is None:
        s.add(Setare(cheie=CHEIE_RESETARE, valoare=an))
    else:
        marcaj.valoare = an
    s.commit()
    # obiectele User deja incarcate in sesiune trebuie sa vada resetarea
    s.expire_all()
    return rezultat.rowcount
