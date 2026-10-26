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
    #: Blocata de un admin: nu apare ca libera in tabul "Fereastra" de pe prima pagina
    #: (protejarea echipamentelor). Nu afecteaza lista adminului de la evenimente.
    blocata: Mapped[bool] = mapped_column(
        "BLOCATA", Boolean, nullable=False, default=False, server_default="0"
    )
    #: O sala din afara orarului FMI ("sala 220", "L-321"), adaugata pentru tutoriate. Nu o
    #: sterge curatenia de dupa incarcarea unui orar, desi n-are nicio ora.
    doar_tutoriate: Mapped[bool] = mapped_column(
        "DOAR_TUTORIATE", Boolean, nullable=False, default=False, server_default="0"
    )

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
#: Rolurile date din Orar. Adminul nu e printre ele: il da rolul `cddd` din SSO-ul ASMI.
ROLURI_LOCALE = ("student", "voluntar", "profesor")


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
    #: True: "Orarul meu" urmeaza orarul implicit al sitului si se actualizeaza singur (la
    #: un an universitar nou, grupa se reseteaza). False: ramane pe `orar_ales`, iar grupa
    #: nu se mai reseteaza.
    orar_automat: Mapped[bool] = mapped_column(
        "ORAR_AUTOMAT", Boolean, nullable=False, default=True, server_default="1"
    )
    orar_ales_id: Mapped[int | None] = mapped_column(
        "ORAR_ALES", ForeignKey("PERIOADA.ID_PERIOADA", ondelete="SET NULL")
    )
    #: Tutoriatele alese pe "Orarul meu": lista JSON de id-uri. Spre deosebire de optionale,
    #: se tine ce e *ales* -- tutoriatele nu apar decat daca le vrei.
    tutoriate_alese: Mapped[str | None] = mapped_column("TUTORIATE_ALESE", Text)
    #: Identificatorul contului in SSO-ul ASMI (claim-ul `sub` din Keycloak). Numele si
    #: emailul de mai sus sunt doar o copie de afisare, rescrisa la fiecare autentificare.
    sso_sub: Mapped[str | None] = mapped_column("SSO_SUB", String(64), unique=True)

    grupa: Mapped[Grupa | None] = relationship()
    orar_ales: Mapped[Perioada | None] = relationship()

    __table_args__ = (
        CheckConstraint("ROL IN ('student','voluntar','profesor','admin')", name="ck_user_rol"),
    )

    def __repr__(self) -> str:
        return f"<User {self.nume!r} ({self.rol})>"


