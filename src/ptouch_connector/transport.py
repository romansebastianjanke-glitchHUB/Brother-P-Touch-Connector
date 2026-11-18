"""Verbindung zum Drucker über Bluetooth.

Zwei Wege:
- "serial": die serielle Schnittstelle /dev/cu.PT-P710BT…, die macOS nach dem Koppeln anlegt.
  Auf neueren macOS-Versionen kommen darüber teils keine Daten an.
- "bt": direkter RFCOMM-Kanal über das macOS-Framework IOBluetooth (PyObjC).
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
    """Gemeinsame Druckerlogik; Unterklassen liefern nur _send/_recv/close."""

    name = "?"

    def _send(self, data: bytes) -> None:
        raise NotImplementedError

    def _recv(self, timeout: float) -> bytes:
        raise NotImplementedError

    def _drain(self) -> None:
        while self._recv(0.05):
            pass

    def close(self) -> None:
        pass

    def __enter__(self) -> "Printer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @staticmethod
    def _find_status(buf: bytearray) -> Status | None:
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
        while True:
            st = self._find_status(buf)
            if st is not None:
                return st
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            buf += self._recv(min(0.5, remaining))

    def status(self, attempts: int = 3, timeout: float = 5.0) -> Status:
        self._drain()
        buf = bytearray()
        received = 0
        for _ in range(attempts):
            self._send(INVALIDATE + INITIALIZE + STATUS_REQUEST)
            before = len(buf)
            st = self._read_until_status(buf, timeout)
            if st is not None:
                return st
            received += len(buf) - before
        detail = f"{received} Bytes empfangen" + (f": {bytes(buf[:64]).hex()}" if buf else "")
        raise TimeoutError(f"[{self.name}] keine gültige Statusantwort nach {attempts} Versuchen ({detail})")

    def print_job(self, job: bytes, wait: float = 30.0) -> Status | None:
        """Auftrag senden und auf 'Druck abgeschlossen' bzw. Fehler warten."""
        self._drain()
        self._send(job)
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


class SerialPrinter(Printer):
    name = "serial"

    def __init__(self, port: str, settle: float = 2.0):
        import serial

        self.port = port
        self.ser = serial.Serial(port, baudrate=115200, timeout=0.5, write_timeout=10)
        # Unter macOS wird die Bluetooth-Verbindung erst beim Öffnen aufgebaut.
        time.sleep(settle)

    def _send(self, data: bytes, chunk: int = 1024) -> None:
        for i in range(0, len(data), chunk):
            self.ser.write(data[i : i + chunk])
            self.ser.flush()

    def _recv(self, timeout: float) -> bytes:
        self.ser.timeout = timeout
        return self.ser.read(256)

    def close(self) -> None:
        self.ser.close()


# --- IOBluetooth (nur macOS) ---------------------------------------------------

SERIAL_PORT_UUID16 = 0x1101


def _run_loop(seconds: float) -> None:
    from Foundation import NSDate, NSRunLoop

    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


def _ok(ret) -> bool:
    return int(ret) == 0


def paired_devices() -> list[tuple[str, str]]:
    """(Name, Adresse) aller gekoppelten Bluetooth-Geräte."""
    import IOBluetooth

    devs = IOBluetooth.IOBluetoothDevice.pairedDevices() or []
    return [(str(d.name() or "?"), str(d.addressString())) for d in devs]


def find_bt_device(name_part: str = "P710"):
    import IOBluetooth

    for d in IOBluetooth.IOBluetoothDevice.pairedDevices() or []:
        if name_part.lower() in str(d.name() or "").lower():
            return d
    return None


_DELEGATE_CLASS = None


def _make_delegate(sink: bytearray, log):
    # Objective-C-Klassen dürfen nur einmal pro Prozess definiert werden
    global _DELEGATE_CLASS
    if _DELEGATE_CLASS is None:
        from Foundation import NSObject

        class PTouchRFCOMMDelegate(NSObject):
            def rfcommChannelData_data_length_(self, channel, data, length):
                try:
                    if isinstance(data, (bytes, bytearray)):
                        chunk = bytes(data[:length])
                    elif hasattr(data, "as_buffer"):
                        chunk = bytes(data.as_buffer(length))
                    else:
                        chunk = bytes(data[i] for i in range(length))
                    self.sink.extend(chunk)
                except Exception as e:  # nur protokollieren
                    self.log(f"  (Empfang nicht lesbar: {type(data).__name__}: {e})")

            def rfcommChannelOpenComplete_status_(self, channel, status):
                self.log(f"  RFCOMM-Kanal geöffnet, Status {int(status)}")

            def rfcommChannelClosed_(self, channel):
                self.log("  RFCOMM-Kanal wurde geschlossen")

        _DELEGATE_CLASS = PTouchRFCOMMDelegate
    d = _DELEGATE_CLASS.alloc().init()
    d.sink = sink
    d.log = log
    return d


class RfcommPrinter(Printer):
    name = "bt"

    def __init__(self, device=None, channel_id: int | None = None, log=print, settle: float = 1.0):
        import IOBluetooth

        self.log = log
        self.dev = device or find_bt_device()
        if self.dev is None:
            raise OSError("Kein gekoppeltes Bluetooth-Gerät mit 'P710' im Namen gefunden")
        log(f"  Gerät: {self.dev.name()} ({self.dev.addressString()})")

        if not self.dev.isConnected():
            ret = self.dev.openConnection()
            log(f"  Basisverbindung: {'ok' if _ok(ret) else f'Fehler {int(ret)}'}")

        if channel_id is None:
            channel_id = self._sdp_channel(IOBluetooth)
        log(f"  RFCOMM-Kanal: {channel_id}")

        self.inbox = bytearray()
        self.delegate = _make_delegate(self.inbox, log)
        result = self.dev.openRFCOMMChannelSync_withChannelID_delegate_(None, channel_id, self.delegate)
        ret, self.channel = result if isinstance(result, tuple) else (result, None)
        if not _ok(ret) or self.channel is None:
            raise OSError(f"RFCOMM-Kanal {channel_id} ließ sich nicht öffnen (Fehler {int(ret)})")
        self.mtu = max(1, min(int(self.channel.getMTU() or 127), 1000))
        _run_loop(settle)

    def _sdp_channel(self, IOBluetooth) -> int:
        uuid = IOBluetooth.IOBluetoothSDPUUID.uuid16_(SERIAL_PORT_UUID16)
        rec = self.dev.getServiceRecordForUUID_(uuid)
        if rec is None:
            self.log("  Dienstliste wird abgefragt …")
            self.dev.performSDPQuery_(None)
            for _ in range(50):
                _run_loop(0.1)
                rec = self.dev.getServiceRecordForUUID_(uuid)
                if rec is not None:
                    break
        if rec is None:
            self.log("  Kein Serial-Port-Dienst gefunden, versuche Kanal 1")
            return 1
        res = rec.getRFCOMMChannelID_(None)
        ret, cid = res if isinstance(res, tuple) else (res, None)
        if not _ok(ret) or not cid:
            self.log("  Kanal nicht ermittelbar, versuche Kanal 1")
            return 1
        return int(cid)

    def _send(self, data: bytes) -> None:
        for i in range(0, len(data), self.mtu):
            chunk = data[i : i + self.mtu]
            ret = self.channel.writeSync_length_(chunk, len(chunk))
            if not _ok(ret):
                raise OSError(f"Senden über Bluetooth fehlgeschlagen (Fehler {int(ret)})")
            _run_loop(0.001)

    def _recv(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while not self.inbox and time.monotonic() < deadline:
            _run_loop(0.05)
        data = bytes(self.inbox)
        self.inbox.clear()
        return data

    def close(self) -> None:
        try:
            if getattr(self, "channel", None) is not None:
                self.channel.closeChannel()
                _run_loop(0.2)
        except Exception:
            pass


def open_printer(transport: str, port: str | None = None, log=print) -> Printer:
    if transport == "serial":
        if not port:
            ports = find_ports()
            if not ports:
                raise OSError("Keine serielle Drucker-Schnittstelle gefunden")
            port = ports[0]
        return SerialPrinter(port)
    if transport == "bt":
        return RfcommPrinter(log=log)
    raise ValueError(f"Unbekannter Transport: {transport}")
