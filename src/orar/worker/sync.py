"""Sincronizarea completa: pagina FMI -> captura -> segmentare -> OCR -> baza.

Ordinea si de ce
----------------
1. **Citim pagina** si aflam ce a publicat facultatea (`ingest/watcher.py`).
2. **Salvam ancorele** de saptamana. Sunt ieftine si independente de orar: chiar daca
   ingestul nu se mai face, paritatea afisata in interfata ramane corecta.
3. **Comparam** data publicata cu cea de la ultimul ingest reusit. Daca nu s-a schimbat
   nimic, ne oprim aici -- captura celor 100 de pagini dureaza ~6 minute si nu are rost.
4. Doar daca s-a schimbat: capturam, citim si incarcam.

`INGESTAT_LA` se scrie **dupa** ce ingestul a reusit, nu inainte. Daca pica la jumatate,
urmatoarea rulare reia sursa in loc sa o creada facuta.

Ingestul nu incarca peste datele vechi: sterge intai orele semestrului respectiv. Altfel
o activitate mutata ar ramane si pe locul vechi, iar `/sala` ar arata conflicte inventate.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from orar.db.models import AncoraSaptamana as AncoraDB
from orar.db.models import Ora, OraGrupa, Perioada, Sesizare
from orar.db.models import SursaOrar as SursaDB
from orar.ingest import watcher

log = logging.getLogger(__name__)

__all__ = [
    "RaportSincronizare",
    "sincronizeaza",
    "salveaza_ancore",
    "calendar_din_baza",
    "curata_orfanii",
]


@dataclass
class RaportSincronizare:
    verificat: bool = False
    schimbari: list[str] = field(default_factory=list)
    ingestate: list[str] = field(default_factory=list)
    ancore: int = 0
    #: Ce a spus verificarea cu orarul profesorilor.
    crosscheck: list[str] = field(default_factory=list)
    avertismente: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        linii = [
            f"pagina citita     : {'da' if self.verificat else 'nu'}",
            f"ancore salvate    : {self.ancore}",
            f"surse de reluat   : {len(self.schimbari)}",
        ]
        linii += [f"    {s}" for s in self.schimbari]
        if self.ingestate:
            linii.append(f"surse ingestate   : {len(self.ingestate)}")
            linii += [f"    {s}" for s in self.ingestate]
        if self.crosscheck:
            linii.append("verificare incrucisata:")
            linii += [f"    {s}" for s in self.crosscheck]
        linii += [f"  ! {a}" for a in self.avertismente]
        return "\n".join(linii)


def _nimic(_mesaj: str) -> None:
    """Progresul implicit: nu-l asculta nimeni."""


def sincronizeaza(
    s: Session,
    *,
    an_universitar: str = "2025-2026",
    semestru: int | None = None,
    director: Path = Path("data/screenshots"),
    forteaza: bool = False,
    doar_verifica: bool = False,
    fara_crosscheck: bool = False,
    html: str | None = None,
) -> RaportSincronizare:
    """Verifica pagina FMI si, daca e cazul, reia ingestul.

    `semestru=None` inseamna semestrul pe care facultatea il tine la zi (cel cu cea mai
    recenta actualizare), ca sa nu reingestam din greseala orarul vechi.
    """
    rap = RaportSincronizare()
    stare = watcher.parseaza(html if html is not None else watcher.descarca())
    rap.verificat = True
    rap.avertismente += stare.avertismente

    rap.ancore = salveaza_ancore(s, stare.ancore, an_universitar=an_universitar)

    tinta = semestru if semestru is not None else stare.semestru_curent
    if tinta is None:
        rap.avertismente.append("nu am putut deduce semestrul curent din pagina")
        return rap

    surse = [x for x in stare.surse if x.semestru == tinta and x.fel == "grupe"]
    if not surse:
        rap.avertismente.append(f"pagina nu are 'Orarul grupelor' pentru semestrul {tinta}")
        return rap

    cunoscute = _ingestate_pana_acum(s, an_universitar)
    for sursa in surse:
        rand = _upsert_sursa(s, sursa, an_universitar)
        rand.verificat_la = datetime.now()
    s.flush()

    schimbari = watcher.compara(stare, cunoscute)
    schimbari = [c for c in schimbari if c.sursa.semestru == tinta and c.sursa.fel == "grupe"]
    if forteaza and not schimbari:
        schimbari = [watcher.Schimbare(surse[0], "reluare fortata")]
    rap.schimbari = [str(c) for c in schimbari]

    if doar_verifica or not schimbari:
        return rap

    # Verificarea cu orarul profesorilor e obligatorie: fara ea, numele raman prescurtate
    # si profesorii nerecunoscuti n-au cu ce fi completati. Daca pagina nu-l are, nu
    # reluam deloc -- orarul de acum ramane cum e.
    if not fara_crosscheck and stare.sursa(tinta, "profesori") is None:
        rap.avertismente.append(
            f"pagina nu are 'Orarul profesorilor' pentru semestrul {tinta}; nu reiau orarul fara el"
        )
        return rap

    for c in schimbari:
        _ingesteaza(s, c.sursa, an_universitar=an_universitar, director=director, rap=rap)

    if rap.ingestate and not fara_crosscheck:
        try:
            _crosscheck(s, stare, tinta, an_universitar=an_universitar, director=director, rap=rap)
        except EroareCrosscheck as e:
            # tot sau nimic: fara a doua sursa, nici orarul grupelor nu se inlocuieste
            s.rollback()
            rap.ingestate = []
            rap.avertismente.append(f"{e}; orarul a ramas cel de dinainte")
    return rap


class EroareCrosscheck(RuntimeError):
    """Orarul profesorilor nu a putut fi citit, deci verificarea incrucisata nu s-a facut."""


def _crosscheck(
    s: Session,
    stare: watcher.StarePublicata,
    semestru: int,
    *,
    an_universitar: str,
    director: Path,
    rap: RaportSincronizare,
    progres: Callable[[str], None] = _nimic,
) -> None:
    """A doua sursa: orarul profesorilor, pentru confirmare si nume intregi.

    Ruleaza dupa ingest, nu inaintea lui: are nevoie de baza deja incarcata ca sa aiba ce
    confirma. E obligatorie: daca orarul profesorilor lipseste sau nu se poate captura,
    arunca `EroareCrosscheck`, iar apelantul renunta la tot ingestul.
    """
    from orar.ingest.capture import EroareCaptura, OptiuniCaptura, captureaza_orar
    from orar.ingest.crosscheck import (
        citeste_orarul_profesorilor,
        completeaza_profesorii,
        extinde_numele,
        salveaza_index,
        verifica,
    )
    from orar.ingest.lexicon import Lexicon
    from orar.ingest.ocr import RapidOCR

    sursa = stare.sursa(semestru, "profesori")
    if sursa is None:
        raise EroareCrosscheck("lipseste orarul profesorilor")

    tinta = director / f"sem{semestru}-profesori"
    progres("capturez orarul profesorilor din Drive (câteva minute)")
    try:
        rez = captureaza_orar(sursa.url, OptiuniCaptura(director=tinta))
    except EroareCaptura as e:
        raise EroareCrosscheck(f"captura orarului profesorilor: {e}") from e
    rap.avertismente += rez.avertismente

    progres("citesc orarul profesorilor și îl compar cu cel al grupelor")
    index = citeste_orarul_profesorilor(tinta, RapidOCR(), Lexicon.din_baza(s))
    raport = verifica(s, index)
    completate = completeaza_profesorii(s, index)
    extinse, ambigue = extinde_numele(s, index)
    if not index.nume:
        raise EroareCrosscheck("orarul profesorilor nu are nicio pagina citibila")
    # dupa `extinde_numele`: randurile cu numele intreg exista deja, doar le marcam
    salveaza_index(s, index, an_universitar=an_universitar, semestru=semestru)

    rand = _upsert_sursa(s, sursa, an_universitar)
    rand.ingestat_la = sursa.actualizat
    s.flush()

    rap.crosscheck = [
        f"{raport.confirmate} activitati confirmate de a doua sursa "
        f"({raport.acoperire * 100:.0f}%), {len(raport.divergente)} divergente",
        f"{len(completate)} campuri nesigure completate, {len(extinse)} nume intregite",
    ]
    if ambigue:
        rap.crosscheck.append(f"{len(ambigue)} prescurtari ambigue, lasate cum sunt")


def salveaza_ancore(s: Session, ancore, *, an_universitar: str, semestru: int = 2) -> int:  # noqa: ANN001
    """Scrie ancorele publicate, inlocuindu-le pe cele cu aceeasi saptamana."""
    n = 0
    for a in ancore:
        rand = s.scalar(
            select(AncoraDB).where(
                AncoraDB.an_univ == an_universitar,
                AncoraDB.semestru == semestru,
                AncoraDB.inceput == a.inceput,
            )
        )
        if rand is None:
            rand = AncoraDB(an_univ=an_universitar, semestru=semestru, inceput=a.inceput)
            s.add(rand)
        rand.numar = a.numar
        rand.paritate = str(a.paritate)
        n += 1
    s.flush()
    return n


def calendar_din_baza(s: Session, *, an_universitar: str | None = None):  # noqa: ANN001
    """`CalendarAcademic` construit din ancorele salvate; None daca nu exista niciuna."""
    from orar.domain.weeks import AncoraSaptamana, CalendarAcademic, Paritate

    q = select(AncoraDB).order_by(AncoraDB.inceput)
    if an_universitar:
        q = q.where(AncoraDB.an_univ == an_universitar)
    randuri = list(s.scalars(q))
    if not randuri:
        return None
    return CalendarAcademic(
        ancore=[
            AncoraSaptamana(inceput=r.inceput, numar=r.numar, paritate=Paritate(r.paritate))
            for r in randuri
        ]
    )


def _ingestate_pana_acum(s: Session, an_universitar: str) -> dict[tuple[int, str], datetime | None]:
    return {
        (r.semestru, r.fel): r.ingestat_la
        for r in s.scalars(select(SursaDB).where(SursaDB.an_univ == an_universitar))
    }


def _upsert_sursa(s: Session, sursa: watcher.SursaOrar, an_universitar: str) -> SursaDB:
    rand = s.scalar(
        select(SursaDB).where(
            SursaDB.an_univ == an_universitar,
            SursaDB.semestru == sursa.semestru,
            SursaDB.fel == sursa.fel,
        )
    )
    if rand is None:
        rand = SursaDB(an_univ=an_universitar, semestru=sursa.semestru, fel=sursa.fel)
        s.add(rand)
    rand.url = sursa.url
    rand.actualizat = sursa.actualizat
    return rand


def _ingesteaza(
    s: Session,
    sursa: watcher.SursaOrar,
    *,
    an_universitar: str,
    director: Path,
    rap: RaportSincronizare,
    progres: Callable[[str], None] = _nimic,
) -> None:
    """Captureaza, citeste si incarca o sursa. `progres` primeste etapa curenta, in cuvinte
    -- ingestul dureaza minute bune, iar cine l-a pornit din panoul de admin vrea sa stie
    unde a ajuns."""
    from orar.db.corectii import aplica_dupa_ingest
    from orar.db.versiuni import arhiveaza_semestrul
    from orar.ingest.capacities import incarca_capacitati
    from orar.ingest.capture import EroareCaptura, OptiuniCaptura, captureaza_orar
    from orar.ingest.consolidate import consolideaza
    from orar.ingest.lexicon import Lexicon, imbina
    from orar.ingest.load import incarca_pagini
    from orar.ingest.ocr import RapidOCR, citeste_fisier
    from orar.ingest.segment import EroareSegmentare

    tinta = director / f"sem{sursa.semestru}-{sursa.fel}"
    progres("capturez paginile orarului din Drive (câteva minute)")
    try:
        rez = captureaza_orar(sursa.url, OptiuniCaptura(director=tinta))
    except EroareCaptura as e:
        rap.avertismente.append(f"captura pentru {sursa.fel} sem {sursa.semestru}: {e}")
        return
    rap.avertismente += rez.avertismente

    # Vocabularul de pornire e cel din baza; la prima rulare e gol si totul ajunge in
    # coada de verificare, ceea ce e onest -- nu avem inca de unde sti termenii corecti.
    lexicon = Lexicon.din_baza(s)
    motor = RapidOCR()

    # Salile se citesc intai din tabelul lor, de pe primele pagini: e lista oficiala.
    progres("citesc lista de săli de pe primele pagini")
    sali, pagina_sali = _tabelul_salilor(rez.pagini, motor)
    if sali:
        lexicon = imbina(lexicon, Lexicon(sali=[x.nume for x in sali]))

    pagini = []
    for i, pagina in enumerate(rez.pagini, 1):
        if i == 1 or i % 10 == 0:
            progres(f"citesc paginile: {i} din {len(rez.pagini)}")
        try:
            citita = citeste_fisier(pagina.cale, motor, lexicon)
        except EroareSegmentare:
            continue  # coperta / tabelul salilor, citit deja mai sus
        # Calea **relativa la radacina capturilor**, nu doar numele: capturam in
        # `screenshots/sem2-grupe/`, si numai numele fisierului n-ar mai duce inapoi la
        # imagine, deci coada de verificare ar ramane fara decupaje.
        pagini.append(
            {
                **citita.ca_json(cu_provenienta=True),
                "_source": str(pagina.cale.relative_to(director)),
            }
        )

    if not pagini:
        rap.avertismente.append(f"nicio pagina citibila in {tinta}")
        return

    # Orarul de pana acum devine o versiune anterioara, vizibila pe /grupa/{id}. In aceeasi
    # tranzactie cu stergerea: daca ingestul pica, dispar amandoua.
    anterior = s.scalar(
        select(SursaDB).where(
            SursaDB.an_univ == an_universitar,
            SursaDB.semestru == sursa.semestru,
            SursaDB.fel == sursa.fel,
        )
    )
    arhivata = arhiveaza_semestrul(
        s,
        an_universitar=an_universitar,
        semestru=sursa.semestru,
        publicat=anterior.ingestat_la if anterior else None,
        inlocuit_de=sursa.actualizat,
    )
    if arhivata:
        rap.ingestate.append(f"versiunea anterioara pastrata: {arhivata.nr_ore} ore")

    progres("încarc orarul în bază")
    _sterge_semestrul(s, an_universitar=an_universitar, semestru=sursa.semestru)
    raport = incarca_pagini(s, pagini, an_universitar=an_universitar, semestru=sursa.semestru)
    consolidare = consolideaza(s, an_universitar=an_universitar)

    # Ce a schimbat un admin de mana (o sala, un profesor, o activitate adaugata) se pune la
    # loc peste orele proaspat incarcate. Inaintea curatarii orfanilor: corectiile pot avea
    # nevoie de profesori si sali pe care orarul nou nu le mai pomeneste.
    corectii = aplica_dupa_ingest(s, an_universitar=an_universitar, semestru=sursa.semestru)
    if corectii.aplicate:
        rap.ingestate.append(f"corectii manuale puse la loc: {corectii.aplicate}")
    rap.avertismente += [
        f"corectie manuala fara activitate in orarul nou -- {c}" for c in corectii.neaplicate
    ]

    orfani = curata_orfanii(s)
    if orfani:
        rap.avertismente.append(f"entitati ramase fara ore, sterse: {orfani}")
    disparute = curata_grupele_disparute(s, an_universitar=an_universitar, pastrate=raport.noduri)
    if disparute:
        rap.avertismente.append(f"formatiuni/pachete disparute din orar, sterse: {disparute}")

    # Dupa curatarea orfanilor: salile din tabel raman in baza chiar si fara nicio ora.
    if sali:
        r = incarca_capacitati(s, sali)
        rap.ingestate.append(
            f"sali din {pagina_sali}: {r.completate} cu numar de locuri"
            + (f", {len(r.adaugate)} fara ore adaugate" if r.adaugate else "")
        )

    rand = _upsert_sursa(s, sursa, an_universitar)
    rand.ingestat_la = sursa.actualizat
    s.flush()
    rap.ingestate.append(
        f"sem {sursa.semestru} / {sursa.fel}: {raport.pagini} pagini, "
        f"{raport.ore} ore -> {raport.ore - consolidare.ore_sterse} dupa consolidare"
    )


#: In cate pagini de la inceputul documentului cautam tabelul salilor. E pe pagina 2; lasam
#: loc pentru o coperta sau o pagina de anunturi in plus.
PAGINI_INTRODUCERE = 5


def _tabelul_salilor(pagini, motor) -> tuple[list, str]:  # noqa: ANN001
    """Lista oficiala a salilor, de pe primele pagini ale documentului: (randuri, pagina).

    Paginile de la inceput care nu sunt orare nu sunt gunoi: una dintre ele tine tabelul cu
    salile facultatii si numarul lor de locuri. O citim **inaintea** orarului, fiindca e si
    vocabularul cu care se recunosc salile din celule -- inclusiv pe o baza goala si cand
    apare o sala noua. Daca nu-l gasim, ingestul merge mai departe cu salile din baza.
    """
    from orar.ingest.capacities import citeste_capacitati
    from orar.ingest.segment import EroareSegmentare, segmenteaza_fisier

    for pagina in pagini[:PAGINI_INTRODUCERE]:
        try:
            segmenteaza_fisier(pagina.cale)
            continue  # e o pagina de orar
        except EroareSegmentare:
            randuri = citeste_capacitati(pagina.cale, motor)
        if randuri:
            return randuri, pagina.cale.name
    return [], ""


def _sterge_semestrul(s: Session, *, an_universitar: str, semestru: int) -> None:
    """Sterge orele semestrului, ca reingestul sa nu lase in urma activitati mutate."""
    perioade = list(
        s.scalars(
            select(Perioada.id).where(
                Perioada.an_univ == an_universitar, Perioada.semestru == semestru
            )
        )
    )
    if perioade:
        # Legaturile intai, explicit: ON DELETE CASCADE merge in SQLite doar cu
        # `PRAGMA foreign_keys=ON`, iar fara el ar ramane legaturi orfane de care s-ar lovi
        # orele reincarcate (SQLite le refoloseste id-urile).
        ore = select(Ora.id).where(Ora.perioada_id.in_(perioade))
        s.execute(delete(OraGrupa).where(OraGrupa.ora_id.in_(ore)))
        # Sesizarile raman, cu descrierea lor in cuvinte; doar legatura spre rand dispare.
        s.execute(update(Sesizare).where(Sesizare.ora_id.in_(ore)).values(ora_id=None))
        s.execute(delete(Ora).where(Ora.perioada_id.in_(perioade)))
        s.flush()


def curata_grupele_disparute(s: Session, *, an_universitar: str, pastrate: set[str]) -> int:
    """Sterge nodurile GRUPA pe care orarul nu le mai contine: o serie desfiintata, o
    specializare care nu mai are an, un pachet redenumit. Fara asta, ele ar ramane listate
    pe prima pagina si in cautare, cu un orar gol.

    `pastrate` = nodurile publicarii tocmai incarcate (`Raport.noduri`); pe acelea nu le
    atingem, chiar daca n-au ore proprii -- o grupa ale carei cursuri au urcat toate la serie
    e tot o grupa. Dintre celelalte, stergem doar ce nu mai foloseste nimic: nici orele
    (altui semestru), nici arhiva de versiuni, nici grupa vreunui cont, nici un copil. De jos
    in sus, ca o serie sa plece abia dupa grupele ei.
    """
    from sqlalchemy import union

    from orar.db.models import Grupa, OraArhivata, OraGrupaArhivata, User

    total = 0
    while True:
        folosite = union(
            select(Ora.grupa_id),
            select(OraGrupa.grupa_id),
            select(OraArhivata.grupa_id),
            select(OraGrupaArhivata.grupa_id),
            select(User.grupa_id).where(User.grupa_id.is_not(None)),
            select(Grupa.parinte_id).where(Grupa.parinte_id.is_not(None)),
        )
        rezultat = s.execute(
            delete(Grupa).where(
                Grupa.an_universitar == an_universitar,
                Grupa.slug.not_in(pastrate),
                Grupa.id.not_in(folosite),
            )
        )
        if not rezultat.rowcount:
            break
        total += rezultat.rowcount
    s.flush()
    return total


def curata_orfanii(s: Session) -> dict[str, int]:
    """Sterge profesorii, materiile si salile la care nu mai trimite nicio ora.

    Ingestul insereaza si valorile pe care nu le-a putut confirma -- altfel ora ar ramane
    fara profesor, iar coada de verificare n-ar avea ce arata. Dupa o reluare, cele care nu
    s-au mai citit la fel raman agatate de nimic. Lasate acolo, tabela creste la fiecare
    rulare si duce si numerele din interfata in eroare ("205 profesori" pentru o facultate
    care are ~197).

    Cele folosite doar de versiunile anterioare raman: fara ele, orarul vechi ar pierde
    profesorul sau sala unei activitati.
    """
    from orar.db.models import Eveniment, Materie, OraArhivata, Profesor, Sala

    sterse: dict[str, int] = {}
    for model, camp, camp_arhiva in (
        (Profesor, Ora.profesor_id, OraArhivata.profesor_id),
        (Materie, Ora.materie_id, OraArhivata.materie_id),
        (Sala, Ora.sala_id, OraArhivata.sala_id),
    ):
        folosite = (
            select(camp)
            .where(camp.is_not(None))
            .union(select(camp_arhiva).where(camp_arhiva.is_not(None)))
        )
        conditii = [model.id.not_in(folosite)]
        if model is Sala:
            # o sala rezervata pentru un eveniment ramane, chiar fara ore in orar
            conditii.append(
                Sala.id.not_in(select(Eveniment.sala_id).where(Eveniment.sala_id.is_not(None)))
            )
        if model is Profesor:
            # cei din orarul profesorilor raman: sunt lista din care se alege la editare
            conditii.append(Profesor.din_orar.is_(False))
        rezultat = s.execute(delete(model).where(*conditii))
        if rezultat.rowcount:
            sterse[model.__tablename__] = rezultat.rowcount
    s.flush()
    return sterse
