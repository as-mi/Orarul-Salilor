"""Normalizarea denumirilor de sali.

Orarul foloseste separatori inconsecventi: `L-507` si `L-508` cu liniuta, dar `L.410` si
`L.506` cu punct; la fel `S-214` vs `S.213`. Verificat pe datele curente: **nicio sala nu
apare in ambele forme**, deci normalizarea nu uneste duplicate azi. O facem oricum pentru ca:

  - da un slug canonic stabil, deci `/sala/L-507` si `/sala/L.507` duc in acelasi loc;
  - OCR-ul (Etapa 5) confunda usor `.` cu `-`, iar fara forma canonica ar inventa sali noi;
  - orarul urmator poate schimba separatorul unei sali, si atunci datele s-ar rupe in doua.

Clasificam si *tipul* locului, pentru ca nu tot ce apare in coloana "sala" e o sala:

    FIZICA   sali in cladirea facultatii (Amf.501, L-507, S.213) -- intra in statistici
    EXTERNA  locatii reale, dar in alta cladire (IMAR, Magurele) -- ocupate, dar nu ale noastre
    VIRTUALA ONLINE, "lab.", string gol -- nu ocupa niciun spatiu fizic
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["TipSala", "SalaNormalizata", "normalizeaza_sala"]


class TipSala(StrEnum):
    FIZICA = "fizica"
    EXTERNA = "externa"
    VIRTUALA = "virtuala"


#: Prefixele cunoscute si forma lor canonica.
_PREFIXE = {
    "AMF": "Amf",
    "AMFITEATRUL": "Amf",
    "L": "L",
    "LAB": "L",
    "S": "S",
}

#: Denumiri care nu sunt sali fizice ale facultatii.
_VIRTUALE = {"", "online", "lab.", "lab", "-"}

_RE_SALA = re.compile(
    r"^\s*(?P<prefix>[A-Za-z]+)\s*[.\-\s]?\s*(?P<numar>\d{2,4})\s*(?P<suffix>.*?)\s*$"
)
_RE_IMAR = re.compile(r"^\s*IMAR\s*\(\s*sala\s*(?P<numar>\d+)\s*\)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class SalaNormalizata:
    """Rezultatul normalizarii: forma canonica + slug pentru URL + clasificare."""

    nume: str
    slug: str
    tip: TipSala
    #: Textul original, exact cum a aparut in orar.
    raw: str

    @property
    def este_bookabila(self) -> bool:
        """Intra in calculul gradului de ocupare?"""
        return self.tip is TipSala.FIZICA


def _slugify(text: str) -> str:
    text = text.lower()
    for a, b in (("ă", "a"), ("â", "a"), ("î", "i"), ("ș", "s"), ("ț", "t")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def normalizeaza_sala(raw: str | None) -> SalaNormalizata:
    """Aduce o denumire de sala la forma canonica.

    >>> normalizeaza_sala("L-507").nume
    'L.507'
    >>> normalizeaza_sala("L.506").nume
    'L.506'
    >>> normalizeaza_sala("S-214").nume
    'S.214'
    >>> normalizeaza_sala("ONLINE").tip
    <TipSala.VIRTUALA: 'virtuala'>
    >>> normalizeaza_sala("L-414 Robotica").nume
    'L.414 Robotica'
    >>> normalizeaza_sala("L.414_Robotica").nume
    'L.414 Robotica'
    """
    original = (raw or "").strip()
    cheie = original.lower()

    if cheie in _VIRTUALE:
        nume = original.upper() if original else "Nespecificat"
        return SalaNormalizata(nume=nume, slug=_slugify(nume), tip=TipSala.VIRTUALA, raw=original)

    if m := _RE_IMAR.match(original):
        nume = f"IMAR {m.group('numar')}"
        return SalaNormalizata(nume=nume, slug=_slugify(nume), tip=TipSala.EXTERNA, raw=original)

    # Locatii externe recunoscute dupa cuvant-cheie.
    if re.search(r"m[ăa]gurele|fizica", cheie):
        nume = re.sub(r"\s+", " ", original).strip()
        return SalaNormalizata(nume=nume, slug=_slugify(nume), tip=TipSala.EXTERNA, raw=original)

    if m := _RE_SALA.match(original):
        prefix_raw = m.group("prefix").upper()
        prefix = _PREFIXE.get(prefix_raw)
        if prefix:
            # `_` si spatiile repetate din lamurire sunt zgomot de scriere: tabelul salilor
            # scrie `L.414_Robotica`, celulele `L.414 Robotica` -- aceeasi sala, acelasi nume.
            suffix = " ".join(m.group("suffix").replace("_", " ").split())
            nume = f"{prefix}.{m.group('numar')}"
            if suffix:
                nume = f"{nume} {suffix}"
            return SalaNormalizata(nume=nume, slug=_slugify(nume), tip=TipSala.FIZICA, raw=original)

    # Nerecunoscut: pastram textul asa cum e, dar nu il numaram ca sala fizica.
    nume = re.sub(r"\s+", " ", original)
    return SalaNormalizata(nume=nume, slug=_slugify(nume), tip=TipSala.EXTERNA, raw=original)