class SsoSesiune(Base):
    """O sesiune deschisa prin SSO-ul ASMI, tinuta pe server.

    Cookie-ul sitului poarta doar `id`-ul; tokenurile Keycloak raman aici, nu ajung in
    browser. `roluri` (JSON: `{"realm": [...], "client": [...]}`) e ce spunea ultimul token;
    se reimprospateaza la cateva minute (`web.sso`), deci un rol retras in Keycloak se pierde
    repede, iar un logout din alt sit ASMI (backchannel, dupa `sid`) inchide sesiunea.
    """

    __tablename__ = "SSO_SESIUNE"

    id: Mapped[str] = mapped_column("ID", String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        "ID_USER", ForeignKey("USER.ID", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Sesiunea Keycloak (claim-ul `sid`), pentru logout-ul backchannel.
    sid: Mapped[str | None] = mapped_column("SID", String(64), index=True)
    refresh_token: Mapped[str | None] = mapped_column("REFRESH_TOKEN", Text)
    id_token: Mapped[str | None] = mapped_column("ID_TOKEN", Text)
    roluri: Mapped[str] = mapped_column("ROLURI", Text, nullable=False, default="{}")
    #: Campurile de studii din profilul ASMI (scope-ul `orar-profile`), pentru grupa propusa.
    profil: Mapped[str | None] = mapped_column("PROFIL", Text)
    creata_la: Mapped[datetime] = mapped_column("CREATA_LA", DateTime, nullable=False)
    verificat_la: Mapped[datetime] = mapped_column("VERIFICAT_LA", DateTime, nullable=False)
    #: Dupa asta sesiunea nu mai e valabila, oricum (expirarea refresh token-ului).
    expira_la: Mapped[datetime] = mapped_column("EXPIRA_LA", DateTime, nullable=False)

    user: Mapped[User] = relationship()


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
    #: grupe | profesori | tutoriate
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
        CheckConstraint("FEL IN ('grupe','profesori','tutoriate')", name="ck_sursa_fel"),
        CheckConstraint("SEMESTRU IN (1,2)", name="ck_sursa_semestru"),
    )

    @property
    def la_zi(self) -> bool:
        return self.actualizat is not None and self.actualizat == self.ingestat_la

    def __repr__(self) -> str:
        return f"<SursaOrar sem{self.semestru} {self.fel!r}>"


class ArticolAsmi(Base):
    """Ultimul articol publicat pe asmi.ro, pentru „fun fact”-ul din subsol.

    O singura linie, adusa periodic de worker (`db/articole.py`), nu de la utilizator.
    Linkul articolului e `https://asmi.ro/Articole/{slug}`.
    """

    __tablename__ = "ARTICOL_ASMI"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    slug: Mapped[str] = mapped_column("SLUG", String(200), nullable=False, unique=True)
    titlu: Mapped[str] = mapped_column("TITLU", String(300), nullable=False)
    actualizat_la: Mapped[datetime] = mapped_column("ACTUALIZAT_LA", DateTime, nullable=False)

    def __repr__(self) -> str:
        return f"<ArticolAsmi {self.slug!r}>"


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


class Setare(Base):
    """O valoare de tinut minte intre reporniri, care nu are o tabela a ei: de exemplu
    anul universitar pentru care s-au resetat deja grupele conturilor."""

    __tablename__ = "SETARE"

    cheie: Mapped[str] = mapped_column("CHEIE", String(60), primary_key=True)
    valoare: Mapped[str] = mapped_column("VALOARE", Text, nullable=False)


# ---------------------------------------------------------------------------
# Tutoriate
# ---------------------------------------------------------------------------

#: Permisiunile care se pot acorda unui cont, pe langa rol: (cheie, descriere).
#: Fiecare cheie deschide o zona functionala a aplicatiei. Rolurile de realm din SSO
#: (`ROLURI_REALM_PERMISIUNI` din web/sso.py) aduc un set implicit, iar un admin le poate
#: acorda individual aici (tabela PERMISIUNE) sau prin roluri de client in Keycloak.
PERMISIUNI = (
    ("incarcare", "Încarcă un orar nou și alege orarul implicit"),
    ("review", "Coada de verificare a activităților citite automat"),
    ("editare", "Corecții manuale în orar"),
    # Orarul profesorilor nu e public: implicit doar adminii il vad, un profesor pe-al lui,
    # iar celelalte conturi primesc permisiunea de la un admin.
    ("orar_profesori", "Vede orarele profesorilor"),
    ("sesizari", "Tratează sesizările"),
    ("evenimente", "Evenimente ASMI și rezervări de săli"),
    ("statistici", "Statistici și filtre salvate"),
    ("structura", "Structura anului universitar"),
    ("tutoriate", "Responsabil de tutoriate"),
    ("utilizatori", "Gestionarea conturilor, rolurilor și permisiunilor"),
)


class Permisiune(Base):
    """O responsabilitate a unui cont, pe langa rolul lui: de exemplu un voluntar care
    raspunde de tutoriate. Tabela si nu un rol nou: rolurile sunt exclusive, responsabilitatile
    se aduna, iar constrangerea pe USER.ROL nu se poate schimba fara a reconstrui tabela."""

    __tablename__ = "PERMISIUNE"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        "ID_USER", ForeignKey("USER.ID", ondelete="CASCADE"), nullable=False, index=True
    )
    cheie: Mapped[str] = mapped_column("CHEIE", String(30), nullable=False)
    acordat_de: Mapped[str] = mapped_column("ACORDAT_DE", String(120), nullable=False)
    acordat_la: Mapped[datetime] = mapped_column("ACORDAT_LA", DateTime, nullable=False)

    user: Mapped[User] = relationship()

    __table_args__ = (UniqueConstraint("ID_USER", "CHEIE", name="uq_permisiune"),)


