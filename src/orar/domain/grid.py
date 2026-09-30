"""Construieste grila saptamanala pe care o randeaza sabloanele.

Aceeasi structura serveste si `/grupa/{id}` si `/sala/{id}` -- difera doar multimea de ore
primita. Fiecare zi devine un rand cu una sau mai multe *benzi*: cand doua activitati se
suprapun in timp (semigrupe diferite, saptamani alternante), ele coboara pe benzi separate,
exact ca in PDF-ul original.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from orar.db.models import Ora
from orar.domain.weeks import Saptamana, parse_interval_saptamani

__all__ = [
    "Provenienta",
    "Bloc",
    "RandZi",
    "Grila",
    "construieste_grila",
    "EvenimentInZi",
    "se_tine",
    "ZILE",
    "ZILE_SAPTAMANA",
    "ORA_MIN",
    "ORA_MAX",
]

ZILE = ("Luni", "Marti", "Miercuri", "Joi", "Vineri")
#: Toata saptamana, in ordinea din `date.weekday()`. Orarul saptamanal are doar zilele
#: lucratoare; sambata si duminica apar in grila numai cand au un eveniment.
ZILE_SAPTAMANA = (*ZILE, "Sambata", "Duminica")
ORA_MIN = 8
ORA_MAX = 20  # exclusiv: ultima coloana e 19:00-20:00
#: Pana unde se poate intinde grila pentru un eveniment de seara.
ORA_MAX_EVENIMENT = 24


@dataclass
class EvenimentInZi:
    """Un eveniment cu data (`db.models.Eveniment`), in ziua din saptamana afisata in care
    cade -- vezi `db.evenimente.evenimente_in_grila`."""

    eveniment: object
    zi: str


class Provenienta(StrEnum):
    """De unde vine o ora, raportat la formatiunea ceruta."""

    PROPRIE = "proprie"
    #: De la un stramos: curs de serie sau de an.
    MOSTENITA = "mostenita"
    #: De la un descendent: laborator de semigrupa.
    SEMIGRUPA = "semigrupa"
    #: Prin ORA_GRUPA -- optional, facultativ, limba straina, activitate comuna.
    PARTAJATA = "partajata"


@dataclass
class Bloc:
    """O activitate, pozitionata in grila."""

    #: Activitatea din orarul saptamanal; None pentru un eveniment.
    ora: Ora | None
    col_start: int  # 1-based, relativ la ORA_MIN
    col_span: int
    provenienta: Provenienta
    #: Evenimentul cu data (ASMI sau alta activitate), cand blocul nu e o ora de orar.
    eveniment: object | None = None
    #: Nodul de la care vine, afisat doar cand e util (ex. "Seria 24").
    sursa: str = ""
    #: False daca activitatea nu se tine in saptamana selectata.
    activa: bool = True

    @property
    def clasa_tip(self) -> str:
        """Clasa CSS dupa tipul activitatii."""
        if self.eveniment is not None:
            return f"eveniment-{self.eveniment.fel}"
        tip = (self.ora.tip_ora_materie or "").lower()
        if "curs" in tip:
            return "curs"
        if "lab" in tip:
            return "lab"
        if "sem" in tip:
            return "seminar"
        if "proiect" in tip:
            return "proiect"
        return "alta"


@dataclass
class RandZi:
    zi: str
    benzi: list[list[Bloc]] = field(default_factory=list)

    @property
    def nr_benzi(self) -> int:
        return max(1, len(self.benzi))


@dataclass
class Grila:
    zile: list[RandZi]
    ore: tuple[int, ...] = tuple(range(ORA_MIN, ORA_MAX))
    #: Cate activitati au fost ascunse de filtrul de saptamana.
    inactive: int = 0

    @property
    def nr_coloane(self) -> int:
        return len(self.ore)

    @property
    def are_evenimente(self) -> bool:
        return any(b.eveniment is not None for zi in self.zile for banda in zi.benzi for b in banda)

    @property
    def total_blocuri(self) -> int:
        return sum(len(b) for zi in self.zile for b in zi.benzi)


def _coloane(o, ora_max: int = ORA_MAX) -> tuple[int, int]:  # noqa: ANN001 -- Ora sau Eveniment
    """(col_start 1-based, col_span) pentru o activitate."""
    start = min(max(ORA_MIN, o.ora_inceput.hour), ora_max - 1)
    sfarsit = min(ora_max, _ora_de_sfarsit(o.ora_sfarsit) or ora_max)
    if sfarsit <= start:
        sfarsit = start + 1
    return start - ORA_MIN + 1, sfarsit - start


def _ora_de_sfarsit(t) -> int:  # noqa: ANN001 -- datetime.time
    """Ora la care se termina, rotunjita in sus: 19:30 ocupa si coloana 19:00-20:00."""
    return t.hour + (1 if t.minute else 0)


def _se_suprapun(a: Bloc, b: Bloc) -> bool:
    return a.col_start < b.col_start + b.col_span and b.col_start < a.col_start + a.col_span


def _asaza_in_benzi(blocuri: list[Bloc]) -> list[list[Bloc]]:
    """Impachetare greedy: fiecare bloc merge pe prima banda unde incape."""
    benzi: list[list[Bloc]] = []
    for bloc in sorted(blocuri, key=lambda b: (b.col_start, -b.col_span)):
        for banda in benzi:
            if not any(_se_suprapun(bloc, alt) for alt in banda):
                banda.append(bloc)
                break
        else:
            benzi.append([bloc])
    return benzi


def se_tine(o: Ora, saptamana: Saptamana | None) -> bool:
    """Se tine activitatea in saptamana academica data? (public: il foloseste si cautarea
    de sali libere)"""
    return _este_activa(o, saptamana)


def _este_activa(o: Ora, saptamana: Saptamana | None) -> bool:
    """Se tine activitatea in saptamana data?

    Fara o saptamana academica cunoscuta (vacanta), consideram tot activ -- e mai bine sa
    aratam prea mult decat sa ascundem ore reale pe baza unui numar inventat.
    """
    if saptamana is None:
        return True
    if o.frecventa and o.frecventa != saptamana.paritate.value:
        return False
    if o.saptamani:
        interval = parse_interval_saptamani(o.saptamani)
        if interval is not None and saptamana.numar not in interval:
            return False
    return True


def construieste_grila(
    ore: list[Ora],
    *,
    provenienta: dict[int, Provenienta] | None = None,
    surse: dict[int, str] | None = None,
    saptamana: Saptamana | None = None,
    doar_saptamana_curenta: bool = False,
    evenimente: list[EvenimentInZi] | None = None,
) -> Grila:
    """Aranjeaza o lista de ore in grila saptamanala.

    `provenienta`/`surse` sunt indexate dupa `Ora.id`; lipsa lor inseamna "proprie".
    Cu `saptamana` data, activitatile care nu se tin atunci sunt marcate `activa=False`
    (sau eliminate de tot, cu `doar_saptamana_curenta`).

    `evenimente`: activitatile cu data din saptamana afisata. Pentru ele grila se poate
    intinde: apar randuri de sambata / duminica si coloane dupa ora 20, doar cat e nevoie.
    """
    provenienta = provenienta or {}
    surse = surse or {}

    evenimente = evenimente or []
    # weekendul intra in grila doar cu un eveniment; la fel orele de seara
    zile = [z for z in ZILE_SAPTAMANA if z in ZILE or any(e.zi == z for e in evenimente)]
    ora_max = min(
        ORA_MAX_EVENIMENT,
        max([ORA_MAX, *(_ora_de_sfarsit(e.eveniment.ora_sfarsit) for e in evenimente)]),
    )
    pe_zi: dict[str, list[Bloc]] = {z: [] for z in zile}
    inactive = 0

    for o in ore:
        if o.zi_saptamana not in pe_zi:
            continue
        col_start, col_span = _coloane(o)
        activa = _este_activa(o, saptamana)
        if not activa:
            inactive += 1
            if doar_saptamana_curenta:
                continue
        pe_zi[o.zi_saptamana].append(
            Bloc(
                ora=o,
                col_start=col_start,
                col_span=col_span,
                provenienta=provenienta.get(o.id, Provenienta.PROPRIE),
                sursa=surse.get(o.id, ""),
                activa=activa,
            )
        )

    for e in evenimente:
        col_start, col_span = _coloane(e.eveniment, ora_max)
        pe_zi[e.zi].append(
            Bloc(
                ora=None,
                col_start=col_start,
                col_span=col_span,
                provenienta=Provenienta.PROPRIE,
                eveniment=e.eveniment,
            )
        )

    return Grila(
        zile=[RandZi(zi=z, benzi=_asaza_in_benzi(pe_zi[z])) for z in zile],
        ore=tuple(range(ORA_MIN, ora_max)),
        inactive=inactive,
    )
