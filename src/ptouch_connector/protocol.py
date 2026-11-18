"""Raster-Protokoll des Brother PT-P710BT (128-Pin-Druckkopf, 180 dpi).

Grundlage: Brother "Software Developer's Manual – Raster Command Reference"
für PT-E550W/P750W/P710BT. Eine Rasterzeile läuft quer über das Band
(128 Pins = 16 Bytes), aufeinanderfolgende Zeilen bilden die Etikettenlänge.
"""

from __future__ import annotations

from dataclasses import dataclass

DPI = 180
HEAD_PINS = 128
LINE_BYTES = HEAD_PINS // 8  # 16

# Bandbreite (mm) -> (linker Rand in Pins, bedruckbare Pins)
TAPE_SPECS: dict[float, tuple[int, int]] = {
    3.5: (52, 24),
    6: (48, 32),
    9: (39, 50),
    12: (29, 70),
    18: (8, 112),
    24: (0, 128),
}

# Werte, die der Drucker im Statusbyte 10 für die Bandbreite meldet
STATUS_WIDTH_TO_TAPE = {4: 3.5, 6: 6, 9: 9, 12: 12, 18: 18, 24: 24}

MEDIA_TYPES = {
    0x00: "kein Band",
    0x01: "laminiertes Band (TZe)",
    0x03: "nicht laminiertes Band",
    0x11: "Schrumpfschlauch 2:1 (HSe)",
    0x13: "flexibles Band (FLe)",
    0x17: "Schrumpfschlauch 3:1",
    0xFF: "inkompatibles Band",
}

ERRORS_1 = {
    0x01: "kein Band eingelegt",
    0x04: "Schneidemesser blockiert",
    0x08: "Akku schwach",
    0x40: "falsches Netzteil (Hochspannung)",
}
ERRORS_2 = {
    0x01: "falsches Band / Band wechseln",
    0x10: "Deckel offen",
    0x20: "Überhitzung",
}

STATUS_TYPES = {
    0x00: "Antwort auf Statusabfrage",
    0x01: "Druck abgeschlossen",
    0x02: "Fehler",
    0x04: "ausgeschaltet",
    0x05: "Benachrichtigung",
    0x06: "Phasenwechsel",
}

# --- Einzelbefehle -----------------------------------------------------------

INVALIDATE = b"\x00" * 100
INITIALIZE = b"\x1b\x40"
STATUS_REQUEST = b"\x1b\x69\x53"
RASTER_MODE = b"\x1b\x69\x61\x01"
COMPRESSION_TIFF = b"\x4d\x02"
ZERO_LINE = b"\x5a"
PRINT_AND_FEED = b"\x1a"


def tape_spec(width_mm: float) -> tuple[int, int]:
    try:
        return TAPE_SPECS[width_mm]
    except KeyError:
        valid = ", ".join(f"{w:g}" for w in TAPE_SPECS)
        raise ValueError(f"Unbekannte Bandbreite {width_mm} mm (gültig: {valid})") from None


def print_info(width_mm: float, raster_lines: int, media_type: int = 0x01) -> bytes:
    """ESC i z – Druckinformationen (Bandbreite, Medientyp, Zeilenanzahl)."""
    width_byte = 4 if width_mm == 3.5 else int(width_mm)
    valid = 0x80 | 0x04 | 0x02  # Recovery an, Breite + Medientyp gültig
    return (
        b"\x1b\x69\x7a"
        + bytes([valid, media_type, width_byte, 0x00])
        + raster_lines.to_bytes(4, "little")
        + bytes([0x00, 0x00])  # Startseite, fix 0
    )


def mode_settings(auto_cut: bool = True) -> bytes:
    """ESC i M – Bit 6 = automatisches Schneiden."""
    return b"\x1b\x69\x4d" + bytes([0x40 if auto_cut else 0x00])


