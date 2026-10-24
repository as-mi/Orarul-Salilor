"""Podul dintre campaniile de tutoriate si restul aplicatiei.

Contine ce nu tine de modele (care stau in `db.models`) si nici de algoritm (care sta in
`domain.tutoriate_scheduler`): parsarea coloanelor JSON, construirea intrarii schedulerului
din "Orarul in cifre", fezabilitatea tutorilor si materializarea unei sesiuni confirmate
intr-un `Tutoriat` + `TutoriatTutor` (tabelele existente).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from orar.db.models import (
    AplicatieTutor,
    Perioada,
    PosturiDeschise,
    SesiuneTutoriat,
    Tutoriat,
    TutoriatTutor,
    User,
)
from orar.domain.grid import ORA_MAX, ORA_MIN, ZILE

__all__ = [
    "OcuparePublic",
    "disponibilitate_tutor",
    "incarca_json",
    "materializeaza_sesiune",
    "ocupare_public",
    "scrie_json",
    "tutori_liberi_slot",
]


def incarca_json(text: str | None, implicit: object = None) -> object:
    """Parsea un JSON din coloanele `Text`; intoarce `implicit` daca e gol sau stricat."""
    if not text:
        return implicit
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return implicit


def scrie_json(valoare: object) -> str:
    """Serializeaza un obiect in JSON, cu chei ordonate (stabil pentru dif-uri)."""
    return json.dumps(valoare, ensure_ascii=False, sort_keys=True)


@dataclass
class OcuparePublic:
    """Intrarea schedulerului, extrasa din agregatul "Orarul in cifre" al unui post."""

    ocupare: dict[tuple[str, int], int]  # (zi, ora) -> persoane ocupate din public
    dominante: dict[tuple[str, int], str | None]  # (zi, ora) -> cursul dominant (pt. explicatii)
    populatie: int


def ocupare_public(
    s: Session, perioada: Perioada, post: PosturiDeschise
) -> OcuparePublic:
    """Construieste intrarea schedulerului pentru publicul-ținta al unui post.

    `ocupare[(zi, ora)]` = varful de oameni ocupati dintre saptamanile semestrului, exact ca
    in `db.tutoriate.sugereaza_intervale`. `dominante` numeste cursul cel mai numeros din
    fiecare ceas, pentru explicatia "lipit de cursul X".
    """
    from orar.db.statistici import Filtre, calculeaza

    st = calculeaza(
        s,
        perioada,
        Filtre(specializari=post.coduri, ani=post.ani_alesi, serii=post.serii_alese),
    )
    ocupare: dict[tuple[str, int], int] = {}
    dominante: dict[tuple[str, int], str | None] = {}
    for (zi, ora), celula in st.celule.items():
        ocupare[(zi, ora)] = celula.persoane
        dominante[(zi, ora)] = _curs_dominant(celula.detalii)
    return OcuparePublic(ocupare, dominante, st.populatie)


def _curs_dominant(detalii: list) -> str | None:
    """Numele materiei cu cel mai mult public din ceas (dintre cele din publicul-ținta)."""
    if not detalii:
        return None
    cel_mai_mare = max(detalii, key=lambda a: a.din_tinta)
    ora = cel_mai_mare.ora
    return ora.materie.nume if ora.materie else None


def disponibilitate_tutor(aplicatie: AplicatieTutor) -> dict[str, set[int]]:
    """Disponibilitatea din grila de orar a unei aplicatii, ca {zi: ore libere}."""
    data = incarca_json(aplicatie.disponibilitate, {})
    if not isinstance(data, dict):
        return {}
    rezultat: dict[str, set[int]] = {}
    for zi, ore in data.items():
        if zi in ZILE and isinstance(ore, list):
            rezultat[zi] = {int(h) for h in ore if isinstance(h, int)}
    return rezultat


def tutori_liberi_slot(
    s: Session,
    *,
    user_ids: list[int],
    zi: str,
    inceput: time,
    sfarsit: time,
    perioada: Perioada | None,
    disponibilitati: dict[int, dict[str, set[int]]],
) -> bool:
    """Toti tutorii sunt liberi in slot: marcati disponibili in grila SI fara ore proprii."""
    from orar.db.queries import ore_pentru_grupa

    ore = list(range(inceput.hour, sfarsit.hour))
    for user in s.scalars(select(User).where(User.id.in_(user_ids or [-1]))):
        disp = disponibilitati.get(user.id, {})
        if zi not in disp or any(h not in disp[zi] for h in ore):
            return False
        if user.grupa_id is not None and perioada is not None:
            for o in ore_pentru_grupa(s, user.grupa_id, perioada_id=perioada.id, include_optionale=False):
                if o.zi_saptamana == zi and o.ora_inceput < sfarsit and inceput < o.ora_sfarsit:
                    return False
    return True


def materializeaza_sesiune(
    s: Session, sesiune: SesiuneTutoriat, *, de_catre: str
) -> Tutoriat:
    """Confirma o sesiune propusa: creeaza `Tutoriat` + `TutoriatTutor` si leaga sesiunea.

    Publicul si perioada vin din postul si campania sesiunii; sala si locul din propunere.
    Primul membru (dupa `ordine`) devine `principal`. Nu face `commit` -- decide apelantul.
    """
    post = sesiune.post
    campanie = sesiune.campanie
    echipa = sesiune.echipa

    tutoriat = Tutoriat(
        an_univ=campanie.an_univ,
        semestru=campanie.semestru,
        materie=post.materie,
        zi_saptamana=sesiune.zi_saptamana,
        ora_inceput=sesiune.ora_inceput,
        ora_sfarsit=sesiune.ora_sfarsit,
        sala_id=sesiune.sala_id,
        loc=sesiune.loc,
        data_inceput=campanie.data_inceput,
        data_sfarsit=campanie.data_sfarsit,
        specializari=post.specializari,
        ani=post.ani,
        serii=post.serii,
        descriere=post.descriere,
        stare="publicat",
        creat_de=de_catre,
        creat_la=datetime.now(),
    )
    s.add(tutoriat)
    s.flush()  # ca sa avem tutoriat.id

    for membru in sorted(echipa.membri, key=lambda m: m.ordine):
        s.add(
            TutoriatTutor(
                tutoriat_id=tutoriat.id,
                user_id=membru.user_id,
                principal=membru.principal,
            )
        )

    sesiune.tutoriat_id = tutoriat.id
    sesiune.stare = "confirmata"
    echipa.stare = "confirmata"
    return tutoriat
