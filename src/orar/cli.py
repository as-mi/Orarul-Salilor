"""Linia de comanda: `orar <subcomanda>`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from orar.db.session import cale_db, sesiune


def _pagini_din_imagini(director: Path, referinta: Path | None) -> list[dict]:
    """Segmenteaza si citeste fiecare PNG din director, in formatul asteptat de `load`."""
    from orar.ingest.ocr import RapidOCR, citeste_fisier
    from orar.ingest.segment import EroareSegmentare

    lex = _lexicon(referinta)
    motor = RapidOCR()
    pagini: list[dict] = []
    # Recursiv: `orar sincronizeaza` pune capturile in subdirectoare, cate unul pe sursa.
    for cale in sorted(director.rglob("*.png")):
        try:
            citita = citeste_fisier(cale, motor, lex)
        except EroareSegmentare as e:
            print(f"  {cale.name}: sarita ({e})")
            continue
        sursa = str(cale.relative_to(director))
        pagini.append({**citita.ca_json(cu_provenienta=True), "_source": sursa})
    print(f"{len(pagini)} pagini citite din {director}")
    return pagini


def _cmd_load(args: argparse.Namespace) -> int:
    from orar.ingest.consolidate import consolideaza
    from orar.ingest.load import incarca_din_json, incarca_pagini

    with sesiune() as s:
        if args.fisier.is_dir():
            pagini = _pagini_din_imagini(args.fisier, args.referinta)
            rap = incarca_pagini(s, pagini, an_universitar=args.an, semestru=args.semestru)
        else:
            rap = incarca_din_json(s, args.fisier, an_universitar=args.an, semestru=args.semestru)
        print(rap)
        if not args.fara_consolidare:
            print("\n--- consolidare (activitati de serie) ---")
            cons = consolideaza(s, an_universitar=args.an)
            print(cons)
            if cons.exemple and args.verbose:
                print("  exemple:")
                for e in cons.exemple:
                    print(f"    {e}")
            if cons.suspecte and args.verbose:
                print("\n  durate contradictorii in sursa (erori de extragere):")
                for e in cons.suspecte:
                    print(f"    {e}")
    if rap.avertismente and args.verbose:
        print("\n--- avertismente ---")
        for a in rap.avertismente:
            print(f"  {a}")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    cale = cale_db()
    if cale.exists():
        if not args.da:
            raspuns = input(f"Sterg {cale}? [y/N] ")
            if raspuns.strip().lower() not in ("y", "yes", "d", "da"):
                print("Anulat.")
                return 1
        cale.unlink()
        for sufix in ("-wal", "-shm"):
            (cale.parent / (cale.name + sufix)).unlink(missing_ok=True)
        print(f"Sters {cale}")
    else:
        print(f"{cale} nu exista.")
    return 0


def _cmd_captureaza(args: argparse.Namespace) -> int:
    from orar.ingest.capture import EroareCaptura, OptiuniCaptura, captureaza_orar

    optiuni = OptiuniCaptura(
        director=args.director,
        scala=args.scala,
        headless=not args.cu_fereastra,
        limita=args.limita,
    )
    try:
        rez = captureaza_orar(args.url, optiuni)
    except EroareCaptura as e:
        print(f"[eroare] {e}")
        return 1

    print(f"pagini capturate : {len(rez.pagini)}")
    print(f"dintre care noi  : {rez.cate_noi}")
    if rez.pagini:
        p = rez.pagini[0]
        print(f"rezolutie        : {p.latime}x{p.inaltime}")
    for a in rez.avertismente:
        print(f"  ! {a}")
    return 0


def _cmd_segmenteaza(args: argparse.Namespace) -> int:
    from orar.ingest.segment import ZILE, EroareSegmentare, segmenteaza_fisier

    for cale in args.imagini:
        try:
            pag = segmenteaza_fisier(cale)
        except EroareSegmentare as e:
            print(f"{cale.name}: [eroare] {e}")
            continue
        print(f"{cale.name}: {len(pag.celule)} activitati")
        for zi in ZILE:
            celule = pag.pe_zi(zi)
            if not celule:
                continue
            detalii = ", ".join(f"{c.ore}(b{c.banda})" for c in celule)
            print(f"   {zi:9} {detalii}")
    return 0


def _lexicon(cale_json: Path | None):  # noqa: ANN001
    """Vocabularul din baza; daca baza e goala, din JSON-ul de referinta."""
    from orar.ingest.lexicon import Lexicon

    with sesiune() as s:
        lex = Lexicon.din_baza(s)
    if lex.materii or not cale_json:
        return lex
    print(f"[info] baza e goala; iau vocabularul din {cale_json}")
    return Lexicon.din_json(cale_json)


def _cmd_citeste(args: argparse.Namespace) -> int:
    from orar.ingest.lexicon import Lexicon
    from orar.ingest.ocr import RapidOCR, citeste_fisier

    lex = Lexicon.din_json(args.referinta) if args.fara_baza else _lexicon(args.referinta)
    motor = RapidOCR()
    for cale in args.imagini:
        pag = citeste_fisier(cale, motor, lex)
        print(f"\n=== {cale.name}: {pag.titlu!r} ({pag.scor_titlu:.2f}) ===")
        for a in pag.activitati:
            semne = f" !{','.join(a.nesigure)}" if a.nesigure else ""
            print(
                f"  {a.zi:9} {a.ore:6} {a.incredere:4.2f}{semne:12} "
                f"{a.profesor:24} {a.materie:22} ({a.tip}"
                f"{', ' + a.frecventa if a.frecventa else ''})"
                f"{' [' + a.saptamani + ']' if a.saptamani else ''} "
                f"{a.semigrupa:10} {a.sala}"
            )
            if args.verbose:
                print(f"      brut: {a.brut}")
    return 0


def _cmd_evalueaza(args: argparse.Namespace) -> int:
    from orar.ingest.evaluate import CAMPURI, evalueaza
    from orar.ingest.lexicon import Lexicon
    from orar.ingest.ocr import RapidOCR

    imagini = sorted(args.director.rglob("*.png"))
    if not imagini:
        print(f"[eroare] niciun PNG in {args.director}")
        return 1
    lex = Lexicon.din_json(args.referinta) if args.fara_baza else _lexicon(args.referinta)
    raport = evalueaza(imagini, args.referinta, RapidOCR(), lex)
    print(raport)
    if raport.pagini_nepotrivite:
        print("\npagini fara corespondent in referinta:")
        for p in raport.pagini_nepotrivite:
            print(f"  {p}")
    if args.verbose:
        print("\ncele mai dese diferente (asteptat -> citit):")
        for (camp, astept, citit), n in raport.diferente.most_common(40):
            print(f"  {n:4}x {camp:10} {astept!r:28} -> {citit!r}")
    praguri = {"sala": 0.99, "tip": 0.99, "frecventa": 0.99, "semigrupa": 0.99}
    praguri |= {"materie": 0.97, "profesor": 0.95, "ore": 0.99, "saptamani": 0.97}
    sub_prag = [c for c in CAMPURI if raport.acuratete(c) < praguri[c]]
    if sub_prag:
        print(f"\nsub pragul din plan: {', '.join(sub_prag)}")
    return 0


def _cmd_sincronizeaza(args: argparse.Namespace) -> int:
    from orar.worker.sync import sincronizeaza

    with sesiune() as s:
        rap = sincronizeaza(
            s,
            an_universitar=args.an,
            semestru=args.semestru,
            director=args.director,
            forteaza=args.forteaza,
            doar_verifica=args.doar_verifica,
        )
        print(rap)
    return 0 if not rap.avertismente else 0


def _cmd_crosscheck(args: argparse.Namespace) -> int:
    from orar.ingest.crosscheck import (
        citeste_orarul_profesorilor,
        completeaza_profesorii,
        extinde_numele,
        salveaza_index,
        verifica,
    )
    from orar.ingest.ocr import RapidOCR

    if not args.director.is_dir():
        print(
            f"[eroare] {args.director} nu exista; ruleaza intai `orar captureaza <link profesori>`"
        )
        return 1

    with sesiune() as s:
        index = citeste_orarul_profesorilor(args.director, RapidOCR(), _lexicon(args.referinta))
        rap = verifica(s, index)
        print(rap)
        if args.verbose:
            for eticheta, lista in (
                ("divergente de profesor", rap.divergente),
                ("neconfirmate", rap.neconfirmate),
                ("nume necunoscute in baza", rap.nume_noi),
            ):
                if lista:
                    print(f"\n--- {eticheta} ({len(lista)}) ---")
                    for x in lista[:40]:
                        print(f"  {x}")
        if args.completeaza:
            schimbate = completeaza_profesorii(s, index)
            print(f"\ncampuri nesigure completate din a doua sursa: {len(schimbate)}")
            for x in schimbate:
                print(f"  {x}")
            extinse, ambigue = extinde_numele(s, index)
            print(f"prescurtari inlocuite cu numele intreg: {len(extinse)}")
            if args.verbose:
                for x in extinse[:40]:
                    print(f"  {x}")
            if ambigue:
                print(f"prescurtari ambigue, lasate cum sunt: {len(ambigue)}")
                for x in ambigue[:20]:
                    print(f"  {x}")
        if args.pastreaza:
            an, semestru = args.pastreaza
            n = salveaza_index(s, index, an_universitar=an, semestru=int(semestru))
            print(f"orarul profesorilor pastrat pentru sem {semestru}, {an}: {n} profesori")
    return 0


def _cmd_capacitati(args: argparse.Namespace) -> int:
    from orar.ingest.capacities import citeste_capacitati, incarca_capacitati
    from orar.ingest.ocr import RapidOCR

    if not args.imagine.is_file():
        print(f"[eroare] {args.imagine} nu exista; ruleaza intai `orar sincronizeaza`")
        return 1
    capacitati = citeste_capacitati(args.imagine, RapidOCR())
    with sesiune() as s:
        rap = incarca_capacitati(s, capacitati)
        print(rap)
        for a in rap.avertismente:
            print(f"  ! {a}")
        if args.verbose:
            if rap.adaugate:
                print(f"\nin tabel, fara ore in orar -- adaugate: {rap.adaugate}")
            if rap.fara_capacitate:
                print(f"\nsali fizice fara numar de locuri: {rap.fara_capacitate}")
    return 0


def _cmd_planuri(args: argparse.Namespace) -> int:
    from orar.ingest.plans import (
        descarca_planuri,
        incarca_planuri,
        parseaza_director,
    )

    if args.descarca:
        print(f"descarc planurile in {args.director} ...")
        descarcate = descarca_planuri(args.director)
        print(f"  {len(descarcate)} PDF-uri")
    if not args.director.is_dir():
        print(f"[eroare] {args.director} nu exista; ruleaza cu --descarca")
        return 1

    rap = parseaza_director(args.director)
    print(rap)
    for a in rap.avertismente:
        print(f"  ! {a}")
    if not rap.discipline:
        return 1

    with sesiune() as s:
        inc = incarca_planuri(s, rap.discipline)
        print()
        print(inc)
        if args.verbose:
            if inc.ambigue:
                print("\n--- ambigue ---")
                for x in inc.ambigue:
                    print(f"  {x}")
            if inc.fara_corespondent:
                print(f"\n--- fara corespondent ({len(inc.fara_corespondent)}) ---")
                for x in inc.fara_corespondent[:40]:
                    print(f"  {x}")
    return 0


def _cmd_surse(_args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from orar.db.models import AncoraSaptamana, SursaOrar

    with sesiune() as s:
        randuri = list(s.scalars(select(SursaOrar).order_by(SursaOrar.semestru.desc())))
        if not randuri:
            print("nicio sursa cunoscuta; ruleaza `orar sincronizeaza --doar-verifica`")
        for r in randuri:
            stare = "la zi" if r.la_zi else "de reluat"
            print(
                f"sem {r.semestru} {r.fel:10} {stare:10} publicat={r.actualizat or '-'} "
                f"ingestat={r.ingestat_la or '-'}\n    {r.url}"
            )
        ancore = list(s.scalars(select(AncoraSaptamana).order_by(AncoraSaptamana.inceput)))
        if ancore:
            print("\nancore de saptamana:")
            for a in ancore:
                print(f"  {a.inceput}  sapt {a.numar} ({a.paritate})")
    return 0


def _cmd_stats(_args: argparse.Namespace) -> int:
    from sqlalchemy import func, select

    from orar.db.models import Grupa, Materie, Ora, Profesor, Sala

    with sesiune() as s:
        for model, eticheta in (
            (Ora, "ore"),
            (Grupa, "noduri GRUPA"),
            (Profesor, "profesori"),
            (Materie, "materii"),
            (Sala, "sali"),
        ):
            n = s.scalar(select(func.count()).select_from(model))
            print(f"{eticheta:14} {n}")
        print("\npe tip de nod:")
        for tip, n in s.execute(
            select(Grupa.tip, func.count()).group_by(Grupa.tip).order_by(func.count().desc())
        ):
            print(f"  {tip:14} {n}")
    return 0


def _cmd_rol(args: argparse.Namespace) -> int:
    """Seteaza rolul unui cont, dupa email -- acelasi lucru ca din panoul /admin, pentru cand
    n-ai (inca) un admin care sa intre in panou."""
    from sqlalchemy import func, select

    from orar.db.models import ROLURI, User

    if args.rol not in ROLURI:
        print(f"[eroare] rol necunoscut: {args.rol!r}; alege dintre {', '.join(ROLURI)}")
        return 1
    with sesiune() as s:
        user = s.scalar(select(User).where(func.lower(User.email) == args.email.strip().lower()))
        if user is None:
            print(f"[eroare] nu exista niciun cont cu adresa {args.email}")
            return 1
        user.rol = args.rol
        print(f"{user.nume} <{user.email}>: {args.rol}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="orar", description="Orarul Salilor (FMI)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    #: Vocabularul de pornire, cand baza e goala.
    ref = Path("tests/golden/date.json")

    pl = sub.add_parser(
        "load", help="incarca in baza un JSON de orar, sau un director de pagini capturate"
    )
    pl.add_argument("fisier", type=Path, help="fisier .json sau director cu PNG-uri")
    pl.add_argument(
        "--referinta",
        type=Path,
        default=Path("tests/golden/date.json"),
        help="vocabular de pornire pentru OCR, cand baza e goala",
    )
    pl.add_argument("--an", default="2025-2026")
    pl.add_argument("--semestru", type=int, default=2, choices=(1, 2))
    pl.add_argument(
        "--fara-consolidare",
        action="store_true",
        help="nu ridica activitatile comune la nodul de serie (util la depanare)",
    )
    pl.set_defaults(func=_cmd_load)

    pr = sub.add_parser("reset", help="sterge baza de date")
    pr.add_argument("--da", action="store_true", help="fara confirmare")
    pr.set_defaults(func=_cmd_reset)

    ps = sub.add_parser("stats", help="cate randuri sunt in baza")
    ps.set_defaults(func=_cmd_stats)

    prl = sub.add_parser("rol", help="seteaza rolul unui cont (student/voluntar/profesor/admin)")
    prl.add_argument("email", help="adresa contului")
    prl.add_argument("rol", help="student, voluntar, profesor sau admin")
    prl.set_defaults(func=_cmd_rol)

    py = sub.add_parser(
        "sincronizeaza", help="verifica pagina FMI si reia ingestul daca s-a schimbat orarul"
    )
    py.add_argument("--an", default=None, help="implicit: anul universitar in care suntem")
    py.add_argument(
        "--semestru", type=int, choices=(1, 2), help="implicit: cel tinut la zi de facultate"
    )
    py.add_argument("-o", "--director", type=Path, default=Path("data/screenshots"))
    py.add_argument("--forteaza", action="store_true", help="reia chiar daca nu s-a schimbat")
    py.add_argument(
        "--doar-verifica", action="store_true", help="spune ce s-ar face, fara sa captureze"
    )
    py.set_defaults(func=_cmd_sincronizeaza)

    pu = sub.add_parser("surse", help="ce orare cunoastem si daca sunt la zi")
    pu.set_defaults(func=_cmd_surse)

    pcap = sub.add_parser(
        "capacitati", help="citeste numarul de locuri din tabelul de pe pagina 2 a orarului"
    )
    pcap.add_argument(
        "imagine",
        type=Path,
        nargs="?",
        default=Path("data/screenshots/sem2-grupe/pag_002.png"),
    )
    pcap.set_defaults(func=_cmd_capacitati)

    pp = sub.add_parser(
        "planuri", help="incarca creditele si forma de evaluare din planurile de invatamant"
    )
    pp.add_argument("director", type=Path, nargs="?", default=Path("data/planuri"))
    pp.add_argument("--descarca", action="store_true", help="ia intai PDF-urile de pe Drive")
    pp.set_defaults(func=_cmd_planuri)

    px = sub.add_parser(
        "crosscheck", help="compara baza cu orarul profesorilor (a doua sursa pentru aceleasi ore)"
    )
    px.add_argument(
        "director", type=Path, nargs="?", default=Path("data/screenshots/sem2-profesori")
    )
    px.add_argument("--referinta", type=Path, default=ref)
    px.add_argument(
        "--completeaza",
        action="store_true",
        help="umple profesorii marcati nesigure, cand a doua sursa da un raspuns unic",
    )
    px.add_argument(
        "--pastreaza",
        nargs=2,
        metavar=("AN", "SEMESTRU"),
        help="pastreaza in baza orarul profesorilor citit (lista de profesori si activitatile "
        "lor), pentru semestrul dat: --pastreaza 2025-2026 2",
    )
    px.set_defaults(func=_cmd_crosscheck)

    pc = sub.add_parser("captureaza", help="descarca paginile orarului din Google Drive")
    pc.add_argument("url", help="linkul bit.ly de pe pagina FMI, sau linkul Drive")
    pc.add_argument("-o", "--director", type=Path, default=Path("data/screenshots"))
    pc.add_argument("--scala", type=int, default=4, help="device scale factor (implicit 4)")
    pc.add_argument("--cu-fereastra", action="store_true", help="ruleaza browserul vizibil")
    pc.add_argument("--limita", type=int, help="opreste-te dupa N pagini (proba rapida)")
    pc.set_defaults(func=_cmd_captureaza)

    pg = sub.add_parser("segmenteaza", help="detecteaza celulele dintr-o imagine de orar")
    pg.add_argument("imagini", type=Path, nargs="+")
    pg.set_defaults(func=_cmd_segmenteaza)

    pci = sub.add_parser("citeste", help="segmenteaza + OCR pe una sau mai multe pagini")
    pci.add_argument("imagini", type=Path, nargs="+")
    pci.add_argument("--referinta", type=Path, default=ref, help="JSON pentru vocabular")
    pci.add_argument("--fara-baza", action="store_true", help="ia vocabularul doar din JSON")
    pci.set_defaults(func=_cmd_citeste)

    pe = sub.add_parser("evalueaza", help="acuratetea OCR fata de setul de referinta")
    # Implicit orarul *grupelor*: setul de referinta e al lor. Directorul cu orarul
    # profesorilor n-are corespondent acolo si ar iesi 191 de "pagini nepotrivite".
    pe.add_argument("director", type=Path, nargs="?", default=Path("data/screenshots/sem2-grupe"))
    pe.add_argument("--referinta", type=Path, default=ref)
    pe.add_argument("--fara-baza", action="store_true", help="ia vocabularul doar din JSON")
    pe.set_defaults(func=_cmd_evalueaza)

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s"
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
