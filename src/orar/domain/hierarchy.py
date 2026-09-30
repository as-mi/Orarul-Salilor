"""Decodarea titlului unei pagini de orar in pozitia ei din ierarhia academica.

Fiecare pagina din PDF-ul FMI are un titlu care identifica formatiunea de studiu.
Codificarea numerelor de grupa e regulata (verificat pe toate cele 98 de titluri):

    NNN  ->  cifra 1 = anul de studiu
             cifrele 1-2 = seria
             tot numarul = grupa

    "INFO Grupa 144"  ->  an 1, seria 14, grupa 144

Ierarhia rezultata (vezi `docs/formatul-orarului.md` §5):

    INFO an 2                 Nivel.SPECIALIZARE
    +-- Seria 24              Nivel.SERIE
        +-- 244               Nivel.GRUPA
            +-- 244/1         Nivel.SEMIGRUPA   (din eticheta "Gr_1" din celula)

Masteratele nu au serii, deci grupa atarna direct de specializare.
Paginile de optionale/facultative/limbi nu sunt formatiuni propriu-zise: se leaga de
grupele-tinta prin tabela de jonctiune ORA_GRUPA, nu prin PARINTE.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "Nivel",
    "TipPagina",
    "TitluOrar",
    "parse_titlu",
    "normalizeaza_specializare",
    "normalizeaza_semigrupa",
    "SPECIALIZARI",
    "CategoriePachet",
    "categorie_pachet",
    "an_din_text",
    "este_master",
    "cod_specializare",
    "specializari_vizate",
]


class Nivel(StrEnum):
    """Nivelul unui nod in arborele GRUPA (coloana GRUPA.TIP)."""

    SPECIALIZARE = "specializare"
    SERIE = "serie"
    GRUPA = "grupa"
    SEMIGRUPA = "semigrupa"
    OPTIONAL = "optional"


class TipPagina(StrEnum):
    """Ce fel de pagina de orar am parsat."""

    GRUPA = "grupa"
    MASTER = "master"
    OPTIONAL_SERII = "optional_serii"
    OPTIONAL = "optional"
    FACULTATIV = "facultativ"
    LIMBI = "limbi"
    SPECIAL = "special"
    NECUNOSCUT = "necunoscut"


# Coduri canonice de specializare. Cheile sunt formele intalnite in titluri
# (normalizate: uppercase, fara punct final, spatii colapsate).
SPECIALIZARI: dict[str, str] = {
    "MATE": "MATE",
    "MATEMATICA": "MATE",
    "MATE APL": "MATE-APL",
    "MATE APLICATE": "MATE-APL",
    "MATEMATICA APLICATA": "MATE-APL",
    "MATE-INFO": "MATE-INFO",
    "MATE INFO": "MATE-INFO",
    "MATEMATICA-INFORMATICA": "MATE-INFO",
    "INFO": "INFO",
    "INFORMATICA": "INFO",
    "CTI": "CTI",
}

# Denumiri lizibile, pentru afisare in UI.
DENUMIRI_SPECIALIZARE: dict[str, str] = {
    "MATE": "Matematică",
    "MATE-APL": "Matematici Aplicate",
    "MATE-INFO": "Matematică-Informatică",
    "INFO": "Informatică",
    "CTI": "Calculatoare și Tehnologia Informației",
}

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}


def normalizeaza_specializare(text: str) -> str | None:
    """Aduce o scriere oarecare a specializarii la codul canonic."""
    cheie = re.sub(r"\s+", " ", text.strip().upper()).rstrip(".")
    return SPECIALIZARI.get(cheie)


def normalizeaza_semigrupa(text: str) -> str | None:
    """ "Gr 1" / "Gr_1" / "Gr1" / "gr. 2"  ->  "Gr_1" / "Gr_2".

    Intoarce None daca textul nu contine o eticheta de semigrupa.
    """
    if not text:
        return None
    m = re.search(r"\bGr\.?[\s_]*([1-4])\b", text, re.IGNORECASE)
    return f"Gr_{m.group(1)}" if m else None


def _an_din_roman_sau_cifra(text: str) -> int | None:
    text = text.strip().upper()
    if text in _ROMAN:
        return _ROMAN[text]
    return int(text) if text.isdigit() else None


# ---------------------------------------------------------------------------
# Cautare permisiva: anul si specializarile vizate de o pagina de pachet
# ---------------------------------------------------------------------------

#: "an III", "anul 2", "An. IV", "anul: II" -- oriunde in titlu.
_RE_AN_ORIUNDE = re.compile(r"\ban(?:ul)?\s*[.:]?\s*(?P<an>[IVX]{1,4}|\d)\b", re.IGNORECASE)
_RE_MASTER_ORIUNDE = re.compile(r"\bmaster", re.IGNORECASE)
#: Separatori "tari": despart specializari diferite. Spatiul, cratima si punctul sunt "moi" --
#: leaga cuvintele aceleiasi specializari (`Mate-Info`, `Mate Apl.`).
_RE_SEPARATOR_TARE = re.compile(r"[,;/()\[\]:]|\bs[iî]\b|\bși\b", re.IGNORECASE)
_RE_CUVANT = re.compile(r"[^\W_]+")


def an_din_text(text: str) -> int | None:
    """Anul de studiu scris oriunde in text: `an III`, `anul 2`, `An. IV`."""
    m = _RE_AN_ORIUNDE.search(curata_titlu(text))
    return _an_din_roman_sau_cifra(m.group("an")) if m else None


def este_master(text: str) -> bool:
    return bool(_RE_MASTER_ORIUNDE.search(text or ""))


def _fara_diacritice(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _cuvinte(text: str) -> tuple[str, ...]:
    return tuple(w.upper() for w in _RE_CUVANT.findall(_fara_diacritice(text)))


def cod_specializare(text: str) -> str:
    """Codul unei specializari, si pentru una pe care n-o cunoastem inca.

    Cele din `SPECIALIZARI` isi pastreaza codul canonic; oricare alta primeste forma ei
    normalizata (`Bio Info` -> `BIO-INFO`), ca o specializare aparuta anul viitor sa aiba
    nodul ei in ierarhie in loc sa ramana fara parinte.
    """
    return normalizeaza_specializare(text) or "-".join(_cuvinte(text))


def _aliasuri(cunoscute: set[str]) -> dict[tuple[str, ...], str]:
    """Secventa de cuvinte -> cod. Din tabela fixa, plus codurile din ierarhie."""
    out = {_cuvinte(alias): cod for alias, cod in SPECIALIZARI.items()}
    for cod in cunoscute:
        out.setdefault(_cuvinte(cod), cod)
    return {k: v for k, v in out.items() if k}


def _repara_cuvant(cuvant: str, vocabular: set[str]) -> str:
    """`lnfo` -> `INFO`, `CTl` -> `CTI`: `l` citit in loc de `I`, numai daca asa devine un
    cuvant de specializare cunoscut. Altfel cuvantul ramane cum e."""
    sus = cuvant.upper()
    if sus in vocabular or "l" not in cuvant:
        return sus
    reparat = cuvant.replace("l", "I").upper()
    return reparat if reparat in vocabular else sus


def _cauta_specializari(text: str, aliasuri: dict[tuple[str, ...], str]) -> list[str]:
    """Codurile scrise in text, cel mai lung alias intai: `Mate Apl.` e MATE-APL, nu MATE."""
    vocabular = {c for alias in aliasuri for c in alias}
    lungimi = sorted({len(a) for a in aliasuri}, reverse=True)
    gasite: list[str] = []
    for segment in _RE_SEPARATOR_TARE.split(_fara_diacritice(text)):
        cuv = [_repara_cuvant(w, vocabular) for w in _RE_CUVANT.findall(segment)]
        i = 0
        while i < len(cuv):
            for n in lungimi:
                cod = aliasuri.get(tuple(cuv[i : i + n]))
                if cod:
                    if cod not in gasite:
                        gasite.append(cod)
                    i += n
                    break
            else:
                i += 1
    return gasite


def _descompune(cod: str, disponibile: set[str], aliasuri: dict[tuple[str, ...], str]) -> list[str]:
    """Un cod compus care nu exista in anul respectiv, luat pe bucati.

    `Limbi straine - an I (Mate Info, CTI)`: in anul I nu exista MATE-INFO, deci `Mate Info`
    (fara virgula) inseamna aici MATE si INFO. Descompunem doar cand bucatile exista toate.
    """
    if cod in disponibile or "-" not in cod:
        return [cod] if cod in disponibile else []
    bucati = [aliasuri.get((p,)) for p in cod.split("-")]
    return [b for b in bucati if b] if all(b in disponibile for b in bucati) else []


def specializari_vizate(titlu: str, disponibile: set[str]) -> list[str]:
    """Specializarile dintr-un titlu de pachet, dintre cele care exista in anul lui.

    Intai in afara parantezelor, apoi -- daca acolo nu e niciuna -- si in paranteze.
    `Optionale an III - MATE-INFO (Informatica)` e un pachet MATE-INFO: paranteza spune
    *ce fel* de optionale sunt, nu ca le-ar face si specializarea INFO. In schimb la
    `Facultative an II (Mate, Info, CTI)` lista e chiar in paranteza.

    Lista goala = titlul nu numeste nicio specializare din an. Apelantul decide ce face
    atunci (loader-ul cade inapoi pe tot anul).
    """
    aliasuri = _aliasuri(disponibile)
    afara = re.sub(r"\([^)]*\)", " ", titlu or "")
    for text in (afara, titlu or ""):
        coduri: list[str] = []
        for cod in _cauta_specializari(text, aliasuri):
            for c in _descompune(cod, disponibile, aliasuri):
                if c not in coduri:
                    coduri.append(c)
        if coduri:
            return coduri
    return []


class CategoriePachet(StrEnum):
    """Ce fel de activitati tine o pagina care nu e formatiune."""

    OPTIONAL = "optional"
    FACULTATIV = "facultativ"
    LIMBI = "limbi"
    ALTELE = "altele"

    @property
    def eticheta(self) -> str:
        return {
            "optional": "Opționale",
            "facultativ": "Facultative",
            "limbi": "Limbi străine",
            "altele": "Alte activități",
        }[self.value]


def categorie_pachet(nume: str) -> CategoriePachet:
    """Dupa cuvintele din titlu, oriunde ar fi, cu sau fara diacritice."""
    text = _fara_diacritice(nume or "").lower()
    if "facultativ" in text:
        return CategoriePachet.FACULTATIV
    if re.search(r"\blimb[ai]\b", text):
        return CategoriePachet.LIMBI
    if "optional" in text:
        return CategoriePachet.OPTIONAL
    return CategoriePachet.ALTELE


@dataclass(frozen=True)
class TitluOrar:
    """Rezultatul decodarii unui titlu de pagina."""

    raw: str
    tip: TipPagina
    specializare: str | None = None
    an: int | None = None
    serie: str | None = None
    grupa: str | None = None
    #: Seriile vizate de o pagina de optionale ("INFO Seriile 33,34,35: ...").
    serii_tinta: tuple[str, ...] = ()
    #: Specializarile vizate de o pagina transversala (facultative, limbi straine).
    specializari_tinta: tuple[str, ...] = ()
    #: Codul programului de master, ex. "BDTS".
    program: str | None = None
    #: Denumirea desfasurata a programului, ex. "Baze de date si tehnologii software".
    denumire_program: str | None = None
    #: Eticheta scurta pentru afisare.
    eticheta: str = ""
    #: Slug stabil, folosit in URL-uri.
    slug: str = ""
    note: tuple[str, ...] = field(default=(), compare=False)

    @property
    def este_formatiune(self) -> bool:
        """True daca pagina descrie o grupa reala (nu un pachet de optionale)."""
        return self.tip in (TipPagina.GRUPA, TipPagina.MASTER)


def _slugify(text: str) -> str:
    text = text.lower()
    for a, b in (
        ("ă", "a"),
        ("â", "a"),
        ("î", "i"),
        ("ș", "s"),
        ("ş", "s"),
        ("ț", "t"),
        ("ţ", "t"),
    ):
        text = text.replace(a, b)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _split_specializari(text: str) -> tuple[str, ...]:
    """ "(Mate, Mate-Info, Mate Apl., Info, CTI)" -> coduri canonice."""
    out: list[str] = []
    for bucata in re.split(r"[,;]| si | și ", text):
        cod = normalizeaza_specializare(bucata)
        if cod and cod not in out:
            out.append(cod)
    return tuple(out)


# ---------------------------------------------------------------------------
# Sabloanele de titlu, in ordinea in care se incearca.
# ---------------------------------------------------------------------------

# "INFO Grupa 144", "MATE APL. Grupa 221", "MATE-INFO Grupa 211"
_RE_GRUPA = re.compile(r"^(?P<spec>[A-Za-z][\w\s.\-]*?)\s+Grupa\s+(?P<nr>\d{3})\s*$", re.IGNORECASE)

# "INFO Master 408 (SD - Sisteme distribuite)"
_RE_MASTER = re.compile(
    r"^(?P<spec>[A-Za-z][\w\s.\-]*?)\s+Master\s+(?P<nr>\d{3})"
    r"(?:\s*\((?P<cod>[^\s)]+)\s*[-–]\s*(?P<den>[^)]*)\))?\s*$",
    re.IGNORECASE,
)

# "INFO Seriile 33,34,35: Optionale an III - INFO (Curs)"
_RE_SERII = re.compile(
    r"^(?P<spec>[A-Za-z][\w\s.\-]*?)\s+Seriile\s+(?P<serii>[\d,\s]+)\s*:\s*(?P<rest>.+)$",
    re.IGNORECASE,
)

# "Optionale an III - MATE (1)", "Optionale an I Master INFO"
_RE_OPTIONAL = re.compile(
    r"^Optionale?\s+an\s+(?P<an>[IVX]+|\d+)\s*(?:[-–]\s*)?(?P<rest>.*)$", re.IGNORECASE
)

# "Facultative an II (Mate, Mate-Info, Mate Apl., Info, CTI)"
_RE_FACULTATIV = re.compile(
    r"^Facultative\s+an\s+(?P<an>[IVX]+|\d+)\s*(?:\((?P<spec>[^)]*)\))?\s*$", re.IGNORECASE
)

# "Limbi straine - an I (Mate Info, CTI)"
_RE_LIMBI = re.compile(
    r"^Limbi\s+strain[ei]\s*[-–]?\s*an\s+(?P<an>[IVX]+|\d+)\s*(?:\((?P<spec>[^)]*)\))?\s*$",
    re.IGNORECASE,
)

# "(studenti Fizica/Robotica, an 2, grupa 201ROB)"
_RE_ROBOTICA = re.compile(
    r"studenti\s+Fizica/Robotica,\s*an\s*(?P<an>\d+),\s*grupa\s*(?P<grupa>\w+)", re.IGNORECASE
)


# Confuziile pe care recunoasterea le face constant la fontul asteia: `I` majuscul si `l`
# mic sunt aproape identice in Arial, la fel `0` si `O`. Le reparam *inainte* de parsare,
# fiindca pica exact pe partile care duc ierarhia: `an III`, `Master 403`, `Seriile 33,34`.
# Corectam numai in interiorul unui token deja omogen -- un cuvant care e altfel numai cifre
# romane, sau altfel numai cifre -- deci nu putem strica un cuvant obisnuit.
_RE_ROMAN_STRICAT = re.compile(r"\b(?=[IVXl]{2,})[IVXl]+\b")
_RE_NUMAR_STRICAT = re.compile(r"\b(?=\d*[O]\d)[\dO]{3}\b")


#: Cuvintele din care sunt facute numele de specializare (MATE, INFO, APL, ...).
_CUVINTE_SPECIALIZARE = {w for alias in SPECIALIZARI for w in re.findall(r"[A-Z]+", alias)}


def _repara_specializare(m: re.Match[str]) -> str:
    """`CTl` -> `CTI`, `Mate-lnfo` -> `Mate-Info`, dar numai daca rezultatul e un cuvant
    de specializare cunoscut."""
    token = m.group(0)
    if token.upper() in _CUVINTE_SPECIALIZARE or "l" not in token:
        return token
    reparat = token.replace("l", "I")
    return reparat if reparat.upper() in _CUVINTE_SPECIALIZARE else token


def curata_titlu(titlu: str) -> str:
    """Repara confuziile de glife dintr-un titlu citit cu OCR."""
    raw = re.sub(r"\s+", " ", (titlu or "").strip())
    raw = _RE_ROMAN_STRICAT.sub(lambda m: m.group(0).replace("l", "I"), raw)
    raw = _RE_NUMAR_STRICAT.sub(lambda m: m.group(0).replace("O", "0"), raw)
    # Codurile de specializare sunt o lista inchisa, deci `CTl` se repara fara risc.
    raw = re.sub(r"\b[A-Za-z]{2,4}\b", _repara_specializare, raw)
    # `Seriile` iese des `Serile`: nu e confuzie de glif, ci o litera pierduta intre doi `i`.
    return re.sub(r"\bSeri+le\b", "Seriile", raw, flags=re.IGNORECASE)


def parse_titlu(titlu: str) -> TitluOrar:
    """Decodeaza titlul unei pagini de orar.

    Nu arunca niciodata: un titlu nerecunoscut intoarce ``TipPagina.NECUNOSCUT``,
    ca o pagina ciudata sa nu opreasca tot ingestul (vezi §9 din plan).
    """
    raw = curata_titlu(titlu)
    if not raw:
        return TitluOrar(raw="", tip=TipPagina.NECUNOSCUT, eticheta="", slug="")

    if m := _RE_GRUPA.match(raw):
        return _construieste_grupa(raw, m.group("spec"), m.group("nr"))

    if m := _RE_MASTER.match(raw):
        return _construieste_master(raw, m)

    if m := _RE_SERII.match(raw):
        serii = tuple(s.strip() for s in m.group("serii").split(",") if s.strip())
        spec = normalizeaza_specializare(m.group("spec"))
        an = int(serii[0][0]) if serii and serii[0][:1].isdigit() else None
        return TitluOrar(
            raw=raw,
            tip=TipPagina.OPTIONAL_SERII,
            specializare=spec,
            an=an,
            serii_tinta=serii,
            eticheta=m.group("rest").strip(),
            slug=_slugify(raw),
        )

    if m := _RE_FACULTATIV.match(raw):
        return TitluOrar(
            raw=raw,
            tip=TipPagina.FACULTATIV,
            an=_an_din_roman_sau_cifra(m.group("an")),
            specializari_tinta=_split_specializari(m.group("spec") or ""),
            eticheta=raw,
            slug=_slugify(raw),
        )

    if m := _RE_LIMBI.match(raw):
        return TitluOrar(
            raw=raw,
            tip=TipPagina.LIMBI,
            an=_an_din_roman_sau_cifra(m.group("an")),
            specializari_tinta=_split_specializari(m.group("spec") or ""),
            eticheta=raw,
            slug=_slugify(raw),
        )

    if m := _RE_OPTIONAL.match(raw):
        rest = m.group("rest").strip()
        # "Master INFO" -> specializarea e in rest; altfel "MATE (1)" / "MATE-INFO (Informatica)"
        e_master = bool(re.search(r"\bMaster\b", rest, re.IGNORECASE))
        curat = re.sub(r"\bMaster\b", "", rest, flags=re.IGNORECASE)
        curat = re.sub(r"\([^)]*\)", "", curat).strip()
        return TitluOrar(
            raw=raw,
            tip=TipPagina.OPTIONAL,
            specializare=normalizeaza_specializare(curat),
            an=_an_din_roman_sau_cifra(m.group("an")),
            program="master" if e_master else None,
            eticheta=raw,
            slug=_slugify(raw),
        )

    if m := _RE_ROBOTICA.search(raw):
        return TitluOrar(
            raw=raw,
            tip=TipPagina.SPECIAL,
            an=int(m.group("an")),
            grupa=m.group("grupa"),
            eticheta=f"Fizică/Robotică {m.group('grupa')}",
            slug=_slugify(m.group("grupa")),
        )

    # Niciun sablon strict, dar cuvintele spun ce e: `Pachet opțional anul 2 - INFO`,
    # `Cursuri facultative, an III`. Anul se cauta oriunde in titlu; specializarile le
    # rezolva loader-ul, fata de ce exista in ierarhie -- inclusiv caderea pe tot anul.
    categorie = categorie_pachet(raw)
    if categorie is not CategoriePachet.ALTELE:
        return TitluOrar(
            raw=raw,
            tip={
                CategoriePachet.OPTIONAL: TipPagina.OPTIONAL,
                CategoriePachet.FACULTATIV: TipPagina.FACULTATIV,
                CategoriePachet.LIMBI: TipPagina.LIMBI,
            }[categorie],
            an=an_din_text(raw),
            program="master" if este_master(raw) else None,
            eticheta=raw,
            slug=_slugify(raw),
            note=("titlu de pachet recunoscut dupa cuvinte, nu dupa sablon",),
        )

    # Nerecunoscut de niciun sablon. Il marcam cinstit, dar NU il aruncam: pagina are
    # activitati reale care ocupa sali reale, iar /sala/{id} ar subestima ocuparea daca
    # le-am ignora. Loader-ul il ataseaza ca nod orfan (PARINTE = NULL) si logheaza.
    return TitluOrar(
        raw=raw,
        tip=TipPagina.NECUNOSCUT,
        eticheta=raw,
        slug=_slugify(raw),
        note=("titlu nerecunoscut de niciun sablon; atasat ca nod orfan",),
    )


def _construieste_grupa(raw: str, spec_text: str, nr: str) -> TitluOrar:
    # O specializare necunoscuta (una noua, anul viitor) primeste codul ei normalizat, ca sa
    # aiba nod de specializare -- altfel grupele ei n-ar primi niciun pachet pe an.
    spec = cod_specializare(spec_text) or None
    an = int(nr[0])
    serie = nr[:2]
    return TitluOrar(
        raw=raw,
        tip=TipPagina.GRUPA,
        specializare=spec,
        an=an,
        serie=serie,
        grupa=nr,
        eticheta=nr,
        slug=nr,
        note=()
        if normalizeaza_specializare(spec_text)
        else (f"specializare noua {spec!r}, din {spec_text!r}",),
    )


def _construieste_master(raw: str, m: re.Match[str]) -> TitluOrar:
    spec = normalizeaza_specializare(m.group("spec"))
    nr = m.group("nr")
    # 4xx = anul I de master, 5xx = anul II.
    an = {"4": 1, "5": 2}.get(nr[0])
    cod = (m.group("cod") or "").strip() or None
    den = (m.group("den") or "").strip()
    return TitluOrar(
        raw=raw,
        tip=TipPagina.MASTER,
        specializare=spec,
        an=an,
        grupa=nr,
        program=cod,
        denumire_program=den or None,
        eticheta=f"{nr} ({cod})" if cod else nr,
        slug=nr,
        note=() if spec else (f"specializare necunoscuta: {m.group('spec')!r}",),
    )
