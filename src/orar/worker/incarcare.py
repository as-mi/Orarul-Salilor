"""Incarcarea unui orar dintr-un link dat de admin, in fundal.

Din panoul /admin, un admin da linkul unui orar cu aceeasi structura ca al FMI -- PDF-ul aSc
publicat pe Google Drive -- iar aici se ruleaza exact lantul lui `sincronizeaza`: captura,
segmentare, OCR, incarcare, consolidare; optional si verificarea cu orarul profesorilor.
Orarul de pana atunci ramane ca versiune anterioara.

De ce in fundal
---------------
Captura din Drive si citirea a ~100 de pagini dureaza minute bune; o cerere HTTP nu poate
astepta atat. Lantul ruleaza intr-un fir separat, iar panoul ii intreaba starea.

Ce **nu** e
-----------
Nu e o coada: o singura incarcare o data, iar starea se tine in memoria procesului. Cu mai
multi workeri `uvicorn` fiecare ar avea starea lui -- la fel ca planificatorul zilnic, merge
cu un singur worker. La repornirea serverului starea se pierde, dar nu si datele: incarcarea
e o singura tranzactie, deci ori s-a terminat, ori n-a schimbat nimic.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

__all__ = [
    "GAZDE_PERMISE",
    "Cerere",
    "LinkRespins",
    "Stare",
    "rezolva_link",
    "porneste",
    "stare_curenta",
    "valideaza_link",
    "valideaza_an",
]

#: De unde acceptam linkuri. Serverul chiar deschide adresa intr-un browser, deci nu lasam
#: un admin (sau un cont de admin compromis) sa-l trimita oriunde in retea: doar Drive si
#: scurtatorul folosit pe pagina FMI.
GAZDE_PERMISE = ("drive.google.com", "docs.google.com", "bit.ly")

_RE_AN = re.compile(r"^(\d{4})-(\d{4})$")


def valideaza_link(url: str) -> str | None:
    """Mesajul de eroare pentru un link care nu poate fi un orar, sau None daca e in regula."""
    url = (url or "").strip()
    if not url:
        return "Lipsește linkul."
    try:
        parti = urlsplit(url)
    except ValueError:
        return "Linkul nu e o adresă validă."
    if parti.scheme != "https":
        return "Linkul trebuie să înceapă cu https://."
    gazda = (parti.hostname or "").lower()
    if gazda not in GAZDE_PERMISE:
        return f"Linkul trebuie să fie de pe {', '.join(GAZDE_PERMISE)}."
    if parti.username or parti.password or parti.port:
        return "Linkul nu poate conține utilizator, parolă sau port."
    return None


class LinkRespins(ValueError):
    """Linkul duce in alta parte decat pe Drive."""


def rezolva_link(url: str, *, salturi: int = 4) -> str:
    """Adresa de pe Drive la care duce linkul, urmand redirectarile unui scurtator.

    Un `bit.ly` poate trimite oriunde -- inclusiv catre o adresa din reteaua serverului. Nu
    lasam browserul de captura sa afle asta singur: urmam noi redirectarile, fara sa
    descarcam nimic, si acceptam rezultatul doar daca fiecare pas ramane pe gazdele permise
    si ultimul e pe Drive.
    """
    import httpx

    for _ in range(salturi):
        if eroare := valideaza_link(url):
            raise LinkRespins(eroare)
        if urlsplit(url).hostname != "bit.ly":
            return url
        raspuns = httpx.get(url, follow_redirects=False, timeout=20)
        tinta = raspuns.headers.get("location")
        if not raspuns.is_redirect or not tinta:
            raise LinkRespins("Linkul scurt nu duce nicăieri.")
        url = tinta
    raise LinkRespins("Linkul are prea multe redirectări.")


def valideaza_an(an: str) -> str | None:
    """`2025-2026`: doi ani consecutivi."""
    m = _RE_AN.match((an or "").strip())
    if not m or int(m.group(2)) != int(m.group(1)) + 1:
        return "Anul universitar se scrie ca 2025-2026."
    return None


@dataclass(frozen=True)
class Cerere:
    url_grupe: str
    semestru: int
    an_universitar: str
    #: Orarul profesorilor, pentru verificarea incrucisata si numele intregi. Obligatoriu.
    url_profesori: str = ""
    #: Cine a pornit-o, pentru afisare.
    de_catre: str = ""


@dataclass
class Stare:
    """Unde a ajuns ultima incarcare. `faza`: inactiv | in_lucru | reusit | esuat."""

    faza: str = "inactiv"
    cerere: Cerere | None = None
    etapa: str = ""
    pornit_la: datetime | None = None
    terminat_la: datetime | None = None
    #: Ce s-a facut si ce avertismente au fost, pe randuri.
    raport: list[str] = field(default_factory=list)
    eroare: str = ""

    @property
    def in_lucru(self) -> bool:
        return self.faza == "in_lucru"


_lacat = threading.Lock()
_stare = Stare()
_fir: threading.Thread | None = None


def stare_curenta() -> Stare:
    with _lacat:
        return Stare(**{**_stare.__dict__, "raport": list(_stare.raport)})


def porneste(cerere: Cerere) -> bool:
    """Porneste incarcarea in fundal. False daca ruleaza deja una."""
    global _stare, _fir
    with _lacat:
        if _stare.in_lucru:
            return False
        _stare = Stare(faza="in_lucru", cerere=cerere, etapa="pornesc", pornit_la=datetime.now())
        _fir = threading.Thread(target=_ruleaza, args=(cerere,), name="incarcare-orar", daemon=True)
        _fir.start()
    return True


def _etapa(mesaj: str) -> None:
    with _lacat:
        _stare.etapa = mesaj


def _incheie(faza: str, *, raport: list[str], eroare: str = "") -> None:
    with _lacat:
        _stare.faza = faza
        _stare.etapa = ""
        _stare.raport = raport
        _stare.eroare = eroare
        _stare.terminat_la = datetime.now()


def _ruleaza(cerere: Cerere) -> None:
    try:
        raport = _incarca(cerere)
    except LinkRespins as e:
        _incheie("esuat", raport=[], eroare=str(e))
        return
    except Exception as e:  # noqa: BLE001 -- firul nu are cui sa arunce; eroarea ajunge in panou
        log.exception("incarcarea orarului a esuat")
        _incheie("esuat", raport=[], eroare=f"{type(e).__name__}: {e}")
        return
    if raport.ingestate:
        _incheie("reusit", raport=[*raport.ingestate, *raport.crosscheck, *raport.avertismente])
    else:
        _incheie(
            "esuat",
            raport=raport.avertismente,
            eroare="Orarul nu a putut fi citit; baza de date a rămas neschimbată.",
        )


def _incarca(cerere: Cerere):  # noqa: ANN202 -- RaportSincronizare
    """Lantul lui `sincronizeaza`, pe linkul dat. O singura tranzactie: daca pica oriunde,
    `sesiune()` face rollback si orarul vechi ramane neatins."""
    from orar.db.session import sesiune
    from orar.ingest import watcher
    from orar.worker import sync

    # Data "publicarii" e momentul incarcarii: asa orarul inlocuit devine o versiune
    # anterioara, iar acesta e cel actual.
    acum = datetime.now().replace(microsecond=0)
    _etapa("verific linkul")
    grupe = watcher.SursaOrar(cerere.semestru, "grupe", rezolva_link(cerere.url_grupe), acum)
    # orarul profesorilor e obligatoriu: fara el nu se face verificarea incrucisata
    if not cerere.url_profesori:
        raise LinkRespins("Lipsește linkul orarului profesorilor.")
    surse = [
        grupe,
        watcher.SursaOrar(cerere.semestru, "profesori", rezolva_link(cerere.url_profesori), acum),
    ]

    rap = sync.RaportSincronizare()
    director = Path("data/screenshots")
    with sesiune() as s:
        sync._ingesteaza(
            s,
            grupe,
            an_universitar=cerere.an_universitar,
            director=director,
            rap=rap,
            progres=_etapa,
        )
        if rap.ingestate:
            # `EroareCrosscheck` iese din `sesiune()`: rollback, orarul vechi ramane neatins
            sync._crosscheck(
                s,
                watcher.StarePublicata(surse=surse),
                cerere.semestru,
                an_universitar=cerere.an_universitar,
                director=director,
                rap=rap,
                progres=_etapa,
            )
    return rap
