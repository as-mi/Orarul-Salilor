"""Dependinte partajate de rute: sesiune, sabloane, contextul saptamanii.

Traiesc separat de `app.py` ca routerele sa le poata importa fara import circular.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from orar.db.session import get_sessionmaker
from orar.domain.weeks import AncoraSaptamana, CalendarAcademic, Paritate

AICI = Path(__file__).resolve().parent


def _context_sesiune(request) -> dict[str, object]:  # noqa: ANN001
    """Ce are nevoie *fiecare* pagina: tokenul CSRF si, pentru bara de sus, contul curent.

    Cheia e `cont`, nu `user`: procesoarele de context ale lui Starlette se aplica **peste**
    contextul rutei. Nu deschide el sesiune la baza: valoarea o pune `auth.cont_curent`, care
    ruleaza ca dependinta pe toata aplicatia si foloseste `get_db` -- deci si testele care
    inlocuiesc baza vad acelasi lucru ca rutele.
    """
    if "session" not in request.scope:
        return {"cont": None, "csrf": ""}
    from orar.web.auth import token_csrf

    return {"cont": getattr(request.state, "cont", None), "csrf": token_csrf(request)}


templates = Jinja2Templates(
    directory=str(AICI / "templates"), context_processors=[_context_sesiune]
)


def _static_versionat(request, path: str) -> str:  # noqa: ANN001
    """URL-ul unui fisier static, cu data modificarii lui in query (`orar.css?v=...`).

    StaticFiles nu trimite `Cache-Control`, iar URL-ul ramane acelasi de la o versiune la
    alta, asa ca Firefox isi pastreaza CSS-ul vechi dupa un update -- sablonul nou apare cu
    stilurile vechi. Cu versiunea in URL, orice modificare a fisierului e alt URL. Data se
    citeste la fiecare cerere (un `stat`), deci merge si cu `--reload`, fara repornire.
    """
    url = str(request.url_for("static", path=path))
    try:
        return f"{url}?v={int((AICI / 'static' / path).stat().st_mtime)}"
    except OSError:
        return url


templates.env.globals["static_versionat"] = _static_versionat

#: Ancorele publicate de FMI pentru semestrul II 2025-2026, ca rezerva. Sunt doua, la
#: distanta de doua saptamani calendaristice dar una academica -- vacanta de Paste dintre
#: ele. Cele reale vin din baza, unde le scrie `orar sincronizeaza`; astea raman pentru o
#: instalare in care watcher-ul n-a rulat inca, ca interfata sa nu porneasca fara calendar.
CALENDAR = CalendarAcademic(
    ancore=[
        AncoraSaptamana(inceput=date(2026, 4, 6), numar=7, paritate=Paritate.IMPARA),
        AncoraSaptamana(inceput=date(2026, 4, 20), numar=8, paritate=Paritate.PARA),
    ]
)


def get_db() -> Iterator[Session]:
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.close()


def calendar(s: Session | None = None) -> CalendarAcademic:
    """Calendarul academic: din baza daca watcher-ul a rulat, altfel cel de rezerva."""
    if s is None:
        return CALENDAR
    from orar.worker.sync import calendar_din_baza

    return calendar_din_baza(s) or CALENDAR


def context_saptamana(zi: date | None = None, s: Session | None = None) -> dict[str, object]:
    """Numarul si paritatea saptamanii pentru o data (implicit azi)."""
    # Alta zi decat azi, aleasa din selectorul de data: il marcheaza si ofera "azi", iar
    # filtrele o pastreaza in linkurile lor.
    zi_aleasa = zi if zi and zi != date.today() else None
    zi = zi or date.today()
    sapt = calendar(s).saptamana(zi)
    return {
        "azi": zi,
        "zi_aleasa": zi_aleasa,
        "saptamana": sapt,
        "numar_saptamana": sapt.numar if sapt else None,
        "paritate": sapt.paritate if sapt else None,
        "in_semestru": sapt is not None,
        "sursa_orar": sursa_curenta(s),
    }


def sursa_curenta(s: Session | None):  # noqa: ANN201
    """Orarul de grupe incarcat cel mai de curand, ca sa aratam cat de proaspete sunt datele.
    Paginile de orar il inlocuiesc cu sursa orarului afisat -- vezi `context_versiuni`."""
    if s is None:
        return None
    from sqlalchemy import select

    from orar.db.models import SursaOrar

    return s.scalar(
        select(SursaOrar)
        .where(SursaOrar.fel == "grupe")
        .order_by(SursaOrar.actualizat.desc().nullslast())
        .limit(1)
    )


def saptamana_activa(ctx: dict[str, object]):
    """Saptamana de folosit la filtrarea grilei, sau None cand nu se poate filtra.

    In vacanta nu exista numar academic, deci orice filtrare ar stinge activitati la
    intamplare. Atunci nu filtram deloc si aratam orarul complet.
    """
    return ctx.get("saptamana")


@dataclass
class Selectie:
    """Ce orar arata pagina.

    Trei cazuri: orarul implicit (nimic in adresa), alt orar viu (`?perioada=ID`) sau o
    versiune anterioara a unuia (`?versiune=N`). `perioada` e orarul viu -- la o versiune
    anterioara, cel caruia ii apartine, daca mai exista.
    """

    perioada: object | None  # orar.db.models.Perioada
    versiune: object | None = None  # orar.db.models.VersiuneOrar
    implicita: bool = True

    @property
    def q(self) -> str:
        """Parametrul care pastreaza alegerea in linkuri; gol pentru orarul implicit."""
        if self.versiune is not None:
            return f"versiune={self.versiune.id}"
        if self.perioada is not None and not self.implicita:
            return f"perioada={self.perioada.id}"
        return ""

    @property
    def perioada_id(self) -> int | None:
        """Pentru interogarile pe orarul viu. -1 pe o baza fara niciun orar: nu se potriveste
        cu nimic, in loc sa insemne "toate perioadele"."""
        return self.perioada.id if self.perioada is not None else -1

    @property
    def versiune_id(self) -> int | None:
        return self.versiune.id if self.versiune is not None else None

    @property
    def an_universitar(self) -> str | None:
        if self.versiune is not None:
            return self.versiune.an_univ
        return self.perioada.an_univ if self.perioada is not None else None


def selectie(s: Session, perioada: int | None = None, versiune: int | None = None) -> Selectie:
    """Orarul cerut prin `?perioada=` / `?versiune=`, sau cel implicit. 404 daca nu exista."""
    from fastapi import HTTPException
    from sqlalchemy import select

    from orar.db.models import Perioada, VersiuneOrar
    from orar.db.orare import orare_vii

    vii = orare_vii(s)
    implicita = vii[0].perioada if vii else None

    if versiune is not None:
        v = s.get(VersiuneOrar, versiune)
        if v is None:
            raise HTTPException(status_code=404, detail=f"Nu există versiunea {versiune}")
        a_ei = s.scalar(
            select(Perioada).where(Perioada.an_univ == v.an_univ, Perioada.semestru == v.semestru)
        )
        return Selectie(perioada=a_ei, versiune=v, implicita=False)

    if perioada is not None:
        aleasa = next((o.perioada for o in vii if o.perioada.id == perioada), None)
        if aleasa is None:
            raise HTTPException(status_code=404, detail=f"Nu există orarul {perioada}")
        return Selectie(perioada=aleasa, implicita=aleasa is implicita)

    return Selectie(perioada=implicita)


@dataclass
class OptiuneOrar:
    """Un rand din panoul de versiuni: un orar viu sau o versiune anterioara."""

    eticheta: str
    #: Data publicarii, daca se stie.
    data: object | None
    #: Ce se pune in adresa ca sa fie ales (gol pentru orarul implicit).
    q: str
    activa: bool
    implicita: bool = False
    #: Doar la versiunile anterioare: diferentele fata de orarul viu afisat.
    arhiva: object | None = None


def context_versiuni(s: Session, sel: Selectie, arhive: list) -> dict[str, object]:  # noqa: ANN001
    """Ce au nevoie panoul de versiuni (`_versiuni.html`), bannerul si linkurile, pe orice
    pagina: toate orarele vii, apoi versiunile lor anterioare."""
    from sqlalchemy import select

    from orar.db.models import SursaOrar
    from orar.db.orare import orare_vii

    optiuni = [
        OptiuneOrar(
            eticheta=o.eticheta,
            data=o.publicat,
            q="" if o.implicit else f"perioada={o.perioada.id}",
            activa=sel.versiune is None and sel.perioada is o.perioada,
            implicita=o.implicit,
        )
        for o in orare_vii(s)
    ]
    optiuni += [
        OptiuneOrar(
            eticheta=f"Semestrul {a.versiune.semestru} · {a.versiune.an_univ}",
            data=a.versiune.publicat or a.versiune.arhivat_la,
            q=f"versiune={a.versiune.id}",
            activa=sel.versiune is a.versiune,
            arhiva=a,
        )
        for a in arhive
    ]
    # subsolul spune de cand e orarul *afisat*, nu cel mai recent incarcat
    sursa = None
    if sel.perioada is not None and sel.versiune is None:
        sursa = s.scalar(
            select(SursaOrar).where(
                SursaOrar.fel == "grupe",
                SursaOrar.an_univ == sel.perioada.an_univ,
                SursaOrar.semestru == sel.perioada.semestru,
            )
        )
    return {
        "sel": sel,
        "sel_q": sel.q,
        "versiune": sel.versiune,
        "optiuni_orar": optiuni,
        "sursa_orar": sursa,
    }


def context_editare(request, cont, ore, *, editare: bool, versiune) -> dict[str, object]:  # noqa: ANN001
    """Ce au nevoie paginile de orar pentru sesizari si, la admini, pentru editare.

    `mod_editare`: in grila, un click pe o activitate deschide formularul ei in loc sa duca
    la sala / la grupa. Doar pentru admini si doar pe orarul actual -- o versiune anterioara
    nu se editeaza.
    """
    from urllib.parse import quote

    from orar.db.corectii import descriere

    poate_edita = bool(cont and cont.e_admin and versiune is None)
    aici = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    zile = {z: i for i, z in enumerate(("Luni", "Marti", "Miercuri", "Joi", "Vineri"))}
    ordonate = sorted(ore, key=lambda o: (zile.get(o.zi_saptamana, 9), o.ora_inceput, o.id))
    return {
        "poate_edita": poate_edita,
        "mod_editare": poate_edita and editare,
        "aici": quote(aici, safe=""),
        "ore_raport": [] if versiune else [(o.id, descriere(o)) for o in ordonate],
    }
