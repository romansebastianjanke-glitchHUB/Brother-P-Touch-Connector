"""Verbindung zum Drucker über die Bluetooth-Seriell-Schnittstelle (SPP).

Nach dem Koppeln in macOS (Systemeinstellungen -> Bluetooth) legt macOS
eine serielle Schnittstelle wie /dev/cu.PT-P710BT1234 an.
"""

from __future__ import annotations

import glob
import time

from .protocol import INITIALIZE, INVALIDATE, STATUS_REQUEST, Status, parse_status


def find_ports() -> list[str]:
    """Kandidaten für den PT-P710BT unter macOS (und Linux-rfcomm)."""
    ports = sorted(glob.glob("/dev/cu.PT-*") + glob.glob("/dev/cu.*P710*"))
    ports += sorted(glob.glob("/dev/rfcomm*"))
    return list(dict.fromkeys(ports))


class Printer:
    def __init__(self, port: str, timeout: float = 10.0, settle: float = 2.0):
        import serial  # erst hier, damit Tests ohne pyserial-Gerät laufen

        self.port = port
        self.ser = serial.Serial(port, baudrate=115200, timeout=timeout, write_timeout=timeout)
        # Unter macOS wird die Bluetooth-Verbindung erst beim Öffnen aufgebaut.
        # Was in dieser Zeit gesendet wird, kann verloren gehen.
        time.sleep(settle)
        self.last_raw = b""

    def close(self) -> None:
        self.ser.close()

    def __enter__(self) -> "Printer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def write(self, data: bytes, chunk: int = 1024) -> None:
        # In Blöcken senden, damit der Bluetooth-Puffer nicht überläuft
        for i in range(0, len(data), chunk):
            self.ser.write(data[i : i + chunk])
            self.ser.flush()

    def _find_status(self, buf: bytearray) -> Status | None:
        """Gültige 32-Byte-Statusmeldung im Puffer suchen und daraus entfernen."""
        while True:
            i = buf.find(b"\x80\x20")
            if i < 0 or len(buf) - i < 32:
                if i > 0:
                    del buf[:i]
                return None
            try:
                st = parse_status(bytes(buf[i : i + 32]))
            except ValueError:
                del buf[: i + 1]
                continue
            del buf[: i + 32]
            return st

    def _read_until_status(self, buf: bytearray, timeout: float) -> Status | None:
        deadline = time.monotonic() + timeout
        old = self.ser.timeout
        self.ser.timeout = 0.5
        try:
            while True:
                st = self._find_status(buf)
                if st is not None:
                    return st
                if time.monotonic() >= deadline:
                    return None
                buf += self.ser.read(64)
        finally:
            self.ser.timeout = old

    def status(self, attempts: int = 3, timeout: float = 5.0) -> Status:
        self.ser.reset_input_buffer()
        buf = bytearray()
        received = 0
        for _ in range(attempts):
            self.write(INVALIDATE + INITIALIZE + STATUS_REQUEST)
            before = len(buf)
            st = self._read_until_status(buf, timeout)
            if st is not None:
                return st
            received += len(buf) - before
        self.last_raw = bytes(buf)
        detail = f"{received} Bytes empfangen" + (f": {bytes(buf[:64]).hex()}" if buf else "")
        raise TimeoutError(f"Keine gültige Statusantwort nach {attempts} Versuchen ({detail})")

    def print_job(self, job: bytes, wait: float = 30.0) -> Status | None:
        """Auftrag senden und auf 'Druck abgeschlossen' bzw. Fehler warten."""
        self.ser.reset_input_buffer()
        self.write(job)
        buf = bytearray()
        deadline = time.monotonic() + wait
        last = None
        while time.monotonic() < deadline:
            st = self._read_until_status(buf, deadline - time.monotonic())
            if st is None:
                break
            last = st
            if st.status_type_code in (0x01, 0x02):  # fertig oder Fehler
                return st
        return last