class Tutor(Base):
    """Un cont asignat ca tutor la o materie, pe o perioada (implicit 6 luni). Un cont poate
    avea mai multe asignari deodata (materii diferite) si o asignare se poate prelungi.

    Fara cont inca: `user_id` gol si `email` completat; asignarea se leaga de cont cand
    cineva se inregistreaza cu acel email."""

    __tablename__ = "TUTOR"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        "ID_USER", ForeignKey("USER.ID", ondelete="CASCADE"), index=True
    )
    email: Mapped[str | None] = mapped_column("EMAIL", String(180), index=True)
    #: Numele intreg al materiei ("Programare orientată pe obiecte"), ca in foaia tutoriatelor.
    materie: Mapped[str] = mapped_column("MATERIE", String(160), nullable=False)
    #: Publicul tutoriatelor lui: coduri de specializare si de an (`INFO`, `L1`), ca la evenimente.
    specializari: Mapped[str | None] = mapped_column("SPECIALIZARI", String(200))
    ani: Mapped[str | None] = mapped_column("ANI", String(40))
    valabil_de_la: Mapped[date] = mapped_column("VALABIL_DE_LA", Date, nullable=False)
    valabil_pana_la: Mapped[date] = mapped_column("VALABIL_PANA_LA", Date, nullable=False)
    creat_de: Mapped[str] = mapped_column("CREAT_DE", String(120), nullable=False)
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)

    user: Mapped[User | None] = relationship()

    __table_args__ = (
        CheckConstraint("VALABIL_DE_LA <= VALABIL_PANA_LA", name="ck_tutor_perioada"),
        CheckConstraint("ID_USER IS NOT NULL OR EMAIL IS NOT NULL", name="ck_tutor_cine"),
    )

    def activ(self, zi: date) -> bool:
        return self.valabil_de_la <= zi <= self.valabil_pana_la

    @property
    def nume(self) -> str:
        return self.user.nume if self.user is not None else (self.email or "?")


#: Starile unui tutoriat: ciorna (doar in panou), publicat (pe sit), incheiat (arhiva).
STARI_TUTORIAT = ("ciorna", "publicat", "incheiat")


class Tutoriat(Base):
    """O sedinta saptamanala de sprijin la o materie, tinuta de o echipa de tutori.

    Nu e o `ORA`: orele se reincarca la fiecare orar nou; un tutoriat il pune responsabilul
    si ramane. Se tine in `ZI_SAPTAMANA`, intre `DATA_INCEPUT` si `DATA_SFARSIT`, mai putin
    zilele fara activitate didactica (vacante, zile libere) si sedintele anulate."""

    __tablename__ = "TUTORIAT"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    an_univ: Mapped[str] = mapped_column("AN_UNIV", String(9), nullable=False)
    semestru: Mapped[int] = mapped_column("SEMESTRU", Integer, nullable=False)
    materie: Mapped[str] = mapped_column("MATERIE", String(160), nullable=False)
    zi_saptamana: Mapped[str] = mapped_column("ZI_SAPTAMANA", String(10), nullable=False)
    ora_inceput: Mapped[time] = mapped_column("ORA_INCEPUT", Time, nullable=False)
    ora_sfarsit: Mapped[time] = mapped_column("ORA_SFARSIT", Time, nullable=False)
    sala_id: Mapped[int | None] = mapped_column(
        "SALA", ForeignKey("SALA.ID_SALA", ondelete="SET NULL")
    )
    #: Locul, cand nu e o sala din lista (online, alta cladire).
    loc: Mapped[str | None] = mapped_column("LOC", String(160))
    data_inceput: Mapped[date] = mapped_column("DATA_INCEPUT", Date, nullable=False)
    data_sfarsit: Mapped[date] = mapped_column("DATA_SFARSIT", Date, nullable=False)
    #: SI / SP: doar in saptamanile impare / pare. Gol: in fiecare saptamana.
    frecventa: Mapped[str | None] = mapped_column("FRECVENTA", String(4))
    #: Publicul: coduri de specializare, de an si slug-uri de serie (gol = fara restrictie).
    specializari: Mapped[str | None] = mapped_column("SPECIALIZARI", String(200))
    ani: Mapped[str | None] = mapped_column("ANI", String(40))
    serii: Mapped[str | None] = mapped_column("SERII", String(300))
    link_whatsapp: Mapped[str | None] = mapped_column("LINK_WHATSAPP", String(300))
    link_teams: Mapped[str | None] = mapped_column("LINK_TEAMS", String(300))
    descriere: Mapped[str | None] = mapped_column("DESCRIERE", Text)
    stare: Mapped[str] = mapped_column(
        "STARE", String(10), nullable=False, default="ciorna", server_default="ciorna"
    )
    creat_de: Mapped[str] = mapped_column("CREAT_DE", String(120), nullable=False)
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)

    sala: Mapped[Sala | None] = relationship()
    echipa: Mapped[list[TutoriatTutor]] = relationship(
        back_populates="tutoriat",
        cascade="all, delete-orphan",
        order_by="TutoriatTutor.principal.desc()",
    )
    anulari: Mapped[list[TutoriatAnulare]] = relationship(
        back_populates="tutoriat", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("STARE IN ('ciorna','publicat','incheiat')", name="ck_tutoriat_stare"),
        CheckConstraint("DATA_INCEPUT <= DATA_SFARSIT", name="ck_tutoriat_perioada"),
        CheckConstraint("ORA_INCEPUT < ORA_SFARSIT", name="ck_tutoriat_ore"),
        Index("ix_tutoriat_slot", "AN_UNIV", "SEMESTRU", "ZI_SAPTAMANA"),
    )

    @staticmethod
    def _lista(text: str | None) -> list[str]:
        return [x for x in (text or "").split(",") if x]

    @property
    def coduri(self) -> list[str]:
        return self._lista(self.specializari)

    @property
    def ani_alesi(self) -> list[str]:
        return self._lista(self.ani)

    @property
    def serii_alese(self) -> list[str]:
        return self._lista(self.serii)

    @property
    def interval(self) -> str:
        return f"{self.ora_inceput.hour}-{self.ora_sfarsit.hour}"

    @property
    def unde(self) -> str:
        return self.sala.nume if self.sala is not None else (self.loc or "")


