"""Numarul de locuri al fiecarei sali, de pe pagina 2 a orarului.

Unde scrie
----------
PDF-ul orarului are 100 de pagini, din care primele doua nu sunt orare: pagina 1 anunta
paritatea saptamanilor, iar pagina 2 tine doua tabele cu capacitatile salilor:

    +----------------+------------+     +------------------+------------+
    | Sala PBT       | Nr. locuri |     | Laborator PBT    | Nr. locuri |
    | Amf. 501       |    122     |     | L.410            |     15     |
    | S-214          |     70     |     | L-509 (iOS)      |     30     |

Segmentarea le gaseste singura (`ingest/tables.py`): ambele au liniile trasate, deci nu e
nevoie de pozitii fixe si nici de numarat randurile dinainte.

Tabelul asta e si dovada ca normalizarea salilor din `domain/rooms.py` era necesara: chiar
si aici, in lista oficiala, aceeasi conventie de scriere nu se tine -- `S-214` sta langa
`S.102`, `L-106` langa `L.410`. Legarea se face pe slug, care le aduce la aceeasi forma.

Ce **nu** face
--------------
Nu inventeaza capacitati. Sala scrisa cu `?` in sursa (L.414 Robotica) ramane `NULL`, iar
salile care nu apar deloc in tabel -- cele externe, Magurele, ONLINE -- raman la fel.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from orar.domain.rooms import normalizeaza_sala

log = logging.getLogger(__name__)

__all__ = ["Capacitate", "RaportCapacitati", "citeste_capacitati", "incarca_capacitati"]

#: Numarul de locuri e un intreg de una-trei cifre. `?` inseamna "nu se stie", si asa ramane.
_RE_LOCURI = re.compile(r"^\s*(?P<n>\d{1,3})\s*$")
#: Randuri de antet, care nu sunt sali.
_ANTETE = ("nr. locuri", "nr locuri", "sala pbt", "laborator pbt", "sală pbt")
#: Lamurirea din paranteza descrie dotarea, nu alta sala: tabelul scrie `L-509 (iOS)`,
#: orarul scrie doar `L-509`.
_RE_PARANTEZA = re.compile(r"\s*\([^)]*\)\s*$")


@dataclass(frozen=True)
class Capacitate:
    """O linie din tabel: sala asa cum e scrisa acolo, si cate locuri are."""

    #: Textul brut din tabel (`Amf. 501`, `L-509 (iOS)`).
    brut: str
    #: Slug-ul canonic, cheia de legatura cu `SALA`.
    slug: str
    #: Slug-ul fara lamurirea din paranteza, ca rezerva la legare.
    slug_scurt: str
    #: None cand sursa scrie `?`.
    locuri: int | None

    @property
    def nume(self) -> str:
        """Numele canonic al salii, cum apare in orar: `Amf. 501` -> `Amf.501`,
        `L-509 (iOS)` -> `L.509`."""
        return normalizeaza_sala(_RE_PARANTEZA.sub("", self.brut)).nume


@dataclass
class RaportCapacitati:
    citite: int = 0
    completate: int = 0
    #: Sali din tabel fara nicio ora in orar, adaugate acum in baza.
    adaugate: list[str] = field(default_factory=list)
    #: Sali din baza pentru care tabelul nu spune nimic.
    fara_capacitate: list[str] = field(default_factory=list)
    avertismente: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return "\n".join(
            [
                f"randuri citite      : {self.citite}",
                f"sali completate     : {self.completate}",
                f"fara ore, adaugate  : {len(self.adaugate)}",
                f"in orar, fara locuri: {len(self.fara_capacitate)}",
            ]
        )


def citeste_capacitati(imagine: Image.Image | Path | str, motor) -> list[Capacitate]:  # noqa: ANN001
    """Perechile (sala, locuri) din tabelele paginii.

    Un rand conteaza doar daca are **doua** celule: una care arata a sala si una cu numarul.
    Asa sar peste antet, peste randurile despartitoare goale si peste textul de sub tabel.
    """
    from orar.ingest.tables import citeste_tabel, detecteaza_tabele

    im = Image.open(imagine) if isinstance(imagine, str | Path) else imagine
    im = im.convert("RGB")

    out: list[Capacitate] = []
    for tabel in detecteaza_tabele(im):
        pe_rand: dict[int, dict[int, str]] = {}
        for celula in citeste_tabel(im, tabel, motor):
            pe_rand.setdefault(celula.rand, {})[celula.coloana] = celula.text

        for valori in pe_rand.values():
            nume = (valori.get(0) or "").strip()
            numar = (valori.get(1) or "").strip()
            if not nume or nume.lower() in _ANTETE:
                continue
            m = _RE_LOCURI.match(numar)
            out.append(
                Capacitate(
                    brut=nume,
                    slug=normalizeaza_sala(nume).slug,
                    slug_scurt=normalizeaza_sala(_RE_PARANTEZA.sub("", nume)).slug,
                    locuri=int(m.group("n")) if m else None,
                )
            )
    return out


def incarca_capacitati(sesiune, capacitati: list[Capacitate]) -> RaportCapacitati:  # noqa: ANN001
    """Scrie `SALA.NR_LOCURI` si aduce in baza salile din tabel care lipsesc.

    Legarea e pe slug, nu pe nume: tabelul scrie `Amf. 501`, orarul scrie `Amf.501`, iar
    `S-214` si `S.214` sunt aceeasi sala.

    Tabelul e lista oficiala a salilor facultatii, deci o sala din el care nu are nicio ora
    in orar **se creeaza**: e o sala libera tot semestrul, nu una inexistenta -- trebuie sa
    apara in lista de sali (cu 0% ocupare) si sa poata fi aleasa cand un admin muta o
    activitate.
    """
    from sqlalchemy import select

    from orar.db.models import Sala

    rap = RaportCapacitati(citite=len(capacitati))
    dupa_slug = {s.slug: s for s in sesiune.scalars(select(Sala))}

    for c in capacitati:
        sala = dupa_slug.get(c.slug) or dupa_slug.get(c.slug_scurt)
        if sala is None:
            norm = normalizeaza_sala(c.nume)
            sala = Sala(nume=norm.nume, slug=norm.slug, tip=norm.tip.value)
            sesiune.add(sala)
            dupa_slug[norm.slug] = sala
            rap.adaugate.append(norm.nume)
        if c.locuri is None:
            rap.avertismente.append(f"{c.brut}: sursa nu spune cate locuri are")
            continue
        sala.nr_locuri = c.locuri
        rap.completate += 1

    sesiune.flush()
    rap.fara_capacitate = sorted(
        s.nume for s in dupa_slug.values() if s.nr_locuri is None and s.tip == "fizica"
    )
    return rap
