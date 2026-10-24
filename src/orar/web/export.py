"""Exportul orarului ca imagine (PNG/JPG), desenat cu Pillow din datele `Grila`.

Printul/PDF-ul se face in browser (`window.print()` + `static/css/print.css`); aici e doar
partea de imagine. Ruta de export reia **aceeasi** `Grila` pe care o randeaza `_grila.html`,
ca imaginea sa arate ca pagina, doar fara interactivitate. Culorile sunt copiate din tema
deschisa (baza.css, evenimente.css, tutoriate.css), ca exportul sa fie mereu lizibil.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from fastapi.responses import Response
from PIL import Image, ImageColor, ImageDraw, ImageFont

from orar.domain.grid import Bloc, Grila, Provenienta

__all__ = ["imagine_grila", "bytes_imagine", "nume_fisier", "raspuns_export"]

# Culorile temei deschise (baza.css / evenimente.css / tutoriate.css).
_TEXT = "#14161c"
_TEXT_SLAB = "#5d6472"
_LINIE = "#e3e6ec"
_LINIE_TARE = "#c8cdd8"
_FUNDAL = "#ffffff"
_SUPRAFATA_2 = "#f4f5f8"

#: `clasa_tip` din `Bloc` -> (fundal, linie).
PALETA: dict[str, tuple[str, str]] = {
    "curs": ("#ffd9a8", "#e8a13c"),
    "seminar": ("#cdeac0", "#6aa84f"),
    "lab": ("#bcd8f5", "#4a8fd4"),
    "proiect": ("#e6cdf0", "#a568c4"),
    "alta": ("#e4e6eb", "#a8afbd"),
    "eveniment-asmi": ("#eaefff", "#2f5de6"),
    "eveniment-alta": ("#e4e6eb", "#a8afbd"),
    "eveniment-tutoriat": ("#e3f5ef", "#13856b"),
    "eveniment-personal": ("#eef2ff", "#6366f1"),
    "eveniment-importat": ("#fff4e6", "#d97706"),
}

# Dimensiuni, in pixeli.
_MARGIN = 16
_ETT_LAT = 84  # latimea coloanei cu numele zilei
_CAP_H = 34    # randul de cap cu orele
_BAND_H = 72   # inaltimea unei benzi
_COL_W = 86    # latimea unei coloane de o ora
_PAD = 4

# Fonturi TrueType cu diacritice romanesti, in ordinea probabilitatii. Pe sistemele fara
# niciuna se cade pe fontul bitmap din Pillow (fara diacritice).
_FAMILII = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans",
    "/usr/share/fonts/dejavu/DejaVuSans",
    "/usr/share/fonts/google-noto/NotoSans",
    "/usr/share/fonts/liberation-sans-fonts/LiberationSans",
    "/usr/share/fonts/google-carlito-fonts/Carlito",
    "/usr/share/fonts/google-droid-sans-fonts/DroidSans",
)


@cache
def _font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for baza in _FAMILII:
        cale = Path(f"{baza}-{'Bold' if bold else 'Regular'}.ttf")
        if cale.is_file():
            try:
                return ImageFont.truetype(str(cale), size)
            except OSError:
                continue
    if bold:  # fara varianta bold, regular e de preferat fontului bitmap
        return _font(size, False)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow vechi
        return ImageFont.load_default()


def _blend_hex(hexa: str, alb: str = _FUNDAL, t: float = 0.55) -> tuple[int, int, int]:
    """Culoarea `hexa`, estompata spre alb (pentru blocurile inactive)."""
    a = ImageColor.getrgb(hexa)
    b = ImageColor.getrgb(alb)
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _trunchiaza(d: ImageDraw.ImageDraw, text: str, font, maxw: int) -> str:
    if not text or d.textlength(text, font=font) <= maxw:
        return text
    while text and d.textlength(text + "…", font=font) > maxw:
        text = text[:-1]
    return text + "…"


def _text_bloc(b: Bloc, mod: str) -> tuple[str, str, str, str]:
    """(sus, mij, jos, sursa) -- textele unui bloc, oglindind `_grila.html`."""
    if b.eveniment is not None:
        e = b.eveniment
        if e.fel in ("personal", "importat"):
            sus = e.titlu
            piese = [p for p in (e.frecventa, e.saptamani) if p]
            piese.append(f"{e.ora_inceput:%H:%M}-{e.ora_sfarsit:%H:%M}")
            mij = " · ".join(piese)
            loc = e.loc or ""
            if e.fel == "importat" and e.sursa:
                loc = f"{loc} · " if loc else ""
                loc += f"importat de la {e.sursa}"
            prof = e.profesor or ""
            jos = f"{prof} · {loc}" if prof and loc else (prof or loc)
            return sus, mij, jos, ""

        # eveniment cu data: asmi / alta / tutoriat
        fel = {"asmi": "ASMI", "tutoriat": "tutoriat", "alta": "rezervare"}.get(e.fel, e.fel)
        etichete = [fel]
        if getattr(e, "anulat", False):
            etichete.append("anulat")
        elif getattr(e, "al_meu", False):
            etichete.append("ții tu")
        mij = " · ".join(etichete)
        mij += f" · {e.data_inceput:%d.%m} · {e.ora_inceput:%H:%M}-{e.ora_sfarsit:%H:%M}"
        loc = e.sala.nume if (mod == "grupa" and e.sala) else (e.loc or "")
        return e.titlu, mij, loc, ""

    o = b.ora
    sus = o.materie.nume if o.materie else "-"
    if o.semigrupa:
        sus += f" · {o.semigrupa.replace('_', ' ')}"
    mij = " · ".join(p for p in (o.tip_ora_materie, o.frecventa, o.saptamani) if p)
    prof = o.grupa.nume if mod == "profesor" else (o.profesor.nume if o.profesor else "")
    loc = (o.sala.nume if o.sala else "") if mod in ("grupa", "profesor") else o.grupa.nume
    jos = f"{prof} · {loc}" if prof and loc else (prof or loc)
    sursa = b.sursa if (mod == "grupa" and b.provenienta is not Provenienta.PROPRIE and b.sursa) else ""
    return sus, mij, jos, sursa


def _stil_provenienta(b: Bloc) -> tuple[str, int]:
    """(stil, grosime) pentru marginea stanga: discret, ca in `grila.css`."""
    if b.ora is None or b.provenienta is Provenienta.PROPRIE:
        return "solid", 3
    if b.provenienta is Provenienta.MOSTENITA:
        return "double", 2
    if b.provenienta is Provenienta.SEMIGRUPA:
        return "dashed", 3
    return "dotted", 3  # partajata


def _linie_stanga(d: ImageDraw.ImageDraw, x: int, y0: int, y1: int, culoare, stil: str, grosime: int) -> None:
    if stil == "double":
        d.line([(x, y0), (x, y1)], fill=culoare, width=grosime)
        d.line([(x + 3, y0), (x + 3, y1)], fill=culoare, width=grosime)
    elif stil in ("dashed", "dotted"):
        pas = 7 if stil == "dashed" else 4
        lung = 5 if stil == "dashed" else 2
        y = y0
        while y < y1:
            d.line([(x, y), (x, min(y + lung, y1))], fill=culoare, width=grosime)
            y += pas
    else:
        d.line([(x, y0), (x, y1)], fill=culoare, width=grosime)


@dataclass
class _Cheie:
    fel: str  # "sw" (culoarea tipului) | "prov" (doar margine) | "inactiv" | "sep"
    clasa: str
    text: str


def _chei_legenda(grila: Grila, mod: str, filtru_saptamana: bool) -> list[_Cheie]:
    chei = [
        _Cheie("sw", "curs", "curs"),
        _Cheie("sw", "seminar", "seminar"),
        _Cheie("sw", "lab", "laborator"),
        _Cheie("sw", "proiect", "proiect"),
    ]
    if grila.are_evenimente:
        chei += [_Cheie("sw", "eveniment-asmi", "activitate ASMI"), _Cheie("sw", "eveniment-alta", "altă activitate")]
    if grila.are_tutoriate:
        chei.append(_Cheie("sw", "eveniment-tutoriat", "tutoriat"))
    if grila.are_personale:
        chei += [_Cheie("sw", "eveniment-personal", "eveniment personal"), _Cheie("sw", "eveniment-importat", "importat")]
    if mod == "grupa":
        chei.append(_Cheie("sep", "", ""))
        chei += [
            _Cheie("prov", "mostenita", "moștenită de la serie/an"),
            _Cheie("prov", "semigrupa", "doar o semigrupă"),
            _Cheie("prov", "partajata", "opțional / comună"),
        ]
    if grila.inactive and not filtru_saptamana:
        chei.append(_Cheie("sep", "", ""))
        chei.append(_Cheie("inactiv", "alta", f"{grila.inactive} nu se țin săptămâna aceasta"))
    return chei


def _randuri_legenda(chei: list[_Cheie], d: ImageDraw.ImageDraw, f, latime_max: int) -> list[list[_Cheie]]:
    """Imparte cheile pe randuri, ca sa incapa in latimea imaginii."""
    randuri: list[list[_Cheie]] = [[]]
    x = 0
    for cheie in chei:
        lat = 8 if cheie.fel == "sep" else 12 + 6 + int(d.textlength(cheie.text, font=f)) + 12
        if randuri[-1] and x + lat > latime_max:
            randuri.append([])
            x = 0
        randuri[-1].append(cheie)
        x += lat
    return randuri


def _deseneaza_legenda(
    d: ImageDraw.ImageDraw, grila: Grila, mod: str, filtru_saptamana: bool, f, x0: int, y0: int, latime_max: int
) -> int:
    """Deseneaza legenda sub grila; intoarce inaltimea folosita."""
    randuri = _randuri_legenda(_chei_legenda(grila, mod, filtru_saptamana), d, f, latime_max)
    y = y0
    h_rand = 22
    for rand in randuri:
        x = x0
        for cheie in rand:
            if cheie.fel == "sep":
                d.line([(x + 4, y + 3), (x + 4, y + 19)], fill=_LINIE_TARE)
                x += 8
                continue
            fundal, linie = (_FUNDAL, PALETA["alta"][1]) if cheie.fel == "prov" else PALETA[cheie.clasa]
            if cheie.fel == "inactiv":
                fundal, linie = _blend_hex(fundal, _FUNDAL, 0.55), _blend_hex(linie, _FUNDAL, 0.3)
            d.rectangle([x, y + 3, x + 12, y + 15], fill=fundal, outline=linie)
            if cheie.fel == "prov":
                stil = {"mostenita": "double", "semigrupa": "dashed", "partajata": "dotted"}.get(cheie.clasa, "solid")
                _linie_stanga(d, x, y + 4, y + 14, linie, stil, 2)
            d.text((x + 18, y), cheie.text, font=f, fill=_TEXT_SLAB)
            x += 12 + 6 + int(d.textlength(cheie.text, font=f)) + 12
        y += h_rand
    return y - y0


def imagine_grila(
    grila: Grila,
    *,
    titlu: str,
    subtitlu: str = "",
    mod: str = "grupa",
    filtru_saptamana: bool = False,
) -> Image.Image:
    """Deseneaza grila ca imagine RGB (fundal alb)."""
    nr_coloane = grila.nr_coloane
    latime_grid = nr_coloane * _COL_W
    latime = _ETT_LAT + latime_grid + 2 * _MARGIN

    f_titlu = _font(22, bold=True)
    f_sub = _font(13)
    f_zi = _font(14, bold=True)
    f_ora = _font(13, bold=True)
    f_ora2 = _font(10)
    f_sus = _font(13, bold=True)
    f_mij = _font(11)
    f_jos = _font(11)
    f_legenda = _font(12)

    titlu_h = 32
    sub_h = 20 if subtitlu else 0
    inaltime_zile = sum(max(1, zi.nr_benzi) * _BAND_H for zi in grila.zile)

    # inaltimea legendei se afla printr-un pas de masurare, apoi se deseneaza pe imagine
    im_tmp = Image.new("RGB", (latime, 10), _FUNDAL)
    legenda_h = _deseneaza_legenda(ImageDraw.Draw(im_tmp), grila, mod, filtru_saptamana, f_legenda, _MARGIN, 0, latime - 2 * _MARGIN)

    inaltime = _MARGIN + titlu_h + sub_h + 10 + _CAP_H + inaltime_zile + legenda_h + 10 + _MARGIN
    im = Image.new("RGB", (latime, inaltime), _FUNDAL)
    d = ImageDraw.Draw(im)

    y = _MARGIN
    d.text((_MARGIN, y), _trunchiaza(d, titlu, f_titlu, latime - 2 * _MARGIN), font=f_titlu, fill=_TEXT)
    y += titlu_h
    if subtitlu:
        d.text((_MARGIN, y), _trunchiaza(d, subtitlu, f_sub, latime - 2 * _MARGIN), font=f_sub, fill=_TEXT_SLAB)
        y += sub_h
    y += 10

    x0 = _MARGIN + _ETT_LAT
    grila_y0 = y

    # randul de cap cu orele
    d.rectangle([_MARGIN, y, x0, y + _CAP_H], fill=_SUPRAFATA_2)
    d.rectangle([x0, y, x0 + latime_grid, y + _CAP_H], fill=_SUPRAFATA_2)
    d.line([x0, y, x0, y + _CAP_H], fill=_LINIE_TARE)
    for i, h in enumerate(grila.ore):
        cx = x0 + i * _COL_W + _COL_W // 2
        d.text((cx, y + 4), str(h), font=f_ora, fill=_TEXT, anchor="ma")
        d.text((cx, y + _CAP_H - 3), f"{h:02d}:00", font=f_ora2, fill=_TEXT_SLAB, anchor="md")
        if i:
            d.line([(x0 + i * _COL_W, y), (x0 + i * _COL_W, y + _CAP_H)], fill=_LINIE)
    y += _CAP_H

    # zilele, fiecare cu eticheta in stanga si una sau mai multe benzi
    for zi in grila.zile:
        h_zi = zi.nr_benzi * _BAND_H
        d.line([(_MARGIN, y), (_MARGIN + _ETT_LAT + latime_grid, y)], fill=_LINIE_TARE, width=2)
        nume_zi = {"Sambata": "Sâmbătă", "Duminica": "Duminică"}.get(zi.zi, zi.zi)
        d.rectangle([_MARGIN, y, x0, y + h_zi], fill=_SUPRAFATA_2)
        d.text((_MARGIN + _ETT_LAT // 2, y + h_zi // 2), nume_zi, font=f_zi, fill=_TEXT_SLAB, anchor="mm")
        d.line([x0, y, x0, y + h_zi], fill=_LINIE_TARE)
        for bi, banda in enumerate(zi.benzi):
            by = y + bi * _BAND_H
            for i in range(nr_coloane + 1):
                d.line([(x0 + i * _COL_W, by), (x0 + i * _COL_W, by + _BAND_H)], fill=_LINIE)
            for b in banda:
                _deseneaza_bloc(d, b, mod, x0, by, f_sus, f_mij, f_jos)
        y += h_zi

    # bordura pronuntata in jurul grilei (ca `.grila` din CSS)
    d.rectangle([_MARGIN, grila_y0, _MARGIN + _ETT_LAT + latime_grid, y], outline=_TEXT, width=2)

    # legenda
    y += 10
    _deseneaza_legenda(d, grila, mod, filtru_saptamana, f_legenda, _MARGIN, y, latime - 2 * _MARGIN)
    return im


def _deseneaza_bloc(d: ImageDraw.ImageDraw, b: Bloc, mod: str, x0: int, by: int, f_sus, f_mij, f_jos) -> None:
    fundal, linie = PALETA.get(b.clasa_tip, PALETA["alta"])
    x = x0 + (b.col_start - 1) * _COL_W
    w = b.col_span * _COL_W
    rx0, ry0 = x + 2, by + 2
    rx1, ry1 = x + w - 2, by + _BAND_H - 2
    if not b.activa:
        fundal, linie = _blend_hex(fundal, _FUNDAL, 0.55), _blend_hex(linie, _FUNDAL, 0.3)
    d.rounded_rectangle([rx0, ry0, rx1, ry1], radius=5, fill=fundal, outline=linie, width=1)
    stil, grosime = _stil_provenienta(b)
    _linie_stanga(d, rx0, ry0 + 2, ry1 - 2, linie, stil, grosime)

    sus, mij, jos, sursa = _text_bloc(b, mod)
    tx = rx0 + _PAD + 3
    maxw = rx1 - tx - _PAD
    ty = ry0 + _PAD
    d.text((tx, ty), _trunchiaza(d, sus, f_sus, maxw), font=f_sus, fill=_TEXT)
    ty += 17
    if mij:
        d.text((tx, ty), _trunchiaza(d, mij, f_mij, maxw), font=f_mij, fill=_TEXT)
        ty += 14
    if jos:
        d.text((tx, ty), _trunchiaza(d, jos, f_jos, maxw), font=f_jos, fill=_TEXT_SLAB)
        ty += 14
    if sursa:
        d.text((tx, ty), _trunchiaza(d, sursa, f_mij, maxw), font=f_mij, fill=_TEXT_SLAB)


def bytes_imagine(im: Image.Image, format: str) -> bytes:
    buf = io.BytesIO()
    if format == "jpg":
        im.convert("RGB").save(buf, "JPEG", quality=90)
    else:
        im.save(buf, "PNG")
    return buf.getvalue()


def nume_fisier(slug: str, format: str) -> str:
    return f"orar-{slug}.{format}"


def raspuns_export(
    grila: Grila,
    *,
    titlu: str,
    slug: str,
    mod: str,
    format: str,
    sel,
    semigrupa: str | None = None,
    doar_saptamana: bool = False,
) -> Response:
    """Imaginea PNG/JPG a grilei, cu `Content-Disposition: attachment`.

    `sel` e `Selectie` (deps.py); din ea se ia eticheta orarului afisat, la fel ca in
    `OrarViu.eticheta`. `semigrupa`/`doar_saptamana` intra doar in subtitlu -- continutul
    grilei e deja filtrat de ruta care a construit-o.
    """
    v = getattr(sel, "versiune", None) or getattr(sel, "perioada", None)
    subtitlu = f"Semestrul {v.semestru} · {v.an_univ}" if v is not None else ""
    if semigrupa:
        subtitlu += f" · semigrupa {semigrupa.replace('_', ' ')}"
    if doar_saptamana:
        subtitlu += " · doar săptămâna curentă"
    im = imagine_grila(
        grila, titlu=titlu, subtitlu=subtitlu, mod=mod, filtru_saptamana=doar_saptamana
    )
    return Response(
        bytes_imagine(im, format),
        media_type="image/png" if format == "png" else "image/jpeg",
        headers={"Content-Disposition": f'attachment; filename="{nume_fisier(slug, format)}"'},
    )