class TutoriatTutor(Base):
    """Un tutor din echipa unui tutoriat; `principal` e cel scris primul (contactul)."""

    __tablename__ = "TUTORIAT_TUTOR"

    tutoriat_id: Mapped[int] = mapped_column(
        "ID_TUTORIAT", ForeignKey("TUTORIAT.ID", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        "ID_USER", ForeignKey("USER.ID", ondelete="CASCADE"), primary_key=True, index=True
    )
    principal: Mapped[bool] = mapped_column(
        "PRINCIPAL", Boolean, nullable=False, default=False, server_default="0"
    )

    tutoriat: Mapped[Tutoriat] = relationship(back_populates="echipa")
    user: Mapped[User] = relationship()


class TutoriatAnulare(Base):
    """O sedinta anume a unui tutoriat care nu se tine."""

    __tablename__ = "TUTORIAT_ANULARE"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    tutoriat_id: Mapped[int] = mapped_column(
        "ID_TUTORIAT", ForeignKey("TUTORIAT.ID", ondelete="CASCADE"), nullable=False, index=True
    )
    data: Mapped[date] = mapped_column("DATA", Date, nullable=False)
    motiv: Mapped[str | None] = mapped_column("MOTIV", String(200))
    de_catre: Mapped[str] = mapped_column("DE_CATRE", String(120), nullable=False)

    tutoriat: Mapped[Tutoriat] = relationship(back_populates="anulari")

    __table_args__ = (UniqueConstraint("ID_TUTORIAT", "DATA", name="uq_anulare"),)


# ---------------------------------------------------------------------------
# Campanii de tutoriate (recrutare + planificare)
# ---------------------------------------------------------------------------


#: Starile unei campanii: ciorna (se construieste), deschisa (se aplica), inchisa (s-au
#: strans aplicatiile), planificata (s-au propus sesiuni), arhivata (semestru incheiat).
STARI_CAMPANIE = ("ciorna", "deschisa", "inchisa", "planificata", "arhivata")
#: Tipurile de intrebari din formularul dinamic. `grila_orar` e grila vizuala de
#: disponibilitate; raspunsul ei sta in `AplicatieTutor.disponibilitate`.
TIPURI_INTREBARE = ("text", "radio", "checkbox", "grila_orar")
#: Starile unei aplicatii de tutor.
STARI_APLICATIE = ("aplicata", "acceptata", "respinsa", "retrasa")
#: Starile unei sesiuni propuse de planificator.
STARI_SESIUNE = ("propusa", "confirmata", "respinsa")


class CampanieTutoriate(Base):
    """Un ciclu de recrutare si planificare a tutoriatelor, pe un semestru.

    Responsabilul o deschide, studentii aplica prin formularul dinamic, se formeaza echipele
    si planificatorul propune sesiuni. O sesiune confirmata devine un `Tutoriat` (in tabelele
    existente), ca sa apara pe orar si sa fie ales de studenti.
    """

    __tablename__ = "CAMPANIE_TUTORIATE"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    an_univ: Mapped[str] = mapped_column("AN_UNIV", String(9), nullable=False)
    semestru: Mapped[int] = mapped_column("SEMESTRU", Integer, nullable=False)
    nume: Mapped[str] = mapped_column("NUME", String(160), nullable=False)
    #: Fereastra in care studentii pot aplica.
    data_deschidere: Mapped[date] = mapped_column("DATA_DESCHIDERE", Date, nullable=False)
    data_inchidere: Mapped[date] = mapped_column("DATA_INCHIDERE", Date, nullable=False)
    #: Perioada in care se tin sesiunile planificate.
    data_inceput: Mapped[date] = mapped_column("DATA_INCEPUT", Date, nullable=False)
    data_sfarsit: Mapped[date] = mapped_column("DATA_SFARSIT", Date, nullable=False)
    #: Pragul minim de acoperire acceptat de planificator (procente), vezi schedulerul.
    indulgenta: Mapped[int] = mapped_column(
        "INDULGENTA", Integer, nullable=False, default=75, server_default="75"
    )
    min_tutori_echipa: Mapped[int] = mapped_column(
        "MIN_TUTORI_ECHIPA", Integer, nullable=False, default=1, server_default="1"
    )
    max_tutori_echipa: Mapped[int] = mapped_column(
        "MAX_TUTORI_ECHIPA", Integer, nullable=False, default=3, server_default="3"
    )
    #: Durata unei sesiuni, in ore (2 = doua ceasuri).
    durata_sesiune: Mapped[int] = mapped_column(
        "DURATA_SESIUNE", Integer, nullable=False, default=2, server_default="2"
    )
    stare: Mapped[str] = mapped_column(
        "STARE", String(16), nullable=False, default="ciorna", server_default="ciorna"
    )
    creat_de: Mapped[str] = mapped_column("CREAT_DE", String(120), nullable=False)
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)

    formular: Mapped[StructuraFormular | None] = relationship(
        back_populates="campanie", cascade="all, delete-orphan", uselist=False
    )
    posturi: Mapped[list[PosturiDeschise]] = relationship(
        back_populates="campanie", cascade="all, delete-orphan"
    )
    aplicatii: Mapped[list[AplicatieTutor]] = relationship(
        back_populates="campanie", cascade="all, delete-orphan"
    )
    echipe: Mapped[list[EchipaTutoriat]] = relationship(
        back_populates="campanie", cascade="all, delete-orphan"
    )
    sesiuni: Mapped[list[SesiuneTutoriat]] = relationship(
        back_populates="campanie", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "STARE IN ('ciorna','deschisa','inchisa','planificata','arhivata')",
            name="ck_campanie_stare",
        ),
        CheckConstraint("SEMESTRU IN (1,2)", name="ck_campanie_semestru"),
        CheckConstraint("DATA_DESCHIDERE <= DATA_INCHIDERE", name="ck_campanie_aplicare"),
        CheckConstraint("DATA_INCEPUT <= DATA_SFARSIT", name="ck_campanie_perioada"),
        CheckConstraint("INDULGENTA BETWEEN 0 AND 100", name="ck_campanie_indulgenta"),
        CheckConstraint("MIN_TUTORI_ECHIPA BETWEEN 1 AND 3", name="ck_campanie_min_tutori"),
        CheckConstraint("MAX_TUTORI_ECHIPA BETWEEN 1 AND 3", name="ck_campanie_max_tutori"),
        CheckConstraint("MIN_TUTORI_ECHIPA <= MAX_TUTORI_ECHIPA", name="ck_campanie_tutori"),
        CheckConstraint("DURATA_SESIUNE BETWEEN 1 AND 6", name="ck_campanie_durata"),
        Index("ix_campanie_an_sem", "AN_UNIV", "SEMESTRU"),
    )

    def __repr__(self) -> str:
        return f"<CampanieTutoriate sem{self.semestru} {self.an_univ} {self.nume!r}>"


