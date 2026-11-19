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
    "chain": False,
}

FONT_DIRS = [
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    str(Path.home() / "Library/Fonts"),
    "/usr/share/fonts/truetype",
]


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


def substitute(text: str, value: str | None) -> str:
    text = text.replace("{datum}", _dt.date.today().strftime("%d.%m.%Y"))
    if value is not None:
        text = text.replace("{n}", value)
    return text


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
    target = int(h * float(item.get("size_pct", 100)) / 100)
    module = target // n
    if module < 1:
        warnings.append(f"QR-Code „{data[:20]}“ passt nicht aufs Band – kürzerer Inhalt oder breiteres Band.")
        module = 1
    elif module == 1:
        warnings.append("QR-Code ist sehr klein (1 Punkt pro Modul) – evtl. schlecht lesbar.")
    size = n * module
    img = _blank(size, h)
    top = (h - size) // 2
    px = img.load()
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                for dy in range(module):
                    for dx in range(module):
                        yy = top + y * module + dy
                        if 0 <= yy < h:
                            px[x * module + dx, yy] = 0
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
    if t == "text":
        img = render_text(item, h, value, warnings)
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


def series_values(spec: dict) -> list[str | None]:
    s = spec.get("series") or {}
    mode = s.get("mode", "none")
    if mode == "numbers":
        start, end = int(s.get("start", 1)), int(s.get("end", 1))
        step = int(s.get("step", 1)) or 1
        if (end - start) * step < 0:
            step = -step
        digits = int(s.get("digits", 0))
        vals = list(range(start, end + (1 if step > 0 else -1), step))[:500]
        return [str(v).zfill(digits) for v in vals]
    if mode == "list":
        vals = [ln.strip() for ln in str(s.get("list", "")).splitlines() if ln.strip()]
        return vals[:500] or [None]
    return [None]


def render_label(spec: dict, tape_mm: float, value: str | None = None) -> tuple[Image.Image, list[str]]:
    """Ein Etikett als Schwarzweißbild (L-Modus, Höhe = bedruckbare Pins)."""
    _, h = tape_spec(tape_mm)
    warnings: list[str] = []
    frame = spec.get("frame", "none")
    fpx = int(spec.get("frame_px", 2)) if frame != "none" else 0
    inner_pad_v = fpx + (2 if fpx else 0)
    inner_h = max(4, h - 2 * inner_pad_v)

    parts = [render_item(it, inner_h, value, warnings) for it in spec.get("items", []) if it]
    parts = [p for p in parts if p.width > 1] or [_blank(1, inner_h)]
    gap = int(float(spec.get("gap_mm", 2)) * MM)
    content_w = sum(p.width for p in parts) + gap * (len(parts) - 1)
    content = _blank(content_w, inner_h)
    x = 0
    for p in parts:
        content.paste(p, (x, 0))
        x += p.width + gap

    pad = int(float(spec.get("padding_mm", 1)) * MM) + fpx + (3 if fpx else 0)
    width = content_w + 2 * pad
    fixed = float(spec.get("length_mm", 0) or 0)
    if fixed > 0:
        fixed_w = int(fixed * MM)
        if fixed_w < width:
            warnings.append(f"Inhalt ist länger als die feste Länge ({width / MM:.0f} mm > {fixed:g} mm) – wird abgeschnitten.")
        width = fixed_w
    img = _blank(width, h)
    align = spec.get("align", "center")
    if align == "left" or fixed <= 0:
        cx = pad
    elif align == "right":
        cx = width - pad - content_w
    else:
        cx = (width - content_w) // 2
    img.paste(content, (cx, inner_pad_v))

    if fpx:
        d = ImageDraw.Draw(img)
        box = [0, 0, width - 1, h - 1]
        if frame == "round":
            d.rounded_rectangle(box, radius=max(4, h // 4), outline=0, width=fpx)
        else:
            d.rectangle(box, outline=0, width=fpx)
    if spec.get("invert"):
        img = ImageOps.invert(img)
    return img.point(lambda p: 0 if p < 128 else 255), warnings


def render_all(spec: dict, tape_mm: float) -> tuple[list[Image.Image], list[str], list[str | None]]:
    values = series_values(spec)
    images, warnings = [], []
    for v in values:
        img, w = render_label(spec, tape_mm, v)
        images.append(img)
        for msg in w:
            if msg not in warnings:
                warnings.append(msg)
    return images, warnings, values
