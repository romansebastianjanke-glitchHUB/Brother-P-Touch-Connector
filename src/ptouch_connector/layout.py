"""Etiketten-Layout: mehrere Elemente nebeneinander auf einem Band.

Ein Etikett wird als JSON-ähnliches dict beschrieben (siehe `DEFAULT_SPEC`):
Elemente (Text, Bild, QR-Code, Barcode, Abstand) werden von links nach rechts
gesetzt, jedes auf die Bandhöhe skaliert und vertikal ausgerichtet.
Vorschau und Druck verwenden exakt diese Funktion.
"""

from __future__ import annotations

import base64
import datetime as _dt
import io
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from .protocol import DPI, tape_spec
from .render import FONT_CANDIDATES

MM = DPI / 25.4  # Punkte pro mm

DEFAULT_SPEC: dict[str, Any] = {
    "tape": "auto",
    "items": [{"type": "text", "text": "Hallo"}],
    "gap_mm": 2.0,
    "padding_mm": 1.0,
    "length_mm": 0,          # 0 = automatisch
    "align": "center",       # bei fester Länge: left | center | right
    "frame": "none",         # none | rect | round
    "frame_px": 2,
    "invert": False,
    "flip": False,
    "margin_mm": 2.0,
    "copies": 1,
    "cut": "full",           # full | half | none
    "series": {"mode": "none", "start": 1, "end": 10, "step": 1, "digits": 0, "list": ""},
    "separate_jobs": False,
}

FONT_DIRS = [
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    str(Path.home() / "Library/Fonts"),
    "/usr/share/fonts/truetype",
]


def margin_dots(spec: dict) -> int:
    """Rand vor und nach dem Inhalt in Druckpunkten (Drucker-Minimum 14 ≈ 2 mm)."""
    return max(14, min(900, int(float(spec.get("margin_mm", 2) or 0) * MM)))


def total_length_mm(spec: dict, content_px: int) -> float:
    """Tatsächliche Etikettlänge: Rand + Inhalt + Rand (+ Längenkorrektur des Geräts)."""
    return (content_px + 2 * margin_dots(spec)) / MM + float(spec.get("_length_offset_mm", 0) or 0)


# --- Schriften -------------------------------------------------------------------

_FONT_CACHE: dict[str, str] | None = None


def list_fonts() -> dict[str, str]:
    """Anzeigename -> Dateipfad aller gefundenen TTF/OTF/TTC-Schriften."""
    global _FONT_CACHE
    if _FONT_CACHE is None:
        found: dict[str, str] = {}
        for d in FONT_DIRS:
            base = Path(d)
            if not base.is_dir():
                continue
            for f in sorted(base.rglob("*")):
                if f.suffix.lower() in (".ttf", ".otf", ".ttc") and "LastResort" not in f.name:
                    name = f.stem.replace("_", " ")
                    found.setdefault(name, str(f))
        _FONT_CACHE = dict(sorted(found.items(), key=lambda kv: kv[0].lower()))
    return _FONT_CACHE


def _font(name: str | None, size: int):
    if name == "__builtin__":  # in Pillow eingebaute Schrift, letzter Rückfall
        return ImageFont.load_default(size=size)
    path = list_fonts().get(name or "")
    if not path:
        for c in FONT_CANDIDATES:
            if Path(c).exists():
                path = c
                break
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


# --- Hilfen ----------------------------------------------------------------------


def _blank(w: int, h: int, color: int = 255) -> Image.Image:
    return Image.new("L", (max(1, w), max(1, h)), color)


def _to_bw(img: Image.Image, threshold: int = 128, dither: bool = False) -> Image.Image:
    g = img.convert("L")
    if dither:
        return g.convert("1", dither=Image.FLOYDSTEINBERG).convert("L")
    return g.point(lambda p: 0 if p < threshold else 255)


def _crop_white(img: Image.Image) -> Image.Image:
    """Weißen Rand entfernen (Bild in L-Modus)."""
    bbox = ImageChops.invert(img).getbbox()
    return img.crop(bbox) if bbox else img


