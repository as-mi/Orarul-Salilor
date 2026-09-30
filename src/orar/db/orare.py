"""Orarele din baza: cate unul pe (an universitar, semestru), si care e cel implicit.

Baza poate tine mai multe orare "vii" deodata -- semestrul 1 si semestrul 2, sau doi ani --
fiindca incarcarea unui orar inlocuieste doar orele semestrului lui. Situl arata insa **unul
singur**: altfel orarul unei grupe ar fi suma a doua semestre. Care: cel implicit, ales de
un admin, sau cel pe care si-l alege vizitatorul din panoul de versiuni.

Fiecare orar viu poate avea si versiuni anterioare (VERSIUNE_ORAR): publicarile mai vechi
ale aceluiasi semestru, pastrate cand o publicare noua le-a inlocuit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from orar.db.models import Ora, Perioada, SursaOrar

__all__ = ["OrarViu", "fa_implicita", "orare_vii", "perioada_implicita"]


@dataclass
class OrarViu:
    perioada: Perioada
    nr_ore: int
    #: Data publicarii orarului incarcat (`SURSA_ORAR.INGESTAT_LA`), daca se stie.
    publicat: datetime | None
    #: Cel afisat cand vizitatorul nu alege altul.
    implicit: bool = False

    @property
    def eticheta(self) -> str:
        return f"Semestrul {self.perioada.semestru} · {self.perioada.an_univ}"


def orare_vii(s: Session) -> list[OrarViu]:
    """Orarele care au ore in baza: cel implicit primul, apoi de la cel mai recent."""
    publicate = {
        (an, sem): cand
        for an, sem, cand in s.execute(
            select(SursaOrar.an_univ, SursaOrar.semestru, SursaOrar.ingestat_la).where(
                SursaOrar.fel == "grupe"
            )
        )
    }
    orare = [
        OrarViu(p, n, publicate.get((p.an_univ, p.semestru)))
        for p, n in s.execute(
            select(Perioada, func.count(Ora.id))
            .join(Ora, Ora.perioada_id == Perioada.id)
            .group_by(Perioada.id)
        )
    ]
    if not orare:
        return []
    implicit = next((o for o in orare if o.perioada.implicita), None)
    if implicit is None:
        # Niciun admin n-a ales inca: cel incarcat cel mai de curand.
        implicit = max(orare, key=lambda o: (o.publicat or datetime.min, o.perioada.id))
    implicit.implicit = True
    # dupa cel implicit: anii recenti intai, semestrul 2 inaintea lui 1
    restul = sorted(
        (o for o in orare if not o.implicit),
        key=lambda o: (o.perioada.an_univ, o.perioada.semestru),
        reverse=True,
    )
    return [implicit, *restul]


def perioada_implicita(s: Session) -> Perioada | None:
    orare = orare_vii(s)
    return orare[0].perioada if orare else None


def fa_implicita(s: Session, perioada: Perioada) -> None:
    """Face din `perioada` orarul implicit -- singurul."""
    s.execute(update(Perioada).values(implicita=False))
    s.flush()
    perioada.implicita = True
    s.flush()
