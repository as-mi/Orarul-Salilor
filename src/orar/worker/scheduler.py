"""Verificarea periodica a orarului, in procesul web.

De ce zilnic si nu mai des
--------------------------
Orarul se schimba de cateva ori pe semestru. Verificarea e o cerere HTTP catre pagina FMI,
deci ieftina; **ingestul** care urmeaza doar daca s-a schimbat ceva dureaza ~10 minute
(captura a 100 de pagini + OCR). O data pe zi acopera nevoia fara sa incarcam nici site-ul
facultatii, nici masina.

De ce nu porneste implicit
--------------------------
Cu mai multe procese `uvicorn` (workers), fiecare ar porni propriul planificator si ar
captura in paralel in acelasi director. Deci pornirea e explicita: `ORAR_SCHEDULER=1`, sau
-- mai curat in productie -- un timer systemd care ruleaza `orar sincronizeaza`. Vezi README.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

__all__ = ["porneste", "porneste_articole", "ruleaza_o_data", "ACTIV", "ORA_VERIFICARE"]

#: Ora la care verificam (local). Noaptea, ca un eventual ingest sa nu prinda varful de trafic.
ORA_VERIFICARE = int(os.getenv("ORAR_ORA_VERIFICARE", "4"))
#: Planificatorul porneste doar daca variabila e setata (vezi docstring).
ACTIV = os.getenv("ORAR_SCHEDULER", "").strip().lower() in ("1", "true", "da", "yes")
#: Aducerea orara a articolului ASMI porneste implicit, independenta de sincronizarea
#: orarului. `ORAR_ARTICOLE=0` o opreste (testele o opresc, ca sa nu atinga asmi.ro si
#: baza reala).
ARTICOLE_ACTIV = os.getenv("ORAR_ARTICOLE", "1").strip().lower() not in ("0", "false", "no", "off")


def ruleaza_o_data(**kwargs) -> None:  # noqa: ANN003
    """O verificare completa, cu sesiune proprie. Nu arunca: e un job de fundal."""
    from orar.db.session import sesiune
    from orar.worker.sync import sincronizeaza

    inceput = datetime.now()
    try:
        with sesiune() as s:
            raport = sincronizeaza(s, **kwargs)
        durata = (datetime.now() - inceput).total_seconds()
        if raport.ingestate:
            log.info("sincronizare in %.0fs: %s", durata, "; ".join(raport.ingestate))
        else:
            log.info("sincronizare in %.0fs: nimic de reluat", durata)
        for a in raport.avertismente:
            log.warning("sincronizare: %s", a)
    except Exception:
        # O eroare de retea sau o schimbare de layout la Drive nu are voie sa opreasca
        # aplicatia web; ramane in log si se reincearca maine.
        log.exception("sincronizarea a esuat")
    # Ultimul articol ASMI se aduce separat: nu depinde de sincronizarea orarului.
    _prospateaza_articole()


def _prospateaza_articole() -> None:
    """Aduce ultimul articol de pe asmi.ro, intr-o sesiune proprie."""
    from orar.db.articole import prospateaza_articole
    from orar.db.session import sesiune

    try:
        with sesiune() as s:
            prospateaza_articole(s)
    except Exception:
        log.exception("aducerea articolului ASMI a esuat")


def porneste(**kwargs):  # noqa: ANN003, ANN201
    """Pornește planificatorul, daca e activat. Intoarce planificatorul sau None."""
    if not ACTIV:
        log.debug("planificatorul e oprit (ORAR_SCHEDULER nesetat)")
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.warning("apscheduler nu e instalat; ruleaza `orar sincronizeaza` din cron")
        return None

    planificator = BackgroundScheduler(timezone="Europe/Bucharest")
    planificator.add_job(
        ruleaza_o_data,
        CronTrigger(hour=ORA_VERIFICARE, minute=0),
        kwargs=kwargs,
        id="sincronizare-orar",
        # Daca procesul a fost oprit peste ora programata, nu vrem sa porneasca deodata
        # mai multe capturi cand revine.
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    planificator.start()
    log.info("planificator pornit: verificare zilnica la ora %d", ORA_VERIFICARE)
    return planificator


def porneste_articole():
    """Porneste (implicit, mereu) aducerea ultimului articol ASMI: o data la pornire, apoi orar.

    Spre deosebire de sincronizarea orarului -- scumpa si cu stare pe proces -- aducerea
    articolului e o cerere HTTP ieftina si idempotenta: suprascrie aceeasi linie din baza.
    Deci poate rula in fiecare worker, in paralel, fara griji. E independenta de
    `ORAR_SCHEDULER`; se opreste doar cu `ORAR_ARTICOLE=0` (in teste).
    """
    if not ARTICOLE_ACTIV:
        log.debug("aducerea articolului ASMI e oprita (ORAR_ARTICOLE=0)")
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        log.warning("apscheduler nu e instalat; ultimul articol ASMI nu se improspateaza orar")
        return None

    planificator = BackgroundScheduler(timezone="Europe/Bucharest")
    planificator.add_job(
        _prospateaza_articole,
        IntervalTrigger(hours=1),
        id="articol-asmi",
        coalesce=True,
        max_instances=1,
    )
    # si o data imediat, ca subsolul sa aiba articolul cat mai repede dupa pornire
    planificator.add_job(_prospateaza_articole)
    planificator.start()
    log.info("articolul ASMI se improspateaza la pornire si apoi orar")
    return planificator