def substitute(text: str, value) -> str:
    """Platzhalter ersetzen: {datum}, {n} und bei Tabellen-Serien jede Spalte, z. B. {raum}.

    `value` ist ein Text (nur {n}) oder ein dict {"n": …, "raum": …}.
    """
    text = text.replace("{datum}", _dt.date.today().strftime("%d.%m.%Y"))
    if value is None:
        return text
    ctx = value if isinstance(value, dict) else {"n": value}
    for key, val in ctx.items():
        if val is not None:
            text = text.replace("{" + str(key) + "}", str(val))
    return text


# Schriftgrößen-Protokoll für einheitliche Größen in einer Serie (pro Thread,
# weil der Studio-Server Vorschauen parallel berechnet)
import threading as _threading

_LOCAL = _threading.local()


def decode_data_url(src: str) -> Image.Image:
    m = re.match(r"data:[^;,]+(;base64)?,(.*)", src, re.S)
    raw = base64.b64decode(m.group(2)) if m and m.group(1) else base64.b64decode(src)
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    return img.convert("L")


# --- Elemente --------------------------------------------------------------------


def render_text(item: dict, h: int, value: str | None, warnings: list[str]) -> Image.Image:
    """Text setzen; scheitert die Schrift in FreeType, mit Ersatzschriften erneut versuchen."""
    try:
        return _render_text(item, h, value, warnings)
    except OSError as e:
        name = item.get("font") or "Standardschrift (Helvetica)"
        warnings.append(f"Schrift „{name}“{' fett' if item.get('bold') else ''} lässt sich nicht "
                        f"zeichnen ({e}) – verwende eine Ersatzschrift.")
        tries = []
        if item.get("font") or item.get("bold"):
            tries.append(dict(item, font="", bold=False))
        tries.append(dict(item, font="__builtin__", bold=False))
        for i, alt in enumerate(tries):
            try:
                return _render_text(alt, h, value, warnings)
            except OSError:
                if i == len(tries) - 1:
                    raise
        raise


