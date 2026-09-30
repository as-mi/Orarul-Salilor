"""Orarul de statistici, pentru admini: cati oameni au ore in fiecare interval si cat de
pline sunt salile, cu filtre pe public, pe activitati si pe sali -- vezi `db/statistici.py`.

Filtrele stau toate in adresa, deci o configurare se poate trimite cuiva ca link sau salva
cu nume (`FILTRU_SALVAT`) si redeschide dintr-un click.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from orar.db.models import FiltruSalvat, Grupa, Sala
from orar.db.orare import orare_vii
from orar.db.statistici import METRICI, Filtre, calculeaza, grupe_de_baza
from orar.domain.grid import ORA_MAX, ORA_MIN, ZILE
from orar.domain.hierarchy import DENUMIRI_SPECIALIZARE
from orar.web.auth import Cont, cere_admin, verifica_csrf
from orar.web.deps import context_saptamana, get_db, templates

router = APIRouter(prefix="/admin/statistici", tags=["statistici"])

TIPURI = (("curs", "Cursuri"), ("seminar", "Seminare"), ("lab", "Laboratoare"), ("proiect", "Proiecte"), ("alta", "Altele"))  # fmt: skip
FELURI_SALA = (("amf", "Amfiteatre"), ("l", "Laboratoare"), ("s", "Săli de seminar"))
#: Parametrii care tin de afisare, nu de filtre: nu intra intr-o configurare salvata.
DOAR_AFISARE = ("celula", "salvat")


def _intre(valoare: int, minim: int, maxim: int) -> int:
    return max(minim, min(maxim, valoare))


@router.get("", response_class=HTMLResponse)
def statistici(
    request: Request,
    perioada: int | None = Query(None),
    metrica: str = Query("persoane"),
    specializari: list[str] = Query([]),
    ani: list[str] = Query([]),
    serii: list[str] = Query([]),
    grupe: list[str] = Query([]),
    tipuri: list[str] = Query([]),
    optionale: bool = Query(False),
    zile: list[str] = Query([]),
    ora_de_la: int = Query(ORA_MIN),
    ora_pana_la: int = Query(ORA_MAX),
    profesor: str = Query(""),
    materie: str = Query(""),
    feluri_sala: list[str] = Query([]),
    etaje: list[str] = Query([]),
    sali: list[str] = Query([]),
    saptamana: str = Query(""),
    marime_semigrupa: int = Query(15),
    marime_grupa: int = Query(30),
    prag_plin: int = Query(100),
    celula: str = Query("", description="celula deschisa: `Luni-10`"),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> HTMLResponse:
    vii = orare_vii(s)
    orar = next((o.perioada for o in vii if o.perioada.id == perioada), None) or (
        vii[0].perioada if vii else None
    )
    if metrica not in dict(METRICI):
        metrica = "persoane"
    f = Filtre(
        specializari=specializari,
        ani=ani,
        serii=serii,
        grupe=grupe,
        tipuri=tipuri,
        optionale=optionale,
        zile=[z for z in zile if z in ZILE],
        ora_de_la=_intre(ora_de_la, ORA_MIN, ORA_MAX - 1),
        ora_pana_la=_intre(ora_pana_la, ORA_MIN + 1, ORA_MAX),
        profesor=profesor.strip(),
        materie=materie.strip(),
        feluri_sala=feluri_sala,
        etaje=etaje,
        sali=sali,
        saptamana=saptamana if saptamana in ("SI", "SP") or saptamana.isdigit() else "",
        marime_semigrupa=_intre(marime_semigrupa, 1, 500),
        marime_grupa=_intre(marime_grupa, 1, 500),
        prag_plin=_intre(prag_plin, 1, 300),
    )
    if f.ora_pana_la <= f.ora_de_la:
        f.ora_de_la, f.ora_pana_la = ORA_MIN, ORA_MAX

    st = calculeaza(s, orar, f) if orar else None
    de_baza = grupe_de_baza(s, orar.an_univ) if orar else []
    coduri = {g.specializare for g in de_baza if g.specializare}
    sali_fizice = list(s.scalars(select(Sala).where(Sala.tip == "fizica").order_by(Sala.nume)))

    # adresa fara parametrii de afisare: ce se salveaza si de unde pornesc linkurile celulelor
    parametri = [(k, v) for k, v in parse_qsl(request.url.query) if k not in DOAR_AFISARE and v]
    deschisa = None
    if st and celula:
        zi, _, ora = celula.partition("-")
        deschisa = st.celule.get((zi, int(ora))) if ora.isdigit() else None

    return templates.TemplateResponse(
        request=request,
        name="admin_statistici.html",
        context={
            **context_saptamana(),
            "orare": vii,
            "orar": orar,
            "st": st,
            "f": f,
            "metrica": metrica,
            "metrici": METRICI,
            "maxim": st.maxim(metrica) if st else 0,
            "deschisa": deschisa,
            "q": urlencode(parametri),
            "q_fara_metrica": urlencode([(k, v) for k, v in parametri if k != "metrica"]),
            "filtre_salvate": list(s.scalars(select(FiltruSalvat).order_by(FiltruSalvat.nume))),
            # optiunile filtrelor
            "specializari": [
                (c, DENUMIRI_SPECIALIZARE.get(c, c))
                for c in (
                    *[c for c in DENUMIRI_SPECIALIZARE if c in coduri],
                    *sorted(coduri - set(DENUMIRI_SPECIALIZARE)),
                )
            ],
            "ani": [
                (a, f"{'Master' if a[0] == 'M' else 'Licență'} · anul {a[1:]}")
                for a in sorted({g.an for g in de_baza if g.an})
            ],
            "serii": sorted(
                {(g.serie, g.serie_nume) for g in de_baza if g.serie}, key=lambda x: x[1]
            ),
            "grupe": de_baza,
            "tipuri": TIPURI,
            "zile": ZILE,
            "ore": list(range(ORA_MIN, ORA_MAX + 1)),
            "feluri_sala": FELURI_SALA,
            "etaje": sorted(
                {next((c for c in x.nume if c.isdigit()), "") for x in sali_fizice} - {""}
            ),
            "sali": sali_fizice,
            "nr_sali_total": len(sali_fizice),
            "nr_filtre": sum(1 for k, _ in parametri if k not in ("metrica", "perioada")),
            "nume_grupe": {g.id: g.nume for g in s.scalars(select(Grupa))} if deschisa else {},
        },
    )


@router.post("/filtre")
def salveaza_filtrul(
    request: Request,
    nume: str = Form(""),
    parametri: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    admin: Cont = Depends(cere_admin),
) -> RedirectResponse:
    """Salveaza configurarea curenta de filtre, cu nume. Acelasi nume o inlocuieste."""
    inapoi = f"/admin/statistici?{parametri}" if parametri else "/admin/statistici"
    nume = " ".join(nume.split())[:80]
    if not verifica_csrf(request, csrf) or not nume:
        return RedirectResponse(inapoi, status_code=303)
    # doar perechi cheie=valoare curate, recompuse de noi: nimic din afara nu ajunge in adresa
    curat = urlencode([(k, v) for k, v in parse_qsl(parametri) if k not in DOAR_AFISARE and v])
    filtru = s.scalar(select(FiltruSalvat).where(FiltruSalvat.nume == nume))
    if filtru is None:
        filtru = FiltruSalvat(nume=nume, creat_de=admin.nume, creat_la=datetime.now(), parametri="")
        s.add(filtru)
    filtru.parametri = curat
    s.commit()
    return RedirectResponse(f"{inapoi}{'&' if parametri else '?'}salvat=1", status_code=303)


@router.post("/filtre/{filtru_id}/sterge")
def sterge_filtrul(
    request: Request,
    filtru_id: int,
    inapoi: str = Form(""),
    csrf: str = Form(""),
    s: Session = Depends(get_db),
    _admin: Cont = Depends(cere_admin),
) -> RedirectResponse:
    tinta = f"/admin/statistici?{urlencode(parse_qsl(inapoi))}" if inapoi else "/admin/statistici"
    if verifica_csrf(request, csrf):
        filtru = s.get(FiltruSalvat, filtru_id)
        if filtru is None:
            raise HTTPException(404, "filtrul nu exista")
        s.delete(filtru)
        s.commit()
    return RedirectResponse(tinta, status_code=303)
