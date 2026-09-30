"""Aplicatia web."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

# Configurarea locala (`ORAR_SECRET`, `ORAR_ADMIN_USER`, `ORAR_ADMIN_PAROLA`...) sta in `.env`,
# care nu intra in git. Se incarca inaintea a tot ce citeste mediul; variabilele deja setate
# in mediu au intaietate.
load_dotenv()

from orar.web.auth import cheie_secreta, cont_curent  # noqa: E402
from orar.web.deps import AICI, context_saptamana, templates  # noqa: E402
from orar.web.routers import (  # noqa: E402
    admin,
    cont,
    editare,
    evenimente,
    grupa,
    index,
    profesor,
    sala,
    sesizari,
    statistici,
)


@asynccontextmanager
async def ciclu_de_viata(_app: FastAPI) -> AsyncIterator[None]:
    """Porneste verificarea zilnica, daca e activata cu `ORAR_SCHEDULER=1`."""
    from orar.worker.scheduler import porneste

    planificator = porneste()
    try:
        yield
    finally:
        if planificator is not None:
            planificator.shutdown(wait=False)


app = FastAPI(
    title="Orarul Sălilor - FMI",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=ciclu_de_viata,
    # Pe toata aplicatia: fiecare cerere isi afla contul o singura data, prin `get_db`,
    # si il lasa in `request.state` pentru bara de sus.
    dependencies=[Depends(cont_curent)],
)
app.mount("/static", StaticFiles(directory=str(AICI / "static")), name="static")

# Sesiunea semnata tine autentificarea si tokenul CSRF.
# `same_site="lax"` face ca un POST venit de pe alt sit sa nu primeasca deloc cookie-ul --
# prima linie de aparare impotriva CSRF; tokenul din formulare e a doua.
# `https_only` se activeaza cu ORAR_HTTPS=1: pe http local ar face cookie-ul inutilizabil.
app.add_middleware(
    SessionMiddleware,
    secret_key=cheie_secreta(),
    session_cookie="orar_sesiune",
    same_site="lax",
    https_only=os.getenv("ORAR_HTTPS", "").strip().lower() in ("1", "true", "da", "yes"),
    max_age=60 * 60 * 24 * 30,
)

app.include_router(index.router)
app.include_router(grupa.router)
app.include_router(sala.router)
app.include_router(profesor.router)
app.include_router(admin.router)
app.include_router(cont.router)
app.include_router(sesizari.router)
app.include_router(editare.router)
app.include_router(evenimente.router)
app.include_router(statistici.router)


@app.exception_handler(404)
async def not_found(request: Request, _exc: object) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="404.html",
        context=context_saptamana(),
        status_code=404,
    )


__all__ = ["app"]
