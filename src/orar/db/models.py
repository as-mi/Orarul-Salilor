"""Schema bazei de date.

Implementeaza schema ceruta (PROFESOR, SALA, GRUPA, PERIOADA, MATERIE, ORA, USER) plus
trei adaugiri agreate:

  GRUPA.TIP          nivelul nodului in arbore -- PARINTE da structura, nu si nivelul
  ORA_GRUPA          jonctiune many-to-many, pentru orele partajate de mai multe grupe
                     (optionale, facultative, limbi straine)
  ORA.confidence /   provenienta si increderea OCR-ului, pentru coada de review (Etapa 5)
  ORA.sursa_pagina

USER tine conturile (cu rol, grupa si preferintele orarului propriu); fara cont, aceleasi
preferinte stau in cookie. Adminul principal vine din mediu, nu din tabela.

Numele de tabele si de coloane respecta specificatia (majuscule), dar atributele Python
sunt in snake_case ca sa ramana idiomatice.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

if TYPE_CHECKING:
    pass


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Nomenclatoare
# ---------------------------------------------------------------------------


class Profesor(Base):
    __tablename__ = "PROFESOR"

    id: Mapped[int] = mapped_column("ID_PROFESOR", Integer, primary_key=True)
    nume: Mapped[str] = mapped_column("NUME", String(120), nullable=False, unique=True)
    titlu: Mapped[str | None] = mapped_column("TITLU", String(40))
    #: Slug pentru URL-uri (/profesor/{slug}).
    slug: Mapped[str] = mapped_column("SLUG", String(140), nullable=False, unique=True)
    #: Numele e titlul unei pagini din orarul profesorilor: un om real, cu numele intreg --
    #: lista din care se alege un profesor la editare. Celelalte randuri sunt ce s-a citit
    #: din celulele orarului de grupe (prescurtari, mai multi oameni intr-un camp).
    din_orar: Mapped[bool] = mapped_column(
        "DIN_ORAR", Boolean, nullable=False, default=False, server_default="0"
    )

    ore: Mapped[list[Ora]] = relationship(back_populates="profesor")

    def __repr__(self) -> str:
        return f"<Profesor {self.nume!r}>"


class SlotProfesor(Base):
    """O activitate asa cum apare in **orarul profesorilor**: cine, cand, unde, ce.

    A doua sursa, pastrata ca sa putem potrivi profesorii cu activitatile din orarul
    grupelor si dupa ingest -- in coada de verificare, cand un om repara un profesor. O
    activitate tinuta de mai multi apare o data pentru fiecare; una impartita pe saptamani
    (`sapt. 1-7` un profesor, `8-14` altul) are `SAPTAMANI` diferite.
    """

    __tablename__ = "ORAR_PROFESOR"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    an_univ: Mapped[str] = mapped_column("AN_UNIV", String(9), nullable=False)
    semestru: Mapped[int] = mapped_column("SEMESTRU", Integer, nullable=False)
    #: Numele intreg, din titlul paginii.
    profesor: Mapped[str] = mapped_column("PROFESOR", String(120), nullable=False)
    zi: Mapped[str] = mapped_column("ZI", String(10), nullable=False)
    ora_inceput: Mapped[int] = mapped_column("ORA_INCEPUT", Integer, nullable=False)
    ora_sfarsit: Mapped[int] = mapped_column("ORA_SFARSIT", Integer, nullable=False)
    #: Slug-ul salii si materia cu litere mici: cheia comuna cu orarul grupelor.
    sala: Mapped[str] = mapped_column("SALA", String(90), nullable=False, default="")
    materie: Mapped[str] = mapped_column("MATERIE", String(200), nullable=False, default="")
    frecventa: Mapped[str | None] = mapped_column("FRECVENTA", String(4))
    saptamani: Mapped[str | None] = mapped_column("SAPTAMANI", String(40))

    __table_args__ = (Index("ix_orar_profesor_slot", "AN_UNIV", "SEMESTRU", "ZI", "ORA_INCEPUT"),)


class Sala(Base):
    __tablename__ = "SALA"

    id: Mapped[int] = mapped_column("ID_SALA", Integer, primary_key=True)
    nume: Mapped[str] = mapped_column("NUME", String(80), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column("SLUG", String(90), nullable=False, unique=True)
    #: fizica | externa | virtuala -- vezi domain.rooms.TipSala
    tip: Mapped[str] = mapped_column("TIP", String(16), nullable=False, default="fizica")
    #: Numarul de locuri, din tabelul de pe pagina 2 a orarului. NULL cand sala nu apare
    #: acolo (salile externe) sau cand sursa insasi scrie "?" (L.414 Robotica).
    nr_locuri: Mapped[int | None] = mapped_column("NR_LOCURI", Integer)

    ore: Mapped[list[Ora]] = relationship(back_populates="sala")

    __table_args__ = (
        CheckConstraint("TIP IN ('fizica','externa','virtuala')", name="ck_sala_tip"),
    )

    def __repr__(self) -> str:
        return f"<Sala {self.nume!r}>"


class Grupa(Base):
    """Nod in arborele Specializare -> Serie -> Grupa -> Semigrupa.

    `parinte` e cheia self-referentiala care tine ierarhia. Paginile de optionale sunt
    tot noduri aici (tip='optional'), dar se leaga de grupele-tinta prin ORA_GRUPA.
    """

    __tablename__ = "GRUPA"

    id: Mapped[int] = mapped_column("ID_GRUPA", Integer, primary_key=True)
    parinte_id: Mapped[int | None] = mapped_column(
        "PARINTE", ForeignKey("GRUPA.ID_GRUPA", ondelete="SET NULL"), index=True
    )
    nume: Mapped[str] = mapped_column("NUME", String(160), nullable=False)
    an_universitar: Mapped[str] = mapped_column("AN_UNIVERSITAR", String(12), nullable=False)

    # --- adaugire fata de specificatie ---
    #: specializare | serie | grupa | semigrupa | optional -- vezi domain.hierarchy.Nivel
    tip: Mapped[str] = mapped_column("TIP", String(16), nullable=False, default="grupa")
    slug: Mapped[str] = mapped_column("SLUG", String(180), nullable=False)
    #: Codul specializarii (INFO, MATE, CTI...), pentru filtrare rapida.
    specializare: Mapped[str | None] = mapped_column("SPECIALIZARE", String(16), index=True)
    an_studiu: Mapped[int | None] = mapped_column("AN_STUDIU", Integer)

    parinte: Mapped[Grupa | None] = relationship(back_populates="copii", remote_side="Grupa.id")
    copii: Mapped[list[Grupa]] = relationship(
        back_populates="parinte", cascade="save-update, merge"
    )
    ore: Mapped[list[Ora]] = relationship(back_populates="grupa")

    __table_args__ = (
        UniqueConstraint("SLUG", "AN_UNIVERSITAR", name="uq_grupa_slug_an"),
        CheckConstraint(
            "TIP IN ('specializare','serie','grupa','semigrupa','optional')",
            name="ck_grupa_tip",
        ),
        Index("ix_grupa_tip_an", "TIP", "AN_UNIVERSITAR"),
    )

    def __repr__(self) -> str:
        return f"<Grupa {self.nume!r} ({self.tip})>"


class Perioada(Base):
    __tablename__ = "PERIOADA"

    id: Mapped[int] = mapped_column("ID_PERIOADA", Integer, primary_key=True)
    semestru: Mapped[int] = mapped_column("SEMESTRU", Integer, nullable=False)
    an_univ: Mapped[str] = mapped_column("AN_UNIV", String(12), nullable=False)
    #: Orarul pe care il vede cine intra pe sit fara sa aleaga altul. Il alege un admin;
    #: cel mult o perioada e implicita (vezi `db.orare`).
    implicita: Mapped[bool] = mapped_column(
        "IMPLICITA", Boolean, nullable=False, default=False, server_default="0"
    )

    ore: Mapped[list[Ora]] = relationship(back_populates="perioada")

    __table_args__ = (
        UniqueConstraint("SEMESTRU", "AN_UNIV", name="uq_perioada"),
        CheckConstraint("SEMESTRU IN (1,2)", name="ck_perioada_semestru"),
    )

    def __repr__(self) -> str:
        return f"<Perioada sem{self.semestru} {self.an_univ}>"


class Materie(Base):
    """Disciplina.

    Coloanele de plan de invatamant (credite, tip, forma de evaluare, numar de ore) NU se
    pot extrage din orar -- vin din "Planurile de invatamant" (Etapa 8). Raman NULL pana
    atunci; nicio ruta din MVP nu depinde de ele.
    """

    __tablename__ = "MATERIE"

    id: Mapped[int] = mapped_column("ID_MATERIE", Integer, primary_key=True)
    nume: Mapped[str] = mapped_column("NUME", String(160), nullable=False)
    slug: Mapped[str] = mapped_column("SLUG", String(180), nullable=False, unique=True)

    #: Denumirea intreaga din planul de invatamant ("Structuri de date"). `NUME` ramane
    #: abrevierea din orar, care e cheia sub care apare disciplina peste tot.
    denumire: Mapped[str | None] = mapped_column("DENUMIRE", String(200))
    credite: Mapped[int | None] = mapped_column("CREDITE", Integer)
    tip_materie: Mapped[str | None] = mapped_column("TIP_MATERIE", String(40))
    forma_evaluare: Mapped[str | None] = mapped_column("FORMA_EVALUARE", String(40))
    tip_disciplina: Mapped[str | None] = mapped_column("TIP_DISCIPLINA", String(40))
    an: Mapped[int | None] = mapped_column("AN", Integer)
    semestru: Mapped[int | None] = mapped_column("SEMESTRU", Integer)
    nr_ore_c: Mapped[int | None] = mapped_column("NR_ORE_C", Integer)
    nr_ore_s: Mapped[int | None] = mapped_column("NR_ORE_S", Integer)
    nr_ore_l: Mapped[int | None] = mapped_column("NR_ORE_L", Integer)
    nr_ore_p: Mapped[int | None] = mapped_column("NR_ORE_P", Integer)

    ore: Mapped[list[Ora]] = relationship(back_populates="materie")

    def __repr__(self) -> str:
        return f"<Materie {self.nume!r}>"


# ---------------------------------------------------------------------------
# Tabela centrala
# ---------------------------------------------------------------------------

ZILE = ("Luni", "Marti", "Miercuri", "Joi", "Vineri", "Sambata", "Duminica")


class Ora(Base):
    """O activitate din orar: cine, ce, unde, cand, cu ce grupa."""

    __tablename__ = "ORA"

    id: Mapped[int] = mapped_column("ID_ORA", Integer, primary_key=True)

    profesor_id: Mapped[int | None] = mapped_column(
        "ID_PROFESOR", ForeignKey("PROFESOR.ID_PROFESOR"), index=True
    )
    materie_id: Mapped[int | None] = mapped_column(
        "ID_MATERIE", ForeignKey("MATERIE.ID_MATERIE"), index=True
    )
    sala_id: Mapped[int | None] = mapped_column("ID_SALA", ForeignKey("SALA.ID_SALA"), index=True)
    #: Grupa-proprietar (pagina din care provine ora). Partajarea trece prin ORA_GRUPA.
    grupa_id: Mapped[int] = mapped_column(
        "ID_GRUPA", ForeignKey("GRUPA.ID_GRUPA"), nullable=False, index=True
    )
    perioada_id: Mapped[int] = mapped_column(
        "ID_PERIOADA", ForeignKey("PERIOADA.ID_PERIOADA"), nullable=False, index=True
    )

    tip_ora_materie: Mapped[str | None] = mapped_column("TIP_ORA_MATERIE", String(24))
    ora_inceput: Mapped[time] = mapped_column("ORA_INCEPUT", Time, nullable=False)
    ora_sfarsit: Mapped[time] = mapped_column("ORA_SFARSIT", Time, nullable=False)
    zi_saptamana: Mapped[str] = mapped_column("ZI_SAPTAMANA", String(12), nullable=False)

    # --- specifice orarului FMI ---
    #: "SI" (saptamana impara) | "SP" (para) | NULL (in fiecare saptamana)
    frecventa: Mapped[str | None] = mapped_column("FRECVENTA", String(4))
    #: Textul brut al intervalului de saptamani, ex. "sapt 1-7".
    saptamani: Mapped[str | None] = mapped_column("SAPTAMANI", String(80))
    #: "Gr_1".."Gr_4" cand activitatea e doar pentru o semigrupa.
    semigrupa: Mapped[str | None] = mapped_column("SEMIGRUPA", String(8))

    # --- provenienta (adaugire) ---
    sursa_pagina: Mapped[str | None] = mapped_column("SURSA_PAGINA", String(60))
    confidence: Mapped[float | None] = mapped_column("CONFIDENCE")
    #: "x0,y0,x1,y1" in imaginea `SURSA_PAGINA`. Fara el, coada de verificare ar arata
    #: valorile propuse fara nimic cu care sa le compari -- adica ar cere sa ai incredere
    #: exact acolo unde am spus ca nu avem.
    sursa_bbox: Mapped[str | None] = mapped_column("SURSA_BBOX", String(40))
    #: Campurile pe care lexiconul nu le-a putut confirma, separate prin virgula.
    campuri_nesigure: Mapped[str | None] = mapped_column("CAMPURI_NESIGURE", String(60))
    #: Corectia manuala care a adaugat sau a modificat activitatea (vezi `Corectie`). NULL
    #: pentru ce vine neatins din orarul publicat.
    corectie_id: Mapped[int | None] = mapped_column(
        "ID_CORECTIE", ForeignKey("CORECTIE.ID_CORECTIE", ondelete="SET NULL"), index=True
    )

    profesor: Mapped[Profesor | None] = relationship(back_populates="ore")
    materie: Mapped[Materie | None] = relationship(back_populates="ore")
    sala: Mapped[Sala | None] = relationship(back_populates="ore")
    grupa: Mapped[Grupa] = relationship(back_populates="ore")
    perioada: Mapped[Perioada] = relationship(back_populates="ore")
    grupe_partajate: Mapped[list[Grupa]] = relationship(
        secondary="ORA_GRUPA", viewonly=False, backref="ore_partajate"
    )

    __table_args__ = (
        CheckConstraint(
            "ZI_SAPTAMANA IN ('Luni','Marti','Miercuri','Joi','Vineri','Sambata','Duminica')",
            name="ck_ora_zi",
        ),
        CheckConstraint("ORA_INCEPUT < ORA_SFARSIT", name="ck_ora_interval"),
        CheckConstraint("FRECVENTA IS NULL OR FRECVENTA IN ('SI','SP')", name="ck_ora_frecventa"),
        # Cele doua interogari fierbinti: orarul unei grupe si ocuparea unei sali.
        Index("ix_ora_grupa_zi", "ID_GRUPA", "ZI_SAPTAMANA", "ORA_INCEPUT"),
        Index("ix_ora_sala_zi", "ID_SALA", "ZI_SAPTAMANA", "ORA_INCEPUT"),
    )

    def __repr__(self) -> str:
        return f"<Ora {self.zi_saptamana} {self.ora_inceput:%H:%M}-{self.ora_sfarsit:%H:%M}>"


class OraGrupa(Base):
    """Jonctiune: o ora poate fi partajata de mai multe grupe.

    Necesara pentru optionale/facultative/limbi straine, unde o singura activitate apare
    in orarul mai multor grupe sau serii.
    """

    __tablename__ = "ORA_GRUPA"

    ora_id: Mapped[int] = mapped_column(
        "ID_ORA", ForeignKey("ORA.ID_ORA", ondelete="CASCADE"), primary_key=True
    )
    grupa_id: Mapped[int] = mapped_column(
        "ID_GRUPA", ForeignKey("GRUPA.ID_GRUPA", ondelete="CASCADE"), primary_key=True
    )


# ---------------------------------------------------------------------------
# Utilizatori
# ---------------------------------------------------------------------------


#: Rolurile unui cont. `student` e cel implicit; celelalte le da un admin, din /admin.
ROLURI = ("student", "voluntar", "profesor", "admin")


class User(Base):
    """Un cont.

    Orarul e public si fara cont. Contul tine **orarul tau**: grupa la care esti si ce vrei
    sa vezi din ea -- semigrupa si optionalele/facultativele alese -- ca sa le gasesti la fel
    de pe orice dispozitiv. (Fara cont, aceleasi alegeri se tin in cookie, pe pagina.)

    Adminul principal **nu** e aici: credentialele lui vin din mediu (`ORAR_ADMIN_USER`,
    `ORAR_ADMIN_PAROLA`), vezi `web.auth`. Rolul `admin` de aici e pentru conturile pe care
    el le ridica la admin.
    """

    __tablename__ = "USER"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    nume: Mapped[str] = mapped_column("NUME", String(120), nullable=False)
    #: Cu el se face autentificarea.
    email: Mapped[str | None] = mapped_column("EMAIL", String(180), unique=True)
    #: `scrypt$n$r$p$sare$hash` -- vezi `web.auth`.
    parola_hash: Mapped[str | None] = mapped_column("PAROLA_HASH", String(255))
    #: student | voluntar | profesor | admin -- vezi `ROLURI`.
    rol: Mapped[str] = mapped_column(
        "ROL", String(12), nullable=False, default="student", server_default="student"
    )
    #: Grupa contului: "Orarul meu" e orarul ei.
    grupa_id: Mapped[int | None] = mapped_column(
        "ID_GRUPA", ForeignKey("GRUPA.ID_GRUPA", ondelete="SET NULL"), index=True
    )
    #: Semigrupa aleasa pe orarul meu ("Gr_1").
    semigrupa: Mapped[str | None] = mapped_column("SEMIGRUPA", String(8))
    #: Ce e ascuns pe orarul meu dintre optionale/facultative: lista JSON de chei, aceleasi
    #: ca in cookie (`web.afisare`): slug de materie sau `@amprenta` de activitate.
    ascunse: Mapped[str | None] = mapped_column("ASCUNSE", Text)
    #: Pentru rolul `profesor`: numele intreg al profesorului, ca in orarul profesorilor --
    #: "Orarul meu" e orarul lui. Nume, nu id: randurile PROFESOR se pot recrea la un orar nou.
    profesor: Mapped[str | None] = mapped_column("PROFESOR", String(120))
    #: Cererea de a primi rolul de profesor: numele ales, pana o trateaza un admin.
    cerere_profesor: Mapped[str | None] = mapped_column("CERERE_PROFESOR", String(120))

    grupa: Mapped[Grupa | None] = relationship()

    __table_args__ = (
        CheckConstraint("ROL IN ('student','voluntar','profesor','admin')", name="ck_user_rol"),
    )

    def __repr__(self) -> str:
        return f"<User {self.nume!r} ({self.rol})>"


class SursaOrar(Base):
    """Ce a publicat facultatea si cand am ingestat-o noi.

    Adaugire operationala, in afara schemei cerute. Fara ea, watcher-ul nu are cu ce compara
    si ar trebui sa reia ingestul (~6 minute de captura) la fiecare verificare. Tinem starea
    **per sursa** -- semestru x fel -- fiindca pagina publica patru orare independente, cu
    date de actualizare proprii; un singur `state.json` global le-ar amesteca.

    Bonus vizibil in interfata: `ACTUALIZAT` e data pe care o anunta chiar facultatea, deci
    se poate arata "orar actualizat 26.04.2026" fara sa inventam nimic.
    """

    __tablename__ = "SURSA_ORAR"

    id: Mapped[int] = mapped_column("ID_SURSA", Integer, primary_key=True)
    an_univ: Mapped[str] = mapped_column("AN_UNIV", String(9), nullable=False)
    semestru: Mapped[int] = mapped_column("SEMESTRU", Integer, nullable=False)
    #: grupe | profesori
    fel: Mapped[str] = mapped_column("FEL", String(12), nullable=False)
    url: Mapped[str] = mapped_column("URL", String(255), nullable=False)
    #: Data anuntata pe pagina FMI ("actualizat 26.04.2026, ora 19:30").
    actualizat: Mapped[datetime | None] = mapped_column("ACTUALIZAT", DateTime)
    #: Cand am citit ultima oara pagina.
    verificat_la: Mapped[datetime | None] = mapped_column("VERIFICAT_LA", DateTime)
    #: `ACTUALIZAT` de la ultimul ingest reusit. Egal cu `ACTUALIZAT` => suntem la zi.
    ingestat_la: Mapped[datetime | None] = mapped_column("INGESTAT_LA", DateTime)

    __table_args__ = (
        UniqueConstraint("AN_UNIV", "SEMESTRU", "FEL", name="uq_sursa"),
        CheckConstraint("FEL IN ('grupe','profesori')", name="ck_sursa_fel"),
        CheckConstraint("SEMESTRU IN (1,2)", name="ck_sursa_semestru"),
    )

    @property
    def la_zi(self) -> bool:
        return self.actualizat is not None and self.actualizat == self.ingestat_la

    def __repr__(self) -> str:
        return f"<SursaOrar sem{self.semestru} {self.fel!r}>"


# ---------------------------------------------------------------------------
# Corectii manuale si sesizari
# ---------------------------------------------------------------------------


class Corectie(Base):
    """O schimbare facuta de un admin in orar: o activitate adaugata, sau una existenta
    modificata -- orice din ea: materia, cand si unde se tine, cu cine, pentru cine.

    De ce nu se modifica pur si simplu randul din ORA: fiecare ingest **sterge si reincarca**
    orele semestrului, deci o modificare facuta direct ar disparea, fara niciun semn, la
    urmatorul orar publicat. Corectia tine minte *ce* s-a schimbat si *cui*, in cuvinte care
    supravietuiesc reingestului -- slug-ul grupei, ziua, orele, materia, nu id-uri -- iar
    `db.corectii.aplica_dupa_ingest` o pune la loc dupa fiecare incarcare. Daca orarul nou
    nu mai are activitatea respectiva, corectia ramane marcata neaplicata, ca adminul sa vada.
    """

    __tablename__ = "CORECTIE"

    id: Mapped[int] = mapped_column("ID_CORECTIE", Integer, primary_key=True)
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)
    #: Numele adminului care a facut-o.
    creat_de: Mapped[str] = mapped_column("CREAT_DE", String(120), nullable=False)
    #: adaugare | modificare
    fel: Mapped[str] = mapped_column("FEL", String(12), nullable=False)
    an_univ: Mapped[str] = mapped_column("AN_UNIV", String(12), nullable=False)
    semestru: Mapped[int] = mapped_column("SEMESTRU", Integer, nullable=False)

    # --- activitatea **asa cum trebuie sa fie**: tot ce a ales adminul ---
    grupa_slug: Mapped[str] = mapped_column("GRUPA_SLUG", String(180), nullable=False)
    zi: Mapped[str] = mapped_column("ZI", String(12), nullable=False)
    ora_inceput: Mapped[time] = mapped_column("ORA_INCEPUT", Time, nullable=False)
    ora_sfarsit: Mapped[time] = mapped_column("ORA_SFARSIT", Time, nullable=False)
    materie: Mapped[str | None] = mapped_column("MATERIE", String(160))
    tip: Mapped[str | None] = mapped_column("TIP", String(24))
    semigrupa: Mapped[str | None] = mapped_column("SEMIGRUPA", String(8))
    frecventa: Mapped[str | None] = mapped_column("FRECVENTA", String(4))
    saptamani: Mapped[str | None] = mapped_column("SAPTAMANI", String(80))
    profesor: Mapped[str | None] = mapped_column("PROFESOR", String(120))
    sala: Mapped[str | None] = mapped_column("SALA", String(80))

    #: Doar la `modificare`: activitatea **cum era in orarul publicat**, ca obiect JSON cu
    #: aceleasi campuri (plus `legaturi`, formatiunile cu care era partajata). Dupa ea se
    #: regaseste activitatea intr-un orar nou si la ea se revine la anulare.
    original: Mapped[str | None] = mapped_column("ORIGINAL", Text)

    #: False cand ultimul ingest n-a mai gasit activitatea (sau grupa) careia i se aplica.
    aplicata: Mapped[bool] = mapped_column(
        "APLICATA", Boolean, nullable=False, default=True, server_default="1"
    )

    __table_args__ = (
        CheckConstraint("FEL IN ('adaugare','modificare')", name="ck_corectie_fel"),
        Index("ix_corectie_an_sem", "AN_UNIV", "SEMESTRU"),
    )

    def __repr__(self) -> str:
        return f"<Corectie {self.fel} {self.grupa_slug} {self.zi} {self.ora_inceput:%H:%M}>"


class Sesizare(Base):
    """O problema semnalata de un vizitator sau de un utilizator, pentru admini.

    `ACTIVITATE` e descrierea in cuvinte a activitatii vizate, luata la momentul sesizarii:
    randul din ORA poate disparea la urmatorul ingest (`ID_ORA` devine NULL), dar adminul
    trebuie sa inteleaga in continuare despre ce era vorba.
    """

    __tablename__ = "SESIZARE"

    id: Mapped[int] = mapped_column("ID_SESIZARE", Integer, primary_key=True)
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)
    #: noua | rezolvata | respinsa
    stare: Mapped[str] = mapped_column(
        "STARE", String(10), nullable=False, default="noua", server_default="noua"
    )
    #: Pagina de pe care a fost trimisa (`/grupa/244`).
    pagina: Mapped[str] = mapped_column("PAGINA", String(200), nullable=False)
    ora_id: Mapped[int | None] = mapped_column(
        "ID_ORA", ForeignKey("ORA.ID_ORA", ondelete="SET NULL"), index=True
    )
    activitate: Mapped[str | None] = mapped_column("ACTIVITATE", String(300))
    mesaj: Mapped[str] = mapped_column("MESAJ", Text, nullable=False)
    #: Cine a trimis-o, daca era autentificat.
    user_id: Mapped[int | None] = mapped_column(
        "ID_USER", ForeignKey("USER.ID", ondelete="SET NULL"), index=True
    )
    #: Optional, pentru vizitatori: un email sau un nume la care sa li se raspunda.
    contact: Mapped[str | None] = mapped_column("CONTACT", String(180))

    tratata_de: Mapped[str | None] = mapped_column("TRATATA_DE", String(120))
    tratata_la: Mapped[datetime | None] = mapped_column("TRATATA_LA", DateTime)
    nota: Mapped[str | None] = mapped_column("NOTA", String(500))

    ora: Mapped[Ora | None] = relationship()
    user: Mapped[User | None] = relationship()

    __table_args__ = (
        CheckConstraint("STARE IN ('noua','rezolvata','respinsa')", name="ck_sesizare_stare"),
        Index("ix_sesizare_stare", "STARE", "CREAT_LA"),
    )

    def __repr__(self) -> str:
        return f"<Sesizare {self.id} {self.stare}>"


# ---------------------------------------------------------------------------
# Versiuni anterioare ale orarului
# ---------------------------------------------------------------------------


class VersiuneOrar(Base):
    """O publicare mai veche a orarului, pastrata cand facultatea a publicat alta.

    Adaugire operationala. Ingestul inlocuieste orele semestrului; inainte de asta le copiem
    aici, ca /grupa/{id}?versiune=N sa poata arata orarul de atunci. O versiune inseamna o
    **publicare FMI**, nu o rulare: reluarea aceleiasi publicari nu adauga o versiune.

    Tabelele de arhiva oglindesc ORA si ORA_GRUPA in loc sa adauge o coloana de versiune pe
    ORA. Asa, tot restul aplicatiei (ocuparea salilor, cautarea, coada de verificare,
    vocabularul OCR) vede doar orarul actual fara sa stie de versiuni -- un filtru uitat
    intr-o singura interogare ar dubla, de exemplu, rezervarile salilor.
    """

    __tablename__ = "VERSIUNE_ORAR"

    id: Mapped[int] = mapped_column("ID_VERSIUNE", Integer, primary_key=True)
    an_univ: Mapped[str] = mapped_column("AN_UNIV", String(9), nullable=False)
    semestru: Mapped[int] = mapped_column("SEMESTRU", Integer, nullable=False)
    #: Data anuntata de FMI pentru orarul arhivat. NULL cand datele veneau dintr-un import
    #: manual, fara data de publicare cunoscuta.
    publicat: Mapped[datetime | None] = mapped_column("PUBLICAT", DateTime)
    #: Cand a fost inlocuit de o publicare mai noua.
    arhivat_la: Mapped[datetime] = mapped_column("ARHIVAT_LA", DateTime, nullable=False)
    nr_ore: Mapped[int] = mapped_column("NR_ORE", Integer, nullable=False)

    ore: Mapped[list[OraArhivata]] = relationship(
        back_populates="versiune", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("SEMESTRU IN (1,2)", name="ck_versiune_semestru"),
        Index("ix_versiune_an_sem", "AN_UNIV", "SEMESTRU"),
    )

    def __repr__(self) -> str:
        return f"<VersiuneOrar sem{self.semestru} {self.publicat}>"


class OraArhivata(Base):
    """O activitate dintr-o versiune anterioara: aceleasi coloane ca ORA.

    Cheile spre PROFESOR, MATERIE, SALA si GRUPA raman chei, nu text copiat: entitatile
    sunt stabile intre publicari, iar `curata_orfanii` nu le sterge cat timp le foloseste
    si arhiva.
    """

    __tablename__ = "ORA_ARHIVA"

    id: Mapped[int] = mapped_column("ID_ORA_ARHIVA", Integer, primary_key=True)
    versiune_id: Mapped[int] = mapped_column(
        "ID_VERSIUNE",
        ForeignKey("VERSIUNE_ORAR.ID_VERSIUNE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: ID_ORA din momentul arhivarii -- doar ca sa copiem legaturile ORA_GRUPA.
    ora_originala: Mapped[int] = mapped_column("ID_ORA_ORIGINALA", Integer, nullable=False)

    profesor_id: Mapped[int | None] = mapped_column(
        "ID_PROFESOR", ForeignKey("PROFESOR.ID_PROFESOR"), index=True
    )
    materie_id: Mapped[int | None] = mapped_column(
        "ID_MATERIE", ForeignKey("MATERIE.ID_MATERIE"), index=True
    )
    sala_id: Mapped[int | None] = mapped_column("ID_SALA", ForeignKey("SALA.ID_SALA"), index=True)
    grupa_id: Mapped[int] = mapped_column(
        "ID_GRUPA", ForeignKey("GRUPA.ID_GRUPA"), nullable=False, index=True
    )
    perioada_id: Mapped[int] = mapped_column(
        "ID_PERIOADA", ForeignKey("PERIOADA.ID_PERIOADA"), nullable=False
    )

    tip_ora_materie: Mapped[str | None] = mapped_column("TIP_ORA_MATERIE", String(24))
    ora_inceput: Mapped[time] = mapped_column("ORA_INCEPUT", Time, nullable=False)
    ora_sfarsit: Mapped[time] = mapped_column("ORA_SFARSIT", Time, nullable=False)
    zi_saptamana: Mapped[str] = mapped_column("ZI_SAPTAMANA", String(12), nullable=False)
    frecventa: Mapped[str | None] = mapped_column("FRECVENTA", String(4))
    saptamani: Mapped[str | None] = mapped_column("SAPTAMANI", String(80))
    semigrupa: Mapped[str | None] = mapped_column("SEMIGRUPA", String(8))
    sursa_pagina: Mapped[str | None] = mapped_column("SURSA_PAGINA", String(60))
    confidence: Mapped[float | None] = mapped_column("CONFIDENCE")
    sursa_bbox: Mapped[str | None] = mapped_column("SURSA_BBOX", String(40))
    campuri_nesigure: Mapped[str | None] = mapped_column("CAMPURI_NESIGURE", String(60))

    versiune: Mapped[VersiuneOrar] = relationship(back_populates="ore")
    profesor: Mapped[Profesor | None] = relationship()
    materie: Mapped[Materie | None] = relationship()
    sala: Mapped[Sala | None] = relationship()
    grupa: Mapped[Grupa] = relationship()
    perioada: Mapped[Perioada] = relationship()

    def __repr__(self) -> str:
        return f"<OraArhivata v{self.versiune_id} {self.zi_saptamana} {self.ora_inceput:%H:%M}>"


class OraGrupaArhivata(Base):
    """ORA_GRUPA pentru o versiune anterioara."""

    __tablename__ = "ORA_GRUPA_ARHIVA"

    ora_id: Mapped[int] = mapped_column(
        "ID_ORA_ARHIVA",
        ForeignKey("ORA_ARHIVA.ID_ORA_ARHIVA", ondelete="CASCADE"),
        primary_key=True,
    )
    grupa_id: Mapped[int] = mapped_column(
        "ID_GRUPA", ForeignKey("GRUPA.ID_GRUPA", ondelete="CASCADE"), primary_key=True
    )


class AncoraSaptamana(Base):
    """Corespondenta publicata intre o saptamana calendaristica si numarul ei academic.

    Adaugire operationala. Numerotarea FMI sare peste vacante, deci nu se poate calcula
    dintr-o singura ancora (vezi `domain/weeks.py`); pastram toate ancorele anuntate, iar
    watcher-ul le reimprospateaza la fiecare verificare.
    """

    __tablename__ = "ANCORA_SAPTAMANA"

    id: Mapped[int] = mapped_column("ID_ANCORA", Integer, primary_key=True)
    an_univ: Mapped[str] = mapped_column("AN_UNIV", String(9), nullable=False)
    semestru: Mapped[int] = mapped_column("SEMESTRU", Integer, nullable=False)
    #: Lunea saptamanii.
    inceput: Mapped[date] = mapped_column("INCEPUT", Date, nullable=False)
    numar: Mapped[int] = mapped_column("NUMAR", Integer, nullable=False)
    #: SI | SP
    paritate: Mapped[str] = mapped_column("PARITATE", String(2), nullable=False)

    __table_args__ = (
        UniqueConstraint("AN_UNIV", "SEMESTRU", "INCEPUT", name="uq_ancora"),
        CheckConstraint("PARITATE IN ('SI','SP')", name="ck_ancora_paritate"),
    )

    def __repr__(self) -> str:
        return f"<AncoraSaptamana {self.inceput} sapt {self.numar}>"


#: Felurile de evenimente: ale asociatiei (apar si in calendarul ASMI) si orice altceva
#: pentru care se rezerva o sala.
FELURI_EVENIMENT = ("asmi", "alta")
#: Unde se vede un eveniment: pe toate orarele, doar pe ale unor specializari, sau doar pe
#: orarul salii rezervate.
VIZIBILITATI_EVENIMENT = ("toate", "specializari", "sala")
#: Culorile dintre care se alege la un eveniment: (cheie, nume afisat). Valorile propriu-zise
#: sunt in CSS (`.culoare-...`), ca sa arate bine si pe fundal deschis, si pe inchis.
CULORI_EVENIMENT = (
    ("albastru", "Albastru"),
    ("turcoaz", "Turcoaz"),
    ("verde", "Verde"),
    ("galben", "Galben"),
    ("portocaliu", "Portocaliu"),
    ("rosu", "Roșu"),
    ("roz", "Roz"),
    ("mov", "Mov"),
)


class Eveniment(Base):
    """O activitate cu **data**, in afara orarului saptamanal: un eveniment ASMI, o sala
    rezervata pentru altceva, sau o perioada fara sala (recrutari, Balul Bobocilor).

    Nu e o `ORA`: orele se repeta saptamanal si se reincarca la fiecare orar nou; un
    eveniment are o zi anume (si in weekend) si ramane pana il sterge un admin.
    """

    __tablename__ = "EVENIMENT"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    fel: Mapped[str] = mapped_column("FEL", String(8), nullable=False, default="asmi")
    titlu: Mapped[str] = mapped_column("TITLU", String(160), nullable=False)
    descriere: Mapped[str | None] = mapped_column("DESCRIERE", Text)
    link: Mapped[str | None] = mapped_column("LINK", String(300))
    #: O singura zi (`DATA_SFARSIT` = `DATA_INCEPUT`) sau o perioada.
    data_inceput: Mapped[date] = mapped_column("DATA_INCEPUT", Date, nullable=False)
    data_sfarsit: Mapped[date] = mapped_column("DATA_SFARSIT", Date, nullable=False)
    #: NULL amandoua: toata ziua / toata perioada.
    ora_inceput: Mapped[time | None] = mapped_column("ORA_INCEPUT", Time)
    ora_sfarsit: Mapped[time | None] = mapped_column("ORA_SFARSIT", Time)
    sala_id: Mapped[int | None] = mapped_column(
        "SALA", ForeignKey("SALA.ID_SALA", ondelete="SET NULL")
    )
    #: Locul, cand nu e o sala a facultatii.
    loc: Mapped[str | None] = mapped_column("LOC", String(160))
    vizibilitate: Mapped[str] = mapped_column(
        "VIZIBILITATE", String(14), nullable=False, default="sala"
    )
    #: Codurile specializarilor (`INFO,CTI`), pentru vizibilitatea `specializari`. Coduri,
    #: nu id-uri de GRUPA: nodurile se pot recrea la un orar nou.
    specializari: Mapped[str | None] = mapped_column("SPECIALIZARI", String(200))
    #: Anii de studiu (`L1,L3,M1`: licenta anul 1 si 3, master anul 1), tot pentru
    #: vizibilitatea `specializari`. Gol = toti anii; la fel, fara coduri = toate specializarile.
    ani: Mapped[str | None] = mapped_column("ANI", String(40))
    #: Cheia unei culori din `CULORI_EVENIMENT` sau o culoare proprie (`#rrggbb`);
    #: NULL = culoarea obisnuita a felului.
    culoare: Mapped[str | None] = mapped_column("CULOARE", String(12))
    creat_de: Mapped[str] = mapped_column("CREAT_DE", String(120), nullable=False)
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)

    sala: Mapped[Sala | None] = relationship()

    __table_args__ = (
        CheckConstraint("FEL IN ('asmi','alta')", name="ck_eveniment_fel"),
        CheckConstraint(
            "VIZIBILITATE IN ('toate','specializari','sala')", name="ck_eveniment_vizibilitate"
        ),
        CheckConstraint("DATA_INCEPUT <= DATA_SFARSIT", name="ck_eveniment_perioada"),
        Index("ix_eveniment_data", "DATA_INCEPUT", "DATA_SFARSIT"),
    )

    @property
    def clasa_culoare(self) -> str:
        """Clasa CSS a culorii: a uneia din lista, sau `culoare-proprie` (vezi `stil_culoare`)."""
        if not self.culoare:
            return ""
        return "culoare-proprie" if self.culoare.startswith("#") else f"culoare-{self.culoare}"

    @property
    def stil_culoare(self) -> str:
        """Pentru o culoare proprie: declaratia CSS care o da elementului."""
        return f"--ev: {self.culoare};" if (self.culoare or "").startswith("#") else ""

    @property
    def coduri(self) -> list[str]:
        return [c for c in (self.specializari or "").split(",") if c]

    @property
    def ani_alesi(self) -> list[str]:
        return [a for a in (self.ani or "").split(",") if a]

    @property
    def pe_mai_multe_zile(self) -> bool:
        return self.data_sfarsit > self.data_inceput

    def __repr__(self) -> str:
        return f"<Eveniment {self.fel} {self.titlu!r} {self.data_inceput}>"


class FiltruSalvat(Base):
    """O configurare de filtre a orarului de statistici, salvata cu nume de un admin."""

    __tablename__ = "FILTRU_SALVAT"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    nume: Mapped[str] = mapped_column("NUME", String(80), nullable=False)
    #: Filtrele, ca parametri de adresa (`specializari=INFO&ani=L2&metrica=libere`).
    parametri: Mapped[str] = mapped_column("PARAMETRI", Text, nullable=False)
    creat_de: Mapped[str] = mapped_column("CREAT_DE", String(120), nullable=False)
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)


class PerioadaStructura(Base):
    """O perioada din structura anului universitar, pusa de un admin: activitate didactica,
    vacanta, sesiune, sesiune de restante si mariri, sustinerea licentei / disertatiei.

    Din perioadele didactice se numara saptamanile academice -- vezi `domain/weeks.py`.
    """

    __tablename__ = "STRUCTURA_AN"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    an_univ: Mapped[str] = mapped_column("AN_UNIV", String(9), nullable=False, index=True)
    fel: Mapped[str] = mapped_column("FEL", String(10), nullable=False)
    #: 1 sau 2; NULL pentru ce tine de tot anul (de exemplu sustinerea licentei).
    semestru: Mapped[int | None] = mapped_column("SEMESTRU", Integer)
    #: Numele afisat ("Vacanța de iarnă"); gol = numele felului.
    nume: Mapped[str | None] = mapped_column("NUME", String(80))
    data_inceput: Mapped[date] = mapped_column("DATA_INCEPUT", Date, nullable=False)
    data_sfarsit: Mapped[date] = mapped_column("DATA_SFARSIT", Date, nullable=False)
    #: Doar la activitatea didactica: zilele perioadei sunt saptamana N, fara sa avanseze
    #: numaratoarea (completarea unei saptamani inceput la mijloc).
    saptamana: Mapped[int | None] = mapped_column("SAPTAMANA", Integer)
    #: toti / neterminali / terminali: anii terminali au alt semestru 2.
    pentru: Mapped[str] = mapped_column(
        "PENTRU", String(12), nullable=False, default="toti", server_default="toti"
    )

    __table_args__ = (
        CheckConstraint(
            "FEL IN ('didactica','vacanta','sesiune','restante','licenta','liber')",
            name="ck_structura_fel",
        ),
        CheckConstraint("PENTRU IN ('toti','neterminali','terminali')", name="ck_structura_pentru"),
        CheckConstraint("DATA_INCEPUT <= DATA_SFARSIT", name="ck_structura_perioada"),
    )

    def ca_perioada(self):  # noqa: ANN201 -- domain.weeks.PerioadaAn
        from orar.domain.weeks import PerioadaAn

        return PerioadaAn(
            fel=self.fel,
            inceput=self.data_inceput,
            sfarsit=self.data_sfarsit,
            semestru=self.semestru,
            nume=self.nume or "",
            saptamana=self.saptamana,
            an=self.an_univ,
            pentru=self.pentru,
        )