def advanced_mode(half_cut: bool = False, chain: bool = False) -> bytes:
    """ESC i K – Bit 2 = Halbschnitt, Bit 3 = KEIN Kettendruck (Band wird ausgeworfen)."""
    value = 0x00
    if half_cut:
        value |= 0x04
    if not chain:
        value |= 0x08
    return b"\x1b\x69\x4b" + bytes([value])


def margin(dots: int) -> bytes:
    """ESC i d – Vorschub/Rand vor und nach dem Druck in Punkten (180 dpi)."""
    return b"\x1b\x69\x64" + dots.to_bytes(2, "little")


def packbits(data: bytes) -> bytes:
    """TIFF-PackBits-Kompression."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        # Wiederholung suchen
        run = 1
        while i + run < n and run < 128 and data[i + run] == data[i]:
            run += 1
        if run >= 2:
            out.append((257 - run) & 0xFF)
            out.append(data[i])
            i += run
            continue
        # Literalfolge bis zur nächsten Wiederholung
        start = i
        i += 1
        while i < n and i - start < 128:
            if i + 1 < n and data[i] == data[i + 1]:
                break
            i += 1
        out.append(i - start - 1)
        out.extend(data[start:i])
    return bytes(out)


def raster_line(line: bytes) -> bytes:
    """Eine 16-Byte-Rasterzeile als G-Befehl (komprimiert) oder Z (leer)."""
    if len(line) != LINE_BYTES:
        raise ValueError(f"Rasterzeile muss {LINE_BYTES} Bytes lang sein")
    if not any(line):
        return ZERO_LINE
    packed = packbits(line)
    return b"\x47" + len(packed).to_bytes(2, "little") + packed


def build_job(
    lines: list[bytes],
    width_mm: float,
    *,
    media_type: int = 0x01,
    margin_dots: int = 14,
    auto_cut: bool = True,
    half_cut: bool = False,
) -> bytes:
    """Kompletter Druckauftrag für ein Etikett."""
    tape_spec(width_mm)
    job = bytearray()
    job += INVALIDATE
    job += INITIALIZE
    job += RASTER_MODE
    job += print_info(width_mm, len(lines), media_type)
    job += mode_settings(auto_cut)
    job += advanced_mode(half_cut=half_cut)
    job += margin(margin_dots)
    job += COMPRESSION_TIFF
    for line in lines:
        job += raster_line(line)
    job += PRINT_AND_FEED
    return bytes(job)


# --- Status -------------------------------------------------------------------


@dataclass
class Status:
    raw: bytes
    model_code: int
    errors: list[str]
    media_width_mm: float | None
    media_type: str
    media_type_code: int
    status_type: str
    status_type_code: int

    @property
    def ok(self) -> bool:
        return not self.errors and self.status_type_code != 0x02

    def describe(self) -> str:
        width = f"{self.media_width_mm:g} mm" if self.media_width_mm else "–"
        lines = [
            f"Modellcode:   0x{self.model_code:02x}",
            f"Status:       {self.status_type}",
            f"Band:         {width}, {self.media_type}",
            f"Fehler:       {', '.join(self.errors) if self.errors else 'keine'}",
        ]
        return "\n".join(lines)


def parse_status(data: bytes) -> Status:
    if len(data) != 32 or data[0] != 0x80 or data[1] != 0x20:
        raise ValueError(f"Ungültige Statusantwort ({len(data)} Bytes): {data.hex()}")
    errors = [msg for bit, msg in ERRORS_1.items() if data[8] & bit]
    errors += [msg for bit, msg in ERRORS_2.items() if data[9] & bit]
    return Status(
        raw=data,
        model_code=data[4],
        errors=errors,
        media_width_mm=STATUS_WIDTH_TO_TAPE.get(data[10]),
        media_type=MEDIA_TYPES.get(data[11], f"unbekannt (0x{data[11]:02x})"),
        media_type_code=data[11],
        status_type=STATUS_TYPES.get(data[18], f"unbekannt (0x{data[18]:02x})"),
        status_type_code=data[18],
    )
