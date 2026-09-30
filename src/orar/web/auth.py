"""Conturi: parole, sesiune, cine e autentificat, CSRF.

Doua feluri de conturi, acelasi formular de intrare (`/login`):

  - **conturile din baza** (tabela USER), create la `/signup`. Au un rol -- `student`
    implicit; `voluntar`, `profesor` sau `admin` il da un admin, din /admin -- o grupa si
    preferintele orarului propriu;
  - **adminul principal, din mediu**: `ORAR_ADMIN_USER` si `ORAR_ADMIN_PAROLA` (sau
    `ORAR_ADMIN_PAROLA_HASH`, in formatul de mai jos). Nu sta in baza, deci nu poate fi
    retrogradat sau sters din panou si exista chiar pe o baza goala. Fara aceste variabile,
    el pur si simplu nu exista.

Orarul ramane public: `cont_curent` nu blocheaza nimic, intoarce `None` si paginile merg
mai departe. Doar /admin cere autentificare (`cere_admin`).

Parolele
--------
`hashlib.scrypt`, din biblioteca standard -- fara inca o dependinta care sa aiba nevoie de
compilator. Parametrii se scriu **in hash**, nu doar in cod: cand vom vrea sa-i crestem,
conturile vechi raman verificabile.

Formatul: `scrypt$n$r$p$sare_hex$hash_hex`.

Ce **nu** face modulul asta
---------------------------
Nu limiteaza incercarile de autentificare. Daca aplicatia ajunge expusa public, limitarea
trebuie adaugata in fata (nginx, fail2ban) -- contul de admin e exact tinta unui atac cu
dictionar.

Mesajul de eroare e acelasi pentru "nu exista contul" si "parola gresita": altfel formularul
ar spune oricui ce adrese sunt inregistrate.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orar.db.models import User
from orar.web.deps import get_db

log = logging.getLogger(__name__)

__all__ = [
    "PAROLA_MINIM",
    "Cont",
    "autentifica",
    "cere_admin",
    "cheie_secreta",
    "cont_curent",
    "deconecteaza",
    "hash_parola",
    "logheaza",
    "normalizeaza_email",
    "token_csrf",
    "verifica_csrf",
    "verifica_parola",
]

#: Parametrii scrypt de azi. N=2^15 cere ~32 MB si zeci de milisecunde -- destul de scump
#: pentru un atac cu dictionar, destul de ieftin pentru o autentificare.
_N, _R, _P = 2**15, 8, 1

#: Sub atat nu acceptam parola unui cont nou. Lungimea bate complexitatea; nu cerem simboluri.
PAROLA_MINIM = 10

_CHEIE_USER = "user_id"
_CHEIE_ADMIN_MEDIU = "admin_mediu"
_CHEIE_CSRF = "csrf"


def _memorie(n: int, r: int, p: int) -> int:
    """Plafonul de memorie cerut lui OpenSSL.

    Implicit el permite 32 MB, iar scrypt cu N=2^15 si r=8 are nevoie de 128*N*r = 33.5 MB,
    deci fara plafonul asta functia arunca "memory limit exceeded". Il calculam din parametri
    si ii lasam loc, ca sa mearga si hash-urile vechi cand vom creste N.
    """
    return 128 * n * r * max(1, p) * 2


def cheie_secreta() -> str:
    """Cheia cu care se semneaza cookie-ul de sesiune.

    Din `ORAR_SECRET`. Fara ea generam una la pornire -- comod in dezvoltare, dar sesiunile
    se pierd la fiecare repornire si nu merge cu mai multe procese, deci avertizam.
    """
    cheie = os.getenv("ORAR_SECRET", "").strip()
    if cheie:
        return cheie
    log.warning(
        "ORAR_SECRET nu e setat: generez o cheie temporara. Sesiunile se pierd la repornire "
        "si nu sunt valabile intre procese. In productie: ORAR_SECRET=$(openssl rand -hex 32)"
    )
    return secrets.token_hex(32)


# --------------------------------------------------------------------- parole


def hash_parola(parola: str) -> str:
    sare = secrets.token_bytes(16)
    brut = hashlib.scrypt(parola.encode(), salt=sare, n=_N, r=_R, p=_P, maxmem=_memorie(_N, _R, _P))
    return f"scrypt${_N}${_R}${_P}${sare.hex()}${brut.hex()}"


def verifica_parola(parola: str, stocat: str | None) -> bool:
    """Compara in timp constant. `stocat` gol => False, fara sa arunce."""
    if not stocat:
        return False
    try:
        schema, n, r, p, sare_hex, hash_hex = stocat.split("$")
        if schema != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        brut = hashlib.scrypt(
            parola.encode(),
            salt=bytes.fromhex(sare_hex),
            n=n,
            r=r,
            p=p,
            maxmem=_memorie(n, r, p),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(brut.hex(), hash_hex)


def normalizeaza_email(email: str) -> str:
    return email.strip().lower()


# ---------------------------------------------------------------- cine esti


@dataclass(frozen=True)
class Cont:
    """Cine e autentificat. `user` e None pentru adminul din mediu."""

    nume: str
    rol: str
    user: User | None = None

    @property
    def e_admin(self) -> bool:
        return self.rol == "admin"


def _admin_din_mediu() -> tuple[str, str, str] | None:
    """(nume, parola in clar, hash) din mediu -- una dintre ultimele doua e goala."""
    nume = os.getenv("ORAR_ADMIN_USER", "").strip()
    parola = os.getenv("ORAR_ADMIN_PAROLA", "")
    stocat = os.getenv("ORAR_ADMIN_PAROLA_HASH", "").strip()
    if not nume or not (parola or stocat):
        return None
    return nume, parola, stocat


def _e_adminul_din_mediu(identificator: str, parola: str) -> Cont | None:
    admin = _admin_din_mediu()
    if admin is None:
        return None
    nume, parola_mediu, stocat = admin
    # Ambele comparatii se fac mereu si in timp constant, ca durata sa nu spuna care a picat.
    nume_ok = hmac.compare_digest(identificator.strip().encode(), nume.encode())
    if stocat:
        parola_ok = verifica_parola(parola, stocat)
    else:
        parola_ok = hmac.compare_digest(parola.encode(), parola_mediu.encode())
    return Cont(nume=nume, rol="admin") if nume_ok and parola_ok else None


#: Un hash valid, pentru adresele care nu exista: verificarea dureaza la fel, deci timpul de
#: raspuns nu spune daca adresa e inregistrata.
_HASH_DE_UMPLUTURA = hash_parola(secrets.token_urlsafe(16))


def autentifica(s: Session, identificator: str, parola: str) -> Cont | None:
    """Contul, daca parola se potriveste. Altfel None -- fara sa spunem care parte a picat.

    `identificator` e emailul unui cont din baza sau numele adminului din mediu.
    """
    if admin := _e_adminul_din_mediu(identificator, parola):
        return admin
    user = s.scalar(select(User).where(func.lower(User.email) == normalizeaza_email(identificator)))
    if user is None:
        verifica_parola(parola, _HASH_DE_UMPLUTURA)
        return None
    if not verifica_parola(parola, user.parola_hash):
        return None
    return Cont(nume=user.nume, rol=user.rol, user=user)


# ------------------------------------------------------------------- sesiunea


def logheaza(request: Request, cont: Cont) -> None:
    """Leaga sesiunea de cont. Regenereaza tokenul CSRF, ca sa nu fie refolosit."""
    request.session.clear()
    if cont.user is not None:
        request.session[_CHEIE_USER] = cont.user.id
    else:
        request.session[_CHEIE_ADMIN_MEDIU] = cont.nume
    request.session[_CHEIE_CSRF] = secrets.token_urlsafe(24)


def deconecteaza(request: Request) -> None:
    request.session.clear()


def cont_curent(request: Request, s: Session = Depends(get_db)) -> Cont | None:
    """Contul din sesiune, sau None. Nu blocheaza: paginile publice raman publice.

    Ruleaza ca dependinta pe toata aplicatia, deci lasa in `request.state.cont` ce are
    nevoie bara de sus -- altfel fiecare sablon ar trebui sa-l care prin context. Rolul se
    citeste din baza la fiecare cerere: o schimbare facuta de un admin se vede imediat, fara
    ca utilizatorul sa iasa si sa intre din nou.
    """
    request.state.cont = None
    if "session" not in request.scope:
        return None

    cont: Cont | None = None
    if nume := request.session.get(_CHEIE_ADMIN_MEDIU):
        admin = _admin_din_mediu()
        # Sesiunea e valabila doar cat timp mediul numeste acelasi admin.
        if admin is not None and admin[0] == nume:
            cont = Cont(nume=nume, rol="admin")
    elif (uid := request.session.get(_CHEIE_USER)) is not None:
        user = s.get(User, uid)
        if user is not None:
            cont = Cont(nume=user.nume, rol=user.rol, user=user)

    if cont is None and (_CHEIE_ADMIN_MEDIU in request.session or _CHEIE_USER in request.session):
        # Contul a fost sters (sau adminul scos din mediu); sesiunea nu mai inseamna nimic.
        request.session.clear()
    request.state.cont = cont
    return cont


def cere_admin(request: Request, cont: Cont | None = Depends(cont_curent)) -> Cont:
    """Dependinta pentru paginile de admin: fara cont trimite la autentificare, iar un cont
    fara rol de admin primeste 403."""
    if cont is None:
        inapoi = quote(request.url.path, safe="/")
        raise HTTPException(status_code=303, headers={"Location": f"/login?inapoi={inapoi}"})
    if not cont.e_admin:
        raise HTTPException(status_code=403, detail="Pagina e doar pentru administratori.")
    return cont


# ---------------------------------------------------------------------- CSRF


def token_csrf(request: Request) -> str:
    """Tokenul din sesiune, creat la prima cerere care are nevoie de el."""
    token = request.session.get(_CHEIE_CSRF)
    if not token:
        token = secrets.token_urlsafe(24)
        request.session[_CHEIE_CSRF] = token
    return token


def verifica_csrf(request: Request, trimis: str | None) -> bool:
    """Cookie-ul e `SameSite=Lax`, deci un POST din alt sit nici nu l-ar primi. Tokenul e
    a doua incuietoare, pentru cazurile in care browserul e mai permisiv decat credem."""
    asteptat = request.session.get(_CHEIE_CSRF)
    return bool(asteptat and trimis and hmac.compare_digest(asteptat, trimis))
