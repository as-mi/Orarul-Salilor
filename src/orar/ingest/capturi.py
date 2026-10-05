"""Unde stau capturile unui orar: cate un director pentru fiecare sesiune de incarcare.

De ce pe sesiuni
----------------
Coada de verificare arata, langa fiecare activitate nesigura, decupajul din pagina din care
a fost citita (`ORA.SURSA_PAGINA` + `ORA.SURSA_BBOX`). Cat timp capturile stateau intr-un
singur director pe semestru (`sem1-grupe/`), o incarcare noua -- alt an, aceeasi semestru --
suprascria paginile, iar decupajele orarului vechi aratau pagini straine. Acum fiecare
incarcare are directorul ei:

    2026-2027-sem1-grupe-20261004-153012/pag_084.png

Numele spune si de unde vin decupajele (an, semestru, fel, momentul), ca sa poata fi
grupate in coada de verificare. Directoarele pe care nu le mai foloseste nicio activitate se
sterg dupa fiecare incarcare.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

__all__ = ["Sesiune", "curata_capturile", "muta_capturile_vechi", "nume_sesiune"]

_RE_SESIUNE = re.compile(
    r"^(?P<an>\d{4}-\d{4})-sem(?P<sem>[12])-(?P<fel>grupe|profesori)-(?P<moment>\d{8}-\d{6})$"
)
_RE_VECHI = re.compile(r"^sem(?P<sem>[12])-(?P<fel>grupe|profesori)$")


def nume_sesiune(an_universitar: str, semestru: int, fel: str, moment: datetime) -> str:
    return f"{an_universitar}-sem{semestru}-{fel}-{moment:%Y%m%d-%H%M%S}"


@dataclass(frozen=True)
class Sesiune:
    """O sesiune de incarcare, citita din numele directorului ei."""

    director: str
    an: str | None = None
    semestru: int | None = None
    fel: str | None = None
    moment: datetime | None = None

    @classmethod
    def din_sursa(cls, sursa_pagina: str) -> Sesiune:
        """Sesiunea din care vine o pagina (`2026-2027-sem1-grupe-.../pag_084.png`)."""
        director = sursa_pagina.split("/")[0] if "/" in sursa_pagina else ""
        if m := _RE_SESIUNE.match(director):
            return cls(
                director=director,
                an=m["an"],
                semestru=int(m["sem"]),
                fel=m["fel"],
                moment=datetime.strptime(m["moment"], "%Y%m%d-%H%M%S"),
            )
        if m := _RE_VECHI.match(director):
            return cls(director=director, semestru=int(m["sem"]), fel=m["fel"])
        return cls(director=director)

    @property
    def eticheta(self) -> str:
        parti = []
        if self.semestru:
            parti.append(f"Semestrul {self.semestru}")
        if self.an:
            parti.append(self.an)
        text = " · ".join(parti) or "Capturi fără sesiune"
        if self.moment:
            text += f", încărcat pe {self.moment:%d.%m.%Y %H:%M}"
        elif self.director:
            text += " (încărcare veche)"
        return text


def _ultima_sursa(s: Session, semestru: int, fel: str | None):  # noqa: ANN202
    """Orarul incarcat cel mai recent pentru un semestru (si un fel, daca e dat). Orarul
    profesorilor nu are mereu o sursa a lui: atunci anul se ia din orarul grupelor."""
    from orar.db.models import SursaOrar

    conditii = [SursaOrar.semestru == semestru]
    if fel is not None:
        conditii.append(SursaOrar.fel == fel)
    return s.scalar(
        select(SursaOrar)
        .where(*conditii)
        .order_by(SursaOrar.ingestat_la.desc().nullslast())
        .limit(1)
    )


def muta_capturile_vechi(s: Session, director: Path) -> list[str]:
    """Muta directoarele vechi (`sem1-grupe/`) in directoare de sesiune si actualizeaza
    activitatile care trimit la ele. Ruleaza o singura data; dupa aceea nu mai gaseste nimic.

    Un director vechi tine paginile ultimei incarcari a semestrului lui: ele se dau
    activitatilor orarului incarcat cel mai recent pentru semestrul si felul acela. Celelalte
    activitati care trimiteau acolo (alt an, suprascris intre timp) raman fara decupaj --
    imaginile lor nu mai exista.
    """
    from orar.db.models import Ora, Perioada

    if not director.is_dir():
        return []
    mutate = []
    for vechi in sorted(p for p in director.iterdir() if p.is_dir() and _RE_VECHI.match(p.name)):
        m = _RE_VECHI.match(vechi.name)
        semestru, fel = int(m["sem"]), m["fel"]

        sursa = _ultima_sursa(s, semestru, fel) or _ultima_sursa(s, semestru, None)
        an = sursa.an_univ if sursa else "0000-0000"
        moment = datetime.fromtimestamp(
            max((f.stat().st_mtime for f in vechi.iterdir()), default=0)
        )
        nou = director / nume_sesiune(an, semestru, fel, moment)
        vechi.rename(nou)
        if sursa is not None:
            perioada = s.scalar(
                select(Perioada.id).where(Perioada.an_univ == an, Perioada.semestru == semestru)
            )
            for ora_id, pagina in s.execute(
                select(Ora.id, Ora.sursa_pagina).where(
                    Ora.perioada_id == perioada, Ora.sursa_pagina.like(f"{vechi.name}/%")
                )
            ):
                s.execute(
                    update(Ora)
                    .where(Ora.id == ora_id)
                    .values(sursa_pagina=f"{nou.name}/{pagina.split('/', 1)[1]}")
                )
        mutate.append(f"{vechi.name} -> {nou.name}")
    if mutate:
        # Directoarele s-au mutat deja pe disc: baza trebuie sa le urmeze acum, nu la
        # sfarsitul unui ingest care poate fi dat inapoi.
        s.commit()
    return mutate


def curata_capturile(s: Session, director: Path) -> list[str]:
    """Sterge directoarele de sesiune pe care nu le mai foloseste nimic.

    Capturile orarului grupelor raman cat timp o activitate (si din versiunile anterioare)
    trimite la ele. Ale orarului profesorilor nu le foloseste nimic dupa incarcare; ramane
    doar cea mai recenta, pe semestru, ca sa poata fi recitita (`orar crosscheck`).
    """
    from orar.db.models import Ora, OraArhivata

    if not director.is_dir():
        return []
    folosite = {
        pagina.split("/")[0]
        for model in (Ora, OraArhivata)
        for (pagina,) in s.execute(
            select(model.sursa_pagina).where(model.sursa_pagina.is_not(None)).distinct()
        )
        if "/" in pagina
    }
    sesiuni = [Sesiune.din_sursa(f"{p.name}/x") for p in director.iterdir() if p.is_dir()]
    ultima_profesori: dict[int, str] = {}
    for x in sorted(
        (x for x in sesiuni if x.fel == "profesori" and x.moment), key=lambda x: x.moment
    ):
        ultima_profesori[x.semestru] = x.director
    sterse = []
    for x in sesiuni:
        if x.moment is None:  # doar sesiunile noi; un director necunoscut nu e al nostru
            continue
        if x.director in folosite or x.director in ultima_profesori.values():
            continue
        shutil.rmtree(director / x.director, ignore_errors=True)
        sterse.append(x.director)
    if sterse:
        log.info("capturi sterse: %s", ", ".join(sterse))
    return sterse
