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

__all__ = ["porneste", "ruleaza_o_data", "ACTIV", "ORA_VERIFICARE"]

#: Ora la care verificam (local). Noaptea, ca un eventual ingest sa nu prinda varful de trafic.
ORA_VERIFICARE = int(os.getenv("ORAR_ORA_VERIFICARE", "4"))
#: Planificatorul porneste doar daca variabila e setata (vezi docstring).
ACTIV = os.getenv("ORAR_SCHEDULER", "").strip().lower() in ("1", "true", "da", "yes")


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
