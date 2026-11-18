"""Etikett rendern (Text -> Schwarzweißbild) und in Rasterzeilen umwandeln."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .protocol import HEAD_PINS, LINE_BYTES, tape_spec

FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _load_font(path: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [path] if path else FONT_CANDIDATES
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    if path:
        raise FileNotFoundError(f"Schriftart nicht gefunden: {path}")
    return ImageFont.load_default(size=size)


def render_text(
    text: str,
    width_mm: float,
    *,
    font_path: str | None = None,
    padding: int = 4,
) -> Image.Image:
    """Text (auch mehrzeilig mit \\n) so groß wie möglich auf die Bandhöhe setzen."""
    _, pins = tape_spec(width_mm)
    target = pins - 2  # 1 Pin Luft oben und unten
    lines = text.split("\n")

    size = target
    while size > 4:
        font = _load_font(font_path, size)
        probe = ImageDraw.Draw(Image.new("1", (1, 1)))
        bbox = probe.multiline_textbbox((0, 0), text, font=font, spacing=1)
        if bbox[3] - bbox[1] <= target:
            break
        size -= 1

    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    img = Image.new("1", (text_w + 2 * padding, pins), 1)
    draw = ImageDraw.Draw(img)
    y = (pins - text_h) // 2 - bbox[1]
    draw.multiline_text((padding - bbox[0], y), text, font=font, fill=0, spacing=1,
                        align="center" if len(lines) > 1 else "left")
    return img


def load_image(path: str, width_mm: float, *, threshold: int = 128) -> Image.Image:
    """Bild laden, auf Bandhöhe skalieren und in Schwarzweiß wandeln."""
    _, pins = tape_spec(width_mm)
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("L")
    if img.height != pins:
        new_w = max(1, round(img.width * pins / img.height))
        img = img.resize((new_w, pins), Image.LANCZOS)
    return img.point(lambda p: 0 if p < threshold else 255).convert("1")


def to_raster_lines(img: Image.Image, width_mm: float, *, flip: bool = False) -> list[bytes]:
    """Bild (Höhe = bedruckbare Pins) in 16-Byte-Rasterzeilen umwandeln.

    Bildspalte x -> Rasterzeile x, Bildzeile y -> Pin (linker Rand + y).
    Wie bei ptouch-print wird die Pin-Nummer innerhalb der Zeile gespiegelt
    abgelegt. Falls der Ausdruck auf dem Kopf steht, `flip=True` setzen.
    """
    left, pins = tape_spec(width_mm)
    if img.height != pins:
        raise ValueError(f"Bildhöhe {img.height} passt nicht zu {width_mm} mm Band ({pins} Pins)")
    bw = img.convert("1")
    px = bw.load()
    lines: list[bytes] = []
    for x in range(bw.width):
        line = bytearray(LINE_BYTES)
        for y in range(pins):
            if px[x, y] == 0:  # schwarz
                row = pins - 1 - y if flip else y
                pin = left + row
                line[(HEAD_PINS - 1 - pin) // 8] |= 0x80 >> ((HEAD_PINS - 1 - pin) % 8)
        lines.append(bytes(line))
    return lines


def preview(img: Image.Image, path: str, scale: int = 4) -> None:
    """Vergrößerte Vorschau speichern (Bandlänge horizontal)."""
    framed = ImageOps.expand(img.convert("L"), border=2, fill=180)
    framed.resize((framed.width * scale, framed.height * scale), Image.NEAREST).save(path)
