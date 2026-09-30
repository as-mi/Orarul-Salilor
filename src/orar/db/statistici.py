"""Orarul in cifre: cati oameni au ore in fiecare interval, si cat de pline sunt salile.

Pentru admini, ca sa vada cand e liber publicul unui eveniment si cat de incarcate sunt
salile. Orarul nu spune cati studenti are o grupa, deci lucram cu marimi asumate, date ca
parametri: o semigrupa (implicit 15 oameni) si o grupa (30). Un curs de serie sau de an are
atatia oameni cate grupe are seria sau anul -- patru grupe fac cei ~120 ai unui curs.

Cum se numara
-------------
* **Oamenii ocupati** se numara pe grupe, nu pe activitati: o grupa cu doua laboratoare de
  semigrupa in acelasi interval e ocupata toata, o data, nu de doua ori. De aceea fiecare
  activitate isi imparte publicul pe grupele de baza, iar o grupa nu trece de marimea ei.
* **Optionalele si facultativele** nu au un public cunoscut: le urmeaza cine le-a ales. Se
  numara doar la cerere, fiecare cu marimea unei grupe (sau a unei semigrupe).
* **Saptamanile**: cu o saptamana anume, conteaza exact ce se tine atunci. Cu "toate"
  (sau "impare" / "pare"), aratam varful dintre saptamanile semestrului -- altfel doua
  laboratoare care alterneaza in aceeasi sala ar parea ca se tin deodata.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from orar.db.evenimente import cheie_an
from orar.db.models import Grupa, Ora, OraGrupa, Perioada, Sala
from orar.domain.grid import ORA_MAX, ORA_MIN, ZILE, se_tine
from orar.domain.weeks import Paritate, Saptamana

__all__ = ["METRICI", "Celula", "Filtre", "GrupaTinta", "Statistici", "calculeaza", "grupe_de_baza"]

#: Ce se poate arata in celulele grilei: (cheie, eticheta).
METRICI = (
    ("persoane", "Persoane cu ore"),
    ("libere", "Persoane libere"),
    ("procent", "% din public cu ore"),
    ("activitati", "Activități"),
    ("sali", "Săli ocupate"),
    ("pline", "Săli pline"),
)


@dataclass
class Filtre:
    """Tot ce se poate alege. Listele goale inseamna "fara restrictie"."""

    # publicul: ale cui ore se numara
    specializari: list[str] = field(default_factory=list)
    ani: list[str] = field(default_factory=list)  # "L2", "M1" -- vezi `cheie_an`
    serii: list[str] = field(default_factory=list)  # slug-uri de serie
    grupe: list[str] = field(default_factory=list)  # slug-uri de grupa
    # activitatile
    tipuri: list[str] = field(default_factory=list)  # curs / seminar / lab / proiect / alta
    optionale: bool = False
    zile: list[str] = field(default_factory=list)
    ora_de_la: int = ORA_MIN
    ora_pana_la: int = ORA_MAX
    profesor: str = ""
    materie: str = ""
    # salile
    feluri_sala: list[str] = field(default_factory=list)  # prefixul numelui: amf / l / s
    etaje: list[str] = field(default_factory=list)  # prima cifra din nume
    sali: list[str] = field(default_factory=list)  # slug-uri
    # saptamana: "" (toate), "SI", "SP" sau un numar de saptamana
    saptamana: str = ""
    # marimile asumate
    marime_semigrupa: int = 15
    marime_grupa: int = 30
    #: De la ce grad de umplere (%) o sala e "plina".
    prag_plin: int = 100


@dataclass
class GrupaTinta:
    """O grupa de baza, cu ce trebuie ca sa fie filtrata."""

    id: int
    slug: str
    nume: str
    specializare: str | None
    an: str | None
    serie: str | None  # slug-ul seriei
    serie_nume: str | None


@dataclass
class ActivitateInCelula:
    ora: Ora
    #: Cati oameni are, in total (nu doar din publicul ales).
    public: int
    #: Cati dintre ei sunt din publicul ales.
    din_tinta: int
    plina: bool


@dataclass
class Celula:
    zi: str
    ora: int
    persoane: int = 0
    activitati: int = 0
    sali: int = 0
    pline: int = 0
    detalii: list[ActivitateInCelula] = field(default_factory=list)


@dataclass
class Statistici:
    celule: dict[tuple[str, int], Celula]
    zile: list[str]
    ore: list[int]
    #: Cati oameni are publicul ales.
    populatie: int
    nr_grupe: int
    nr_sali: int
    nr_activitati: int

    def valoare(self, celula: Celula, metrica: str) -> int:
        if metrica == "libere":
            return max(0, self.populatie - celula.persoane)
        if metrica == "procent":
            return round(100 * celula.persoane / self.populatie) if self.populatie else 0
        return getattr(celula, metrica)

    def maxim(self, metrica: str) -> int:
        if metrica == "procent":
            return 100
        if metrica in ("persoane", "libere"):
            return self.populatie
        return max((self.valoare(c, metrica) for c in self.celule.values()), default=0)


def _clasa_tip(tip: str | None) -> str:
    tip = (tip or "").lower()
    for cheie in ("curs", "lab", "sem", "proiect"):
        if cheie in tip:
            return "seminar" if cheie == "sem" else cheie
    return "alta"


def grupe_de_baza(s: Session, an_universitar: str) -> list[GrupaTinta]:
    """Grupele propriu-zise ale unui an universitar, cu specializarea, anul si seria lor."""
    grupe = []
    for g in s.scalars(
        select(Grupa)
        .options(joinedload(Grupa.parinte))
        .where(Grupa.tip == "grupa", Grupa.an_universitar == an_universitar)
        .order_by(Grupa.nume)
    ):
        serie = g.parinte if g.parinte is not None and g.parinte.tip == "serie" else None
        grupe.append(
            GrupaTinta(
                id=g.id,
                slug=g.slug,
                nume=g.nume,
                specializare=g.specializare,
                an=cheie_an(g),
                serie=serie.slug if serie else None,
                serie_nume=serie.nume if serie else None,
            )
        )
    return grupe


def _in_tinta(g: GrupaTinta, f: Filtre) -> bool:
    return (
        (not f.specializari or g.specializare in f.specializari)
        and (not f.ani or g.an in f.ani)
        and (not f.serii or g.serie in f.serii)
        and (not f.grupe or g.slug in f.grupe)
    )


def _fel_si_etaj(sala: Sala) -> tuple[str, str]:
    """Prefixul numelui (`amf`, `l`, `s`) si etajul (prima cifra) ale unei sali."""
    litere = "".join(c for c in sala.nume.split(".")[0] if c.isalpha()).lower()
    etaj = next((c for c in sala.nume if c.isdigit()), "")
    return litere, etaj


#: Cate saptamani are un semestru: pentru "toate saptamanile" le luam pe rand.
SAPTAMANI_SEMESTRU = 14


def _saptamani(f: Filtre) -> list[Saptamana]:
    """Saptamanile pentru care se calculeaza; cu mai multe, se ia varful dintre ele.

    "Toate" inseamna fiecare saptamana a semestrului, pe rand -- nu doar "una impara si una
    para": un curs tinut de un profesor in saptamanile 1-7 si de altul in 8-14 nu umple
    sala de doua ori.
    """
    if f.saptamana.isdigit():
        numere = [int(f.saptamana)]
    else:
        numere = [
            n
            for n in range(1, SAPTAMANI_SEMESTRU + 1)
            if f.saptamana not in ("SI", "SP") or Paritate.din_numar(n).value == f.saptamana
        ]
    return [Saptamana(numar=n, paritate=Paritate.din_numar(n), exact=True) for n in numere]


def calculeaza(s: Session, perioada: Perioada, f: Filtre) -> Statistici:
    """Grila de statistici a orarului `perioada`, cu filtrele `f`."""
    grupe = grupe_de_baza(s, perioada.an_univ)
    tinta = {g.id for g in grupe if _in_tinta(g, f)}
    doar_public_intreg = not (f.serii or f.grupe)

    # arborele, ca sa stim ce grupe de baza are sub el un nod (serie, an)
    noduri = {
        g.id: g for g in s.scalars(select(Grupa).where(Grupa.an_universitar == perioada.an_univ))
    }
    copii: dict[int, list[int]] = {}
    for g in noduri.values():
        if g.parinte_id is not None:
            copii.setdefault(g.parinte_id, []).append(g.id)
    de_baza = {g.id for g in grupe}

    def grupele_de_sub(nod_id: int) -> list[int]:
        gasite, de_vizitat = [], [nod_id]
        while de_vizitat:
            curent = de_vizitat.pop()
            if curent in de_baza:
                gasite.append(curent)
            else:
                de_vizitat.extend(copii.get(curent, []))
        return gasite

    legaturi: dict[int, list[int]] = {}
    for ora_id, grupa_id in s.execute(select(OraGrupa.ora_id, OraGrupa.grupa_id)):
        legaturi.setdefault(ora_id, []).append(grupa_id)

    zile = [z for z in ZILE if not f.zile or z in f.zile]
    ore = list(range(max(ORA_MIN, f.ora_de_la), min(ORA_MAX, f.ora_pana_la)))

    # publicul fiecarei activitati care trece de filtre: {grupa de baza: oameni} + nealocati
    activitati: list[tuple[Ora, dict[int, int], int]] = []
    for o in s.scalars(
        select(Ora)
        .options(
            joinedload(Ora.grupa),
            joinedload(Ora.sala),
            joinedload(Ora.materie),
            joinedload(Ora.profesor),
        )
        .where(Ora.perioada_id == perioada.id)
    ):
        if o.zi_saptamana not in zile or o.grupa is None:
            continue
        if f.tipuri and _clasa_tip(o.tip_ora_materie) not in f.tipuri:
            continue
        if f.profesor and f.profesor.lower() not in (o.profesor.nume if o.profesor else "").lower():
            continue
        if (
            f.materie
            and f.materie.lower()
            not in (f"{o.materie.nume} {o.materie.denumire or ''}" if o.materie else "").lower()
        ):
            continue
        if f.feluri_sala or f.etaje or f.sali:
            if o.sala is None:
                continue
            fel, etaj = _fel_si_etaj(o.sala)
            if (
                (f.feluri_sala and fel not in f.feluri_sala)
                or (f.etaje and etaj not in f.etaje)
                or (f.sali and o.sala.slug not in f.sali)
            ):
                continue

        pe_grupe: dict[int, int] = {}
        nealocati = 0
        nod = o.grupa
        parte = f.marime_semigrupa if o.semigrupa else f.marime_grupa
        if nod.tip == "optional":
            if not f.optionale:
                continue
            # cine l-a ales nu se stie: marimea unei grupe, daca pachetul tine de public
            potrivit = (
                doar_public_intreg
                and (not f.specializari or nod.specializare in f.specializari)
                and (not f.ani or cheie_an(nod) in f.ani)
            )
            if not potrivit:
                continue
            nealocati = parte
        elif nod.tip == "semigrupa":
            if nod.parinte_id is not None:
                pe_grupe[nod.parinte_id] = f.marime_semigrupa
        else:
            for nod_id in (nod.id, *legaturi.get(o.id, [])):
                for g in grupele_de_sub(nod_id):
                    pe_grupe[g] = max(pe_grupe.get(g, 0), parte)
        activitati.append((o, pe_grupe, nealocati))

    celule = {(z, h): Celula(z, h) for z in zile for h in ore}
    for sapt in _saptamani(f):
        ocupare: dict[tuple[str, int], dict[int, int]] = {k: {} for k in celule}
        extra: dict[tuple[str, int], int] = dict.fromkeys(celule, 0)
        in_sali: dict[tuple[str, int], dict[int, int]] = {k: {} for k in celule}
        detalii: dict[tuple[str, int], list[ActivitateInCelula]] = {k: [] for k in celule}

        for o, pe_grupe, nealocati in activitati:
            if not se_tine(o, sapt):
                continue
            public = sum(pe_grupe.values()) + nealocati
            din_tinta = sum(n for g, n in pe_grupe.items() if g in tinta) + nealocati
            for h in range(
                o.ora_inceput.hour, o.ora_sfarsit.hour + (1 if o.ora_sfarsit.minute else 0)
            ):
                k = (o.zi_saptamana, h)
                if k not in celule:
                    continue
                for g, n in pe_grupe.items():
                    if g in tinta:
                        ocupare[k][g] = min(f.marime_grupa, ocupare[k].get(g, 0) + n)
                extra[k] += nealocati
                if din_tinta:
                    detalii[k].append(ActivitateInCelula(o, public, din_tinta, plina=False))
                if o.sala is not None and o.sala.tip == "fizica" and din_tinta:
                    in_sali[k][o.sala_id] = in_sali[k].get(o.sala_id, 0) + public

        capacitati = {x.id: x.nr_locuri for x in s.scalars(select(Sala)) if x.nr_locuri}
        for k, c in celule.items():
            persoane = sum(ocupare[k].values()) + extra[k]
            pline = {
                sala_id
                for sala_id, oameni in in_sali[k].items()
                if sala_id in capacitati and 100 * oameni >= f.prag_plin * capacitati[sala_id]
            }
            for d in detalii[k]:
                d.plina = d.ora.sala_id in pline
            # varful dintre saptamani: celula cu cei mai multi oameni isi pastreaza si detaliile
            if persoane >= c.persoane:
                c.detalii = detalii[k] if persoane > c.persoane or not c.detalii else c.detalii
            c.persoane = max(c.persoane, persoane)
            c.activitati = max(c.activitati, len(detalii[k]))
            c.sali = max(c.sali, len(in_sali[k]))
            c.pline = max(c.pline, len(pline))

    populatie = len(tinta) * f.marime_grupa
    for c in celule.values():
        c.persoane = min(c.persoane, populatie) if populatie else c.persoane
    return Statistici(
        celule=celule,
        zile=zile,
        ore=ore,
        populatie=populatie,
        nr_grupe=len(tinta),
        nr_sali=len(
            {o.sala_id for o, _, _ in activitati if o.sala is not None and o.sala.tip == "fizica"}
        ),
        nr_activitati=len(activitati),
    )