def _render_text(item: dict, h: int, value: str | None, warnings: list[str]) -> Image.Image:
    text = substitute(str(item.get("text", "")), value).replace("\\n", "\n")
    if not text.strip():
        return _blank(1, h)
    bold = bool(item.get("bold"))
    italic = bool(item.get("italic"))
    underline = bool(item.get("underline"))
    spacing_pct = float(item.get("line_spacing", 10))
    align = item.get("align", "center")
    target = max(4, int(h * float(item.get("size_pct", 100)) / 100) - 2)

    def measure(size: int):
        f = _font(item.get("font"), size)
        d = ImageDraw.Draw(Image.new("L", (1, 1)))
        sp = int(size * spacing_pct / 100)
        st = max(1, size // 22) if bold else 0
        bb = d.multiline_textbbox((0, 0), text, font=f, spacing=sp, stroke_width=st)
        return f, sp, bb

    if item.get("size_mode") == "fixed":
        size = max(4, int(float(item.get("size_px", 40))))
        f, sp, bb = measure(size)
        best = (f, sp, bb)
        if bb[3] - bb[1] > h:
            warnings.append(f"Text „{text[:20]}“ ist höher als das Band und wird abgeschnitten.")
    else:
        lo, hi = 4, target * 2
        best = measure(lo)
        while lo <= hi:
            mid = (lo + hi) // 2
            m = measure(mid)
            if m[2][3] - m[2][1] <= target:
                best, lo = m, mid + 1
            else:
                hi = mid - 1
        f, sp, bb = best
    stroke = max(1, f.size // 22) if bold and hasattr(f, "size") else 0
    log = getattr(_LOCAL, "sizes", None)
    if log is not None and "_uid" in item and hasattr(f, "size"):
        uid = item["_uid"]
        log[uid] = min(log.get(uid, 10 ** 6), int(f.size))

    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    pad = 2 + (th // 4 if italic else 0)
    img = _blank(tw + 2 * pad, h)
    d = ImageDraw.Draw(img)
    y = (h - th) // 2 - bb[1]
    d.multiline_text((pad - bb[0], y), text, font=f, fill=0, spacing=sp, align=align,
                     stroke_width=stroke, stroke_fill=0)
    if underline:
        uy = min(h - 1, (h + th) // 2 + 1)
        d.line([(pad, uy), (pad + tw, uy)], fill=0, width=max(1, h // 30))
    if italic:
        shear = 0.2
        img = img.transform(img.size, Image.AFFINE, (1, shear, -shear * h / 2, 0, 1, 0),
                            resample=Image.BICUBIC, fillcolor=255)
    return _to_bw(img)


def render_image(item: dict, h: int, warnings: list[str]) -> Image.Image:
    src = item.get("src")
    if not src:
        return _blank(1, h)
    try:
        img = decode_data_url(src)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"Bild konnte nicht gelesen werden: {e}")
        return _blank(1, h)
    rot = int(item.get("rotate", 0)) % 360
    if rot:
        img = img.rotate(-rot, expand=True, fillcolor=255)
    # Helligkeit/Kontrast
    contrast = float(item.get("contrast", 0))
    if contrast:
        from PIL import ImageEnhance
        img = ImageEnhance.Contrast(img).enhance(1 + contrast / 100)
    if item.get("invert"):
        img = ImageOps.invert(img)
    if item.get("autocrop", True):
        img = _crop_white(img)
    target_h = max(1, int(h * float(item.get("size_pct", 100)) / 100))
    w = max(1, round(img.width * target_h / img.height))
    img = img.resize((w, target_h), Image.LANCZOS)
    mode = item.get("mode", "dither")
    return _to_bw(img, int(item.get("threshold", 128)), dither=(mode == "dither"))


def render_qr(item: dict, h: int, value: str | None, warnings: list[str]) -> Image.Image:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q

    data = substitute(str(item.get("data", "")), value)
    if not data:
        return _blank(1, h)
    level = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M, "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}
    qr = qrcode.QRCode(error_correction=level.get(item.get("ecc", "M"), ERROR_CORRECT_M), border=0)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    caption = substitute(str(item.get("caption", "") or ""), value).strip()
    cap_h = int(h * float(item.get("caption_pct", 25)) / 100) if caption else 0
    target = int((h - cap_h) * float(item.get("size_pct", 100)) / 100)
    module = target // n
    if module < 1:
        warnings.append(f"QR-Code „{data[:20]}“ passt nicht aufs Band – kürzerer Inhalt oder breiteres Band.")
        module = 1
    elif module == 1:
        warnings.append("QR-Code ist sehr klein (1 Punkt pro Modul) – evtl. schlecht lesbar.")
    size = n * module
    cap_img = None
    if caption:
        # kleiner Text unter dem QR-Code (z. B. die QR-Nummer), so breit wie der Code
        cap_img = render_text({"type": "text", "text": caption, "font": item.get("font", ""),
                               "bold": item.get("caption_bold", False), "line_spacing": 0,
                               "size_pct": 100}, max(6, cap_h), None, warnings)
        cap_img = _crop_white(cap_img)
        if cap_img.width > size * 1.15:  # breiter als der QR-Code: proportional verkleinern
            k = size * 1.15 / cap_img.width
            cap_img = _to_bw(cap_img.resize((max(1, int(cap_img.width * k)), max(1, int(cap_img.height * k))), Image.LANCZOS))
    width = max(size, cap_img.width if cap_img else 0)
    img = _blank(width, h)
    gap = max(1, cap_h // 8) if cap_img else 0
    block = size + (gap + cap_img.height if cap_img else 0)
    top = (h - block) // 2
    if cap_img:
        img.paste(cap_img, ((width - cap_img.width) // 2, top + size + gap))
    xoff = (width - size) // 2
    px = img.load()
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                for dy in range(module):
                    for dx in range(module):
                        yy = top + y * module + dy
                        if 0 <= yy < h:
                            px[xoff + x * module + dx, yy] = 0
    return img


def render_barcode(item: dict, h: int, value: str | None, warnings: list[str]) -> Image.Image:
    from barcode import get_barcode_class

    data = substitute(str(item.get("data", "")), value)
    kind = item.get("kind", "code128")
    if not data:
        return _blank(1, h)
    try:
        bc = get_barcode_class(kind)(data)
        modules = bc.build()[0]
        human = bc.get_fullcode()
    except Exception as e:  # noqa: BLE001
        warnings.append(f"Barcode ({kind}) ungültig: {e}")
        return _blank(1, h)
    mw = max(1, int(item.get("module_px", 2)))
    show_text = bool(item.get("show_text", True))
    text_h = 0
    font = None
    if show_text:
        text_h = max(8, h // 4)
        font = _font(item.get("font"), text_h)
    bar_h = max(4, int(h * float(item.get("size_pct", 100)) / 100) - text_h)
    quiet = 4 * mw
    w = len(modules) * mw + 2 * quiet
    img = _blank(w, h)
    d = ImageDraw.Draw(img)
    top = (h - bar_h - text_h) // 2
    for i, m in enumerate(modules):
        if m == "1":
            x = quiet + i * mw
            d.rectangle([x, top, x + mw - 1, top + bar_h - 1], fill=0)
    if show_text and font is not None:
        bb = d.textbbox((0, 0), human, font=font)
        tx = (w - (bb[2] - bb[0])) // 2 - bb[0]
        d.text((tx, top + bar_h - bb[1]), human, font=font, fill=0)
    return img


def render_with_caption(item: dict, h: int, value, warnings: list[str]) -> Image.Image:
    """Text/Nummer, optional mit kleiner Zeile darunter (z. B. 3/4 Nummer + 1/4 „SM 58“)."""
    caption = substitute(str(item.get("caption", "") or ""), value).strip()
    if not caption:
        return render_text(item, h, value, warnings)
    cap_h = max(6, int(h * float(item.get("caption_pct", 25)) / 100))
    gap = max(1, cap_h // 8)
    main_h = max(6, h - cap_h - gap)
    main = _crop_white(render_text(item, main_h, value, warnings))
    cap_item = {"type": "text", "text": caption, "font": item.get("caption_font") or item.get("font", ""),
                "bold": bool(item.get("caption_bold")), "line_spacing": 0, "size_pct": 100}
    cap = _crop_white(render_text(cap_item, cap_h, None, warnings))
    width = max(main.width, cap.width) + 4
    img = _blank(width, h)
    block = main.height + gap + cap.height
    top = max(0, (h - block) // 2)
    align = item.get("caption_align", "center")

    def x_for(w):
        return 2 if align == "left" else (width - w - 2 if align == "right" else (width - w) // 2)

    img.paste(main, ((width - main.width) // 2, top))
    img.paste(cap, (x_for(cap.width), min(h - cap.height, top + main.height + gap)))
    return img


def render_item(item: dict, h: int, value: str | None, warnings: list[str]) -> Image.Image:
    """Ein Element rendern; Fehler werden zur Warnung, statt die ganze Vorschau zu blockieren."""
    try:
        return _render_item(item, h, value, warnings)
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        what = {"text": "Text", "image": "Bild", "symbol": "Symbol", "qr": "QR-Code",
                "barcode": "Barcode", "spacer": "Abstand"}.get(item.get("type"), "Element")
        warnings.append(f"{what} konnte nicht erstellt werden: {type(e).__name__}: {e}")
        return _blank(1, h)


def _render_item(item: dict, h: int, value: str | None, warnings: list[str]) -> Image.Image:
    t = item.get("type")
    if t in ("text", "number"):
        main = dict(item, text=item.get("text") or "{n}") if t == "number" else item
        val = value if (value is not None or t == "text") else "01"
        img = render_with_caption(main, h, val, warnings)
    elif t in ("image", "symbol"):
        img = render_image(item, h, warnings)
    elif t == "qr":
        img = render_qr(item, h, value, warnings)
    elif t == "barcode":
        img = render_barcode(item, h, value, warnings)
    elif t == "spacer":
        img = _blank(int(float(item.get("width_mm", 5)) * MM), h)
    else:
        warnings.append(f"Unbekanntes Element: {t}")
        img = _blank(1, h)
    # vertikal ausrichten, falls kleiner als Bandhöhe
    if img.height != h:
        canvas = _blank(img.width, h)
        valign = item.get("valign", "center")
        y = 0 if valign == "top" else (h - img.height if valign == "bottom" else (h - img.height) // 2)
        canvas.paste(img, (0, y))
        img = canvas
    return img


# --- Etikett ---------------------------------------------------------------------


def _generated_values(s: dict) -> list[str]:
    start, end = int(s.get("start", 1)), int(s.get("end", 1))
    step = int(s.get("step", 1)) or 1
    if (end - start) * step < 0:
        step = -step
    digits = int(s.get("digits", 0))
    vals = list(range(start, end + (1 if step > 0 else -1), step))[:500]
    return [str(v).zfill(digits) for v in vals]


def series_rows(spec: dict) -> list[dict]:
    """Etiketten einer Serie: [{"value": "01", "items": None | [...]}, …].

    `items` = eigene Elemente nur für dieses Etikett, sonst gelten die Vorlage-Elemente.
    Ältere Vorlagen ohne `rows` (mode numbers/list) werden weiter unterstützt.
    """
    s = spec.get("series") or {}
    mode = s.get("mode", "none")
    if mode == "none" or not mode:
        return [{"value": None, "items": None, "fields": {}, "on": True}]
    rows = s.get("rows")
    if isinstance(rows, list) and rows:
        out = []
        for r in rows[:500]:
            if isinstance(r, dict):
                fields = {str(k): str(v) for k, v in (r.get("fields") or {}).items()}
                out.append({"value": str(r.get("value", "")), "items": r.get("items") or None,
                            "fields": fields, "on": r.get("on", True) is not False})
            else:
                out.append({"value": str(r), "items": None, "fields": {}, "on": True})
        return out
    if mode == "numbers":
        vals = _generated_values(s)
    elif mode == "list":
        vals = [ln.strip() for ln in str(s.get("list", "")).splitlines() if ln.strip()][:500]
    else:
        vals = []
    return ([{"value": v, "items": None, "fields": {}, "on": True} for v in vals]
            or [{"value": None, "items": None, "fields": {}, "on": True}])


def series_values(spec: dict) -> list[str | None]:
    return [r["value"] for r in series_rows(spec)]


def item_position(it: dict) -> str:
    """links / mitte / rechts. Alte Nummern ohne Angabe stehen links, 'flow' = mitte."""
    p = it.get("position")
    if p in ("left", "right"):
        return p
    if not p and it.get("type") == "number":
        return "left"
    return "center"


def _scaled(items: list, f: float) -> list:
    out = []
    for it in items:
        it = dict(it)
        if it.get("type") in ("text", "number") and it.get("size_mode") == "fixed":
            it["size_px"] = max(6, float(it.get("size_px", 40)) * f)
        elif it.get("type") != "spacer":
            it["size_pct"] = max(15, float(it.get("size_pct", 100)) * f)
        out.append(it)
    return out


def render_label(spec: dict, tape_mm: float, value: str | None = None,
                 items: list | None = None) -> tuple[Image.Image, list[str]]:
    """Wie _render_label_once; passt der Inhalt nicht in die feste Länge, wird er
    automatisch verkleinert, damit die eingestellte Länge die echte Länge bleibt."""
    src = items if items is not None else spec.get("items", [])
    img, warnings, need = _render_label_once(spec, tape_mm, value, src)
    if need is None or spec.get("autofit") is False:
        return img, warnings
    have, natural = need
    f = 1.0
    for _ in range(6):
        f *= max(0.5, min(0.97, have / natural))
        img2, w2, need2 = _render_label_once(spec, tape_mm, value, _scaled(src, f))
        if need2 is None:
            w2.append(f"Inhalt auf {f * 100:.0f} % verkleinert, damit er auf {float(spec['length_mm']):g} mm passt.")
            return img2, w2
        have, natural = need2
        if f < 0.2:
            break
    return img, warnings


def _render_label_once(spec: dict, tape_mm: float, value: str | None,
                       items: list) -> tuple[Image.Image, list[str], tuple | None]:
    """Ein Etikett als Schwarzweißbild (L-Modus, Höhe = bedruckbare Pins).

    Aufbau: [Elemente links] | [Elemente Mitte] | [Elemente rechts]. Links/rechts
    bleiben bei fester Etikettlänge am Rand stehen, die Mitte wird im Rest
    ausgerichtet. Links/rechts optional mit Trennlinie zur Mitte.
    """
    _, h = tape_spec(tape_mm)
    warnings: list[str] = []
    frame = spec.get("frame", "none")
    fpx = int(spec.get("frame_px", 2)) if frame != "none" else 0
    inner_pad_v = fpx + (2 if fpx else 0)
    inner_h = max(4, h - 2 * inner_pad_v)
    gap = int(float(spec.get("gap_mm", 2)) * MM)

    src_items = [it for it in items if it]
    left_items = [it for it in src_items if item_position(it) == "left"]
    right_items = [it for it in src_items if item_position(it) == "right"]
    mid_items = [it for it in src_items if item_position(it) == "center"]

    def strip(its):
        parts = [render_item(it, inner_h, value, warnings) for it in its]
        return [(p, it) for p, it in zip(parts, its) if p.width > 1]

    def join(parts):
        if not parts:
            return None
        w = sum(p.width for p, _ in parts) + gap * (len(parts) - 1)
        c = _blank(w, inner_h)
        x = 0
        for p, _ in parts:
            c.paste(p, (x, 0))
            x += p.width + gap
        return c

    left = strip(left_items)
    right = strip(right_items)
    mid = join(strip(mid_items))
    lblk, rblk = join(left), join(right)
    l_div = any(it.get("divider") for _, it in left)
    r_div = any(it.get("divider") for _, it in right)
    div_w = max([int(it.get("divider_px", 2)) for _, it in left + right if it.get("divider")] or [0])
    # Abstand neben einer Trennlinie: auf beiden Seiten ein Elementabstand
    l_gap = (2 * gap + div_w) if l_div else gap
    r_gap = (2 * gap + div_w) if r_div else gap

    pad = int(float(spec.get("padding_mm", 1)) * MM) + fpx + (3 if fpx else 0)
    lw = lblk.width if lblk else 0
    rw = rblk.width if rblk else 0
    mw = mid.width if mid else 0
    natural = pad * 2 + lw + rw + mw
    if lblk and (mid or rblk):
        natural += l_gap
    if rblk and mid:
        natural += r_gap
    width = natural
    overflow = None
    fixed = float(spec.get("length_mm", 0) or 0)
    if fixed > 0:
        # eingegebene Länge = Länge des fertigen Etiketts, also Ränder abziehen
        offset = float(spec.get("_length_offset_mm", 0) or 0)
        fixed_w = int(round((fixed - offset) * MM)) - 2 * margin_dots(spec)
        if fixed_w < 8:
            min_mm = total_length_mm(spec, 8)
            warnings.append(f"Etikettlänge {fixed:g} mm ist zu kurz – wegen des Rands vor/nach dem Druck "
                            f"sind mindestens {min_mm:.0f} mm nötig (Rand im Tab Druck verkleinern).")
            fixed_w = 8
        if fixed_w < natural:
            overflow = (fixed_w, natural)
            warnings.append(f"Inhalt passt nicht auf {fixed:g} mm (bräuchte ≈ {total_length_mm(spec, natural):.0f} mm) "
                            "– wird abgeschnitten. Abstände verringern oder Länge erhöhen.")
        width = fixed_w
    img = _blank(width, h)
    d = ImageDraw.Draw(img)
    y0 = inner_pad_v

    # linker Block
    x_left_end = pad
    if lblk:
        img.paste(lblk, (pad, y0))
        x_left_end = pad + lw
    # rechter Block
    x_right_start = width - pad
    if rblk:
        x_right_start = width - pad - rw
        if not (fixed > 0):
            x_right_start = natural - pad - rw
        img.paste(rblk, (x_right_start, y0))
    # Mittelbereich
    area_l = x_left_end + (l_gap if lblk else 0)
    area_r = x_right_start - (r_gap if rblk else 0)
    if mid:
        align = spec.get("align", "center")
        if fixed <= 0 or align == "left":
            mx = area_l
        elif align == "right":
            mx = area_r - mw
        else:
            mx = area_l + max(0, (area_r - area_l - mw) // 2)
        img.paste(mid, (mx, y0))
    # Trennlinien (über die ganze Bandhöhe)
    if l_div and lblk:
        x = x_left_end + gap
        d.rectangle([x, 0, x + div_w - 1, h - 1], fill=0)
    if r_div and rblk:
        x = x_right_start - gap - div_w
        d.rectangle([x, 0, x + div_w - 1, h - 1], fill=0)

    if fpx:
        box = [0, 0, width - 1, h - 1]
        if frame == "round":
            d.rounded_rectangle(box, radius=max(4, h // 4), outline=0, width=fpx)
        else:
            d.rectangle(box, outline=0, width=fpx)
    if spec.get("invert"):
        img = ImageOps.invert(img)
    return img.point(lambda p: 0 if p < 128 else 255), warnings, overflow


def row_context(row: dict):
    if row["value"] is None and not row.get("fields"):
        return None
    ctx = {"n": row["value"] if row["value"] is not None else ""}
    ctx.update(row.get("fields") or {})
    return ctx


def _tag_items(items: list, prefix: str) -> list:
    return [dict(it, _uid=f"{prefix}{i}") for i, it in enumerate(items or []) if it]


def render_all(spec: dict, tape_mm: float, only_selected: bool = False):
    """Alle Etiketten einer Serie rendern.

    Rückgabe: (Bilder, Warnungen, Werte, Zeilen-Indizes). Mit `series.uniform` bekommen
    gleiche Text-Elemente auf allen Etiketten dieselbe (kleinste nötige) Schriftgröße.
    """
    rows = series_rows(spec)
    index = [i for i, r in enumerate(rows) if r.get("on", True) or not only_selected]
    rows_sel = [rows[i] for i in index]
    uniform = bool((spec.get("series") or {}).get("uniform")) and len(rows_sel) > 1

    def items_for(r):
        own = r["items"] is not None
        src = r["items"] if own else spec.get("items", [])
        # Etiketten mit eigener Gestaltung werden für sich vereinheitlicht
        return _tag_items(src, f"r{id(r)}-" if own else "t")

    tagged = [items_for(r) for r in rows_sel]
    if uniform:
        _LOCAL.sizes = {}
        try:
            for r, its in zip(rows_sel, tagged):
                render_label(spec, tape_mm, row_context(r), its)
            sizes = dict(_LOCAL.sizes)
        finally:
            _LOCAL.sizes = None
        fixed = []
        for its in tagged:
            out = []
            for it in its:
                if it.get("type") in ("text", "number") and it["_uid"] in sizes:
                    it = dict(it, size_mode="fixed", size_px=sizes[it["_uid"]])
                out.append(it)
            fixed.append(out)
        tagged = fixed

    images, warnings = [], []
    for r, its in zip(rows_sel, tagged):
        img, w = render_label(spec, tape_mm, row_context(r), its)
        images.append(img)
        for msg in w:
            if msg not in warnings:
                warnings.append(msg)
    return images, warnings, [r["value"] for r in rows_sel], index