class StructuraFormular(Base):
    """Intrebarile formularului dinamic al unei campanii, ca lista JSON ordonata.

    Fiecare intrebare: `{"cheie", "tip" ("text"|"radio"|"checkbox"|"grila_orar"),
    "text", "obligatoriu", "optiuni": [...]}`. `grila_orar` nu are optiuni; raspunsul ei e
    disponibilitatea pe grila saptamanala (ZILE x 8..20), tinuta in `AplicatieTutor`.
    """

    __tablename__ = "STRUCTURA_FORMULAR"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    campanie_id: Mapped[int] = mapped_column(
        "ID_CAMPANIE",
        ForeignKey("CAMPANIE_TUTORIATE.ID", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    intrebari: Mapped[str] = mapped_column("INTREBARI", Text, nullable=False, default="[]")
    creat_de: Mapped[str] = mapped_column("CREAT_DE", String(120), nullable=False)
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)

    campanie: Mapped[CampanieTutoriate] = relationship(back_populates="formular")

    def __repr__(self) -> str:
        return f"<StructuraFormular campanie={self.campanie_id}>"


class PosturiDeschise(Base):
    """O materie oferita intr-o campanie, pentru un public (specializari grupate).

    `specializari`/`ani`/`serii` sunt coduri separate prin virgula, ca la `Tutoriat`;
    potrivirea cu o grupa reutilizeaza `db.tutoriate.pentru_grupa`.
    """

    __tablename__ = "POSTURI_DESCHISE"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    campanie_id: Mapped[int] = mapped_column(
        "ID_CAMPANIE",
        ForeignKey("CAMPANIE_TUTORIATE.ID", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    materie: Mapped[str] = mapped_column("MATERIE", String(160), nullable=False)
    descriere: Mapped[str | None] = mapped_column("DESCRIERE", Text)
    specializari: Mapped[str | None] = mapped_column("SPECIALIZARI", String(200))
    ani: Mapped[str | None] = mapped_column("ANI", String(40))
    serii: Mapped[str | None] = mapped_column("SERII", String(300))
    nr_max_tutori: Mapped[int | None] = mapped_column("NR_MAX_TUTORI", Integer)
    creat_de: Mapped[str] = mapped_column("CREAT_DE", String(120), nullable=False)
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)

    campanie: Mapped[CampanieTutoriate] = relationship(back_populates="posturi")
    echipe: Mapped[list[EchipaTutoriat]] = relationship(back_populates="post")
    sesiuni: Mapped[list[SesiuneTutoriat]] = relationship(back_populates="post")

    @staticmethod
    def _lista(text: str | None) -> list[str]:
        return [x for x in (text or "").split(",") if x]

    @property
    def coduri(self) -> list[str]:
        return self._lista(self.specializari)

    @property
    def ani_alesi(self) -> list[str]:
        return self._lista(self.ani)

    @property
    def serii_alese(self) -> list[str]:
        return self._lista(self.serii)

    def __repr__(self) -> str:
        return f"<PosturiDeschise {self.materie!r}>"


class AplicatieTutor(Base):
    """Aplicatia unui student la o campanie (una pe campanie).

    `raspunsuri` = raspunsurile la intrebarile formularului (cheie -> valoare);
    `disponibilitate` = raspunsul la intrebarea `grila_orar` (liste de ore libere);
    `materii` = lista ORDONATA de posturi dorite (`{"post": id, "motiv": str|null}`),
    ordinea fiind prioritatea.
    """

    __tablename__ = "APLICATIE_TUTOR"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    campanie_id: Mapped[int] = mapped_column(
        "ID_CAMPANIE",
        ForeignKey("CAMPANIE_TUTORIATE.ID", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        "ID_USER", ForeignKey("USER.ID", ondelete="CASCADE"), nullable=False, index=True
    )
    raspunsuri: Mapped[str | None] = mapped_column("RASPUNSURI", Text)
    disponibilitate: Mapped[str | None] = mapped_column("DISPONIBILITATE", Text)
    materii: Mapped[str | None] = mapped_column("MATERII", Text)
    preferinte_co_tutori: Mapped[str | None] = mapped_column("PREFERINTE_CO_TUTORI", Text)
    stare: Mapped[str] = mapped_column(
        "STARE", String(12), nullable=False, default="aplicata", server_default="aplicata"
    )
    creat_de: Mapped[str] = mapped_column("CREAT_DE", String(120), nullable=False)
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)

    campanie: Mapped[CampanieTutoriate] = relationship(back_populates="aplicatii")
    user: Mapped[User] = relationship()

    __table_args__ = (
        UniqueConstraint("ID_CAMPANIE", "ID_USER", name="uq_aplicatie_campanie_user"),
        CheckConstraint(
            "STARE IN ('aplicata','acceptata','respinsa','retrasa')", name="ck_aplicatie_stare"
        ),
    )

    def __repr__(self) -> str:
        return f"<AplicatieTutor user={self.user_id} campanie={self.campanie_id}>"


class EchipaTutoriat(Base):
    """O echipa de 1-3 tutori (studenti acceptati), asignata la un post."""

    __tablename__ = "ECHIPA_TUTORIAT"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    campanie_id: Mapped[int] = mapped_column(
        "ID_CAMPANIE",
        ForeignKey("CAMPANIE_TUTORIATE.ID", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    post_id: Mapped[int] = mapped_column(
        "ID_POST",
        ForeignKey("POSTURI_DESCHISE.ID", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    nume: Mapped[str | None] = mapped_column("NUME", String(160))
    stare: Mapped[str] = mapped_column(
        "STARE", String(12), nullable=False, default="propusa", server_default="propusa"
    )
    creat_de: Mapped[str] = mapped_column("CREAT_DE", String(120), nullable=False)
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)

    campanie: Mapped[CampanieTutoriate] = relationship(back_populates="echipe")
    post: Mapped[PosturiDeschise] = relationship(back_populates="echipe")
    membri: Mapped[list[EchipaTutoriatMembru]] = relationship(
        back_populates="echipa",
        cascade="all, delete-orphan",
        order_by="EchipaTutoriatMembru.ordine",
    )
    sesiuni: Mapped[list[SesiuneTutoriat]] = relationship(back_populates="echipa")

    __table_args__ = (
        CheckConstraint("STARE IN ('propusa','confirmata')", name="ck_echipa_stare"),
    )

    def __repr__(self) -> str:
        return f"<EchipaTutoriat {self.id} post={self.post_id}>"


class EchipaTutoriatMembru(Base):
    """Un tutor dintr-o echipa; `principal` e contactul, `ordine` tine ordinea afisarii."""

    __tablename__ = "ECHIPA_TUTORIAT_MEMBRU"

    echipa_id: Mapped[int] = mapped_column(
        "ID_ECHIPA", ForeignKey("ECHIPA_TUTORIAT.ID", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        "ID_USER", ForeignKey("USER.ID", ondelete="CASCADE"), primary_key=True
    )
    principal: Mapped[bool] = mapped_column(
        "PRINCIPAL", Boolean, nullable=False, default=False, server_default="0"
    )
    ordine: Mapped[int] = mapped_column(
        "ORDINE", Integer, nullable=False, default=0, server_default="0"
    )

    echipa: Mapped[EchipaTutoriat] = relationship(back_populates="membri")
    user: Mapped[User] = relationship()

    __table_args__ = (Index("ix_echipa_membru_user", "ID_USER"),)

    def __repr__(self) -> str:
        return f"<EchipaTutoriatMembru echipa={self.echipa_id} user={self.user_id}>"


class SesiuneTutoriat(Base):
    """O sesiune propusa de planificator, pentru o echipa la un post.

    Confirmata, devine un `Tutoriat` (legat prin `tutoriat_id`), care apare pe orar si poate
    fi ales de studenti. `acoperire` si `explicatie` sunt scrise de planificator.
    """

    __tablename__ = "SESIUNE_TUTORIAT"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    campanie_id: Mapped[int] = mapped_column(
        "ID_CAMPANIE",
        ForeignKey("CAMPANIE_TUTORIATE.ID", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    post_id: Mapped[int] = mapped_column(
        "ID_POST",
        ForeignKey("POSTURI_DESCHISE.ID", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    echipa_id: Mapped[int] = mapped_column(
        "ID_ECHIPA",
        ForeignKey("ECHIPA_TUTORIAT.ID", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    zi_saptamana: Mapped[str] = mapped_column("ZI_SAPTAMANA", String(10), nullable=False)
    ora_inceput: Mapped[time] = mapped_column("ORA_INCEPUT", Time, nullable=False)
    ora_sfarsit: Mapped[time] = mapped_column("ORA_SFARSIT", Time, nullable=False)
    sala_id: Mapped[int | None] = mapped_column("SALA", ForeignKey("SALA.ID_SALA", ondelete="SET NULL"))
    loc: Mapped[str | None] = mapped_column("LOC", String(160))
    acoperire: Mapped[int | None] = mapped_column("ACOPERIRE", Integer)
    explicatie: Mapped[str | None] = mapped_column("EXPLICATIE", Text)
    stare: Mapped[str] = mapped_column(
        "STARE", String(12), nullable=False, default="propusa", server_default="propusa"
    )
    tutoriat_id: Mapped[int | None] = mapped_column(
        "ID_TUTORIAT", ForeignKey("TUTORIAT.ID", ondelete="SET NULL")
    )
    creat_de: Mapped[str] = mapped_column("CREAT_DE", String(120), nullable=False)
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)

    campanie: Mapped[CampanieTutoriate] = relationship(back_populates="sesiuni")
    post: Mapped[PosturiDeschise] = relationship(back_populates="sesiuni")
    echipa: Mapped[EchipaTutoriat] = relationship(back_populates="sesiuni")
    sala: Mapped[Sala | None] = relationship()
    tutoriat: Mapped[Tutoriat | None] = relationship()

    __table_args__ = (
        CheckConstraint("STARE IN ('propusa','confirmata','respinsa')", name="ck_sesiune_stare"),
        CheckConstraint("ORA_INCEPUT < ORA_SFARSIT", name="ck_sesiune_ore"),
    )

    def __repr__(self) -> str:
        return f"<SesiuneTutoriat {self.zi_saptamana} {self.ora_inceput:%H:%M}-{self.ora_sfarsit:%H:%M}>"


# ---------------------------------------------------------------------------
# Orarul propriu: evenimente personale si activitati importate
# ---------------------------------------------------------------------------


class EvenimentPersonal(Base):
    """Un eveniment personal, pus de cineva pe orarul lui. Fie saptamanal (zi + ore), fie cu
    data fixa; frecventa SI/SP se aplica doar celor saptamanale."""

    __tablename__ = "EVENIMENT_PERSONAL"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        "ID_USER", ForeignKey("USER.ID", ondelete="CASCADE"), nullable=False, index=True
    )
    titlu: Mapped[str] = mapped_column("TITLU", String(160), nullable=False)
    loc: Mapped[str | None] = mapped_column("LOC", String(160))
    #: Exact una dintre ele: zi_saptamana (saptamanal) sau data (eveniment cu data fixa).
    zi_saptamana: Mapped[str | None] = mapped_column("ZI_SAPTAMANA", String(10))
    data: Mapped[date | None] = mapped_column("DATA", Date)
    ora_inceput: Mapped[time] = mapped_column("ORA_INCEPUT", Time, nullable=False)
    ora_sfarsit: Mapped[time] = mapped_column("ORA_SFARSIT", Time, nullable=False)
    #: SI / SP: doar in saptamanile impare / pare. Gol: in fiecare saptamana.
    frecventa: Mapped[str | None] = mapped_column("FRECVENTA", String(4))
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)

    user: Mapped[User] = relationship()

    __table_args__ = (
        CheckConstraint(
            "(ZI_SAPTAMANA IS NULL) != (DATA IS NULL)", name="ck_eveniment_personal_zi"
        ),
        CheckConstraint("ORA_INCEPUT < ORA_SFARSIT", name="ck_eveniment_personal_ore"),
        CheckConstraint(
            "FRECVENTA IS NULL OR FRECVENTA IN ('SI','SP')", name="ck_eveniment_personal_frecv"
        ),
    )

    def __repr__(self) -> str:
        return f"<EvenimentPersonal {self.titlu!r}>"


class ActivitateImportata(Base):
    """O activitate din alta grupa/serie, copiata pe orarul propriu al unui cont.

    E copie (snapshot), nu referinta la ORA: orele se reincarca la fiecare orar nou, iar
    importul trebuie sa ramana exact ce a vazut persoana cand l-a facut."""

    __tablename__ = "ACTIVITATE_IMPORTATA"

    id: Mapped[int] = mapped_column("ID", Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        "ID_USER", ForeignKey("USER.ID", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Formatiunea de la care a fost importata (ex. "244" sau "Seria 24").
    sursa: Mapped[str] = mapped_column("SURSA", String(120), nullable=False)
    materie: Mapped[str] = mapped_column("MATERIE", String(160), nullable=False)
    tip: Mapped[str | None] = mapped_column("TIP", String(40))
    profesor: Mapped[str | None] = mapped_column("PROFESOR", String(120))
    sala: Mapped[str | None] = mapped_column("SALA", String(120))
    zi_saptamana: Mapped[str] = mapped_column("ZI_SAPTAMANA", String(10), nullable=False)
    ora_inceput: Mapped[time] = mapped_column("ORA_INCEPUT", Time, nullable=False)
    ora_sfarsit: Mapped[time] = mapped_column("ORA_SFARSIT", Time, nullable=False)
    frecventa: Mapped[str | None] = mapped_column("FRECVENTA", String(4))
    saptamani: Mapped[str | None] = mapped_column("SAPTAMANI", String(120))
    creat_la: Mapped[datetime] = mapped_column("CREAT_LA", DateTime, nullable=False)

    user: Mapped[User] = relationship()

    __table_args__ = (
        CheckConstraint("ORA_INCEPUT < ORA_SFARSIT", name="ck_activitate_importata_ore"),
    )

    def __repr__(self) -> str:
        return f"<ActivitateImportata {self.materie!r} de la {self.sursa!r}>"
