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
    def __init__(self, port: str, timeout: float = 10.0):
        import serial  # erst hier, damit Tests ohne pyserial-Gerät laufen

        self.port = port
        self.ser = serial.Serial(port, baudrate=115200, timeout=timeout, write_timeout=timeout)

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

    def read_status(self, timeout: float | None = None) -> Status | None:
        old = self.ser.timeout
        if timeout is not None:
            self.ser.timeout = timeout
        try:
            data = self.ser.read(32)
        finally:
            self.ser.timeout = old
        return parse_status(data) if len(data) == 32 else None

    def status(self) -> Status:
        self.ser.reset_input_buffer()
        self.write(INVALIDATE + INITIALIZE + STATUS_REQUEST)
        st = self.read_status()
        if st is None:
            raise TimeoutError("Keine Statusantwort vom Drucker (eingeschaltet und verbunden?)")
        return st

    def print_job(self, job: bytes, wait: float = 30.0) -> Status | None:
        """Auftrag senden und auf 'Druck abgeschlossen' bzw. Fehler warten."""
        self.ser.reset_input_buffer()
        self.write(job)
        deadline = time.monotonic() + wait
        last = None
        while time.monotonic() < deadline:
            st = self.read_status(timeout=max(0.5, deadline - time.monotonic()))
            if st is None:
                continue
            last = st
            if st.status_type_code in (0x01, 0x02):  # fertig oder Fehler
                return st
        return last
