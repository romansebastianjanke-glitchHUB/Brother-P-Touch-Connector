"""Etiketten-Studio: lokale Weboberfläche mit Live-Vorschau und Druckknopf.

Start:  python -m ptouch_connector.studio [--home ORDNER] [--port 8765] [--no-browser]

Der Webserver läuft in einem Hintergrund-Thread, alle Druckerzugriffe laufen
nacheinander im Haupt-Thread (dort arbeitet das macOS-Bluetooth-Framework zuverlässig).
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import queue
import re
import sys
import threading
import time
import traceback
import webbrowser
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import layout, protocol, render
from .transport import open_printer

STATIC = Path(__file__).parent / "static"
DEFAULT_TRANSPORT = "bt" if sys.platform == "darwin" else "serial"


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Studio:
    def __init__(self, home: Path):
        self.home = home
        self.templates = home / "vorlagen"
        self.jobs: queue.Queue = queue.Queue()
        self.status: dict = {"known": False}
        self.busy = False
        self.stop = threading.Event()
        t = home / ".transport"
        self.transport = t.read_text().strip() if t.exists() else DEFAULT_TRANSPORT
        self.history = home / "verlauf"
        self.settings_file = home / ".einstellungen.json"
        self.settings = {"mirror": False, "length_offset_mm": 0.0}
        try:
            self.settings.update(json.loads(self.settings_file.read_text()))
        except Exception:  # noqa: BLE001
            pass
        # ".flip" glich einen inzwischen behobenen Spiegelfehler aus -> entfernen
        legacy = home / ".flip"
        if legacy.exists():
            legacy.unlink()
            log("Alte Einstellung „.flip“ entfernt (Ausrichtung ist jetzt im Programm korrigiert).")

    def save_settings(self, data: dict) -> dict:
        if "mirror" in data:
            self.settings["mirror"] = bool(data["mirror"])
        if "length_offset_mm" in data:
            self.settings["length_offset_mm"] = max(-50.0, min(50.0, float(data["length_offset_mm"] or 0)))
        self.settings_file.write_text(json.dumps(self.settings, indent=1))
        return self.settings

    def _prep(self, spec: dict) -> dict:
        """Geräte-Einstellungen in die Etikettbeschreibung übernehmen."""
        spec = dict(spec)
        spec["_length_offset_mm"] = float(self.settings.get("length_offset_mm", 0) or 0)
        return spec

    # --- Drucker-Thread (Haupt-Thread) ------------------------------------------

    def submit(self, fn, timeout: float = 180.0):
        fut: Future = Future()
        self.jobs.put((fn, fut))
        return fut.result(timeout=timeout)

    def run_worker(self) -> None:
        while not self.stop.is_set():
            try:
                fn, fut = self.jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            self.busy = True
            try:
                fut.set_result(fn())
            except Exception as e:  # noqa: BLE001
                log(f"FEHLER: {type(e).__name__}: {e}")
                fut.set_exception(e)
            finally:
                self.busy = False

    def _status_dict(self, st: protocol.Status) -> dict:
        return {
            "known": True,
            "ok": st.ok,
            "errors": st.errors,
            "tape_mm": st.media_width_mm,
            "media": st.media_type,
            "media_code": st.media_type_code,
            "tape_color": st.tape_color[0],
            "tape_css": st.tape_color[1],
            "text_color": st.text_color[0],
            "text_css": st.text_color[1],
            "time": time.strftime("%H:%M:%S"),
        }

    def do_status(self) -> dict:
        log(f"Statusabfrage ({self.transport}) …")
        try:
            with open_printer(self.transport, log=lambda m: None) as p:
                st = p.status()
            self.status = self._status_dict(st)
            log(f"Drucker: {st.media_width_mm} mm, {st.tape_color[0]}, Fehler: {st.errors or 'keine'}")
        except Exception as e:  # noqa: BLE001
            self.status = {"known": False, "error": f"{e}", "time": time.strftime("%H:%M:%S")}
            log(f"Drucker nicht erreichbar: {e}")
        return self.status

    def do_print(self, spec: dict) -> dict:
        original = spec
        spec = self._prep(spec)
        messages: list[str] = []
        with open_printer(self.transport, log=lambda m: None) as p:
            st = p.status()
            self.status = self._status_dict(st)
            if not st.ok:
                raise RuntimeError("Drucker meldet: " + ", ".join(st.errors or [st.status_type]))
            loaded = st.media_width_mm
            tape = spec.get("tape", "auto")
            if tape == "auto":
                if not loaded:
                    raise RuntimeError("Bandbreite nicht erkannt – bitte in den Einstellungen wählen.")
                tape = loaded
            tape = float(tape)
            if loaded and loaded != tape:
                raise RuntimeError(f"Eingelegt ist {loaded:g} mm-Band, gewählt sind {tape:g} mm. "
                                   "Band wechseln oder „Automatisch“ wählen.")
            images, warnings, values = layout.render_all(spec, tape)
            copies = max(1, min(99, int(spec.get("copies", 1))))
            mirror = bool(self.settings.get("mirror"))
            pages = []
            for img in images:
                lines = render.to_raster_lines(img, tape, mirror=mirror)
                pages += [lines] * copies
            cut = spec.get("cut", "full")
            opts = dict(
                media_type=st.media_type_code or 0x01,
                margin_dots=layout.margin_dots(spec),
                auto_cut=cut != "none",
                half_cut=cut == "half",
            )
            total = len(pages)
            log(f"Drucke {total} Etikett(en) auf {tape:g} mm …")
            if spec.get("chain") and total > 1:
                job = protocol.build_multi_job(pages, tape, **opts)
                res = p.print_job(job, expect=total)
                self._check(res, messages, f"{total} Etiketten")
            else:
                for i, lines in enumerate(pages, 1):
                    res = p.print_job(protocol.build_job(lines, tape, **opts))
                    self._check(res, messages, f"Etikett {i}/{total}")
        if len(messages) > 3 and all(m.endswith(": gedruckt") for m in messages):
            messages = [f"{len(messages)} Etiketten gedruckt"]
        log("Fertig: " + " | ".join(messages))
        try:
            self.add_history(original, images, values, tape, total)
        except Exception as e:  # noqa: BLE001
            log(f"Verlauf konnte nicht gespeichert werden: {e}")
        return {"ok": True, "messages": messages, "warnings": warnings, "status": self.status}

    @staticmethod
    def _check(res, messages: list[str], what: str) -> None:
        if res is None:
            messages.append(f"{what}: gesendet (keine Rückmeldung)")
        elif not res.ok:
            raise RuntimeError(f"{what}: " + ", ".join(res.errors or [res.status_type]))
        else:
            messages.append(f"{what}: gedruckt")

    # --- Vorschau (im Web-Thread, ohne Drucker) ---------------------------------

    def preview(self, spec: dict) -> dict:
        spec = self._prep(spec)
        tape = spec.get("tape", "auto")
        if tape == "auto":
            tape = self.status.get("tape_mm") or 12
        tape = float(tape)
        left, pins = protocol.tape_spec(tape)
        images, warnings, values = layout.render_all(spec, tape)
        out = []
        out = [_png(img) for img in images[:120]]
        w = images[0].width
        margin = layout.margin_dots(spec)
        return {
            "images": out,
            "count": len(images),
            "values": values[:120],
            "copies": int(spec.get("copies", 1)),
            "tape_mm": tape,
            "pins": pins,
            "tape_px": round(tape * layout.MM),
            "width_px": w,
            "content_mm": round(w / layout.MM, 1),
            "margin_px": margin,
            "length_mm": round(layout.total_length_mm(spec, w), 1),
            "warnings": warnings,
        }

    # --- Verlauf -------------------------------------------------------------------

    def add_history(self, spec: dict, images, values, tape: float, total: int) -> None:
        self.history.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        vals = [v for v in values if v is not None]
        title = _title(spec, vals)
        entry = {
            "id": f"{stamp}-{int(time.time() * 1000) % 1000:03d}",
            "time": time.strftime("%d.%m.%Y %H:%M"),
            "title": title,
            "count": total,
            "labels": len(images),
            "tape_mm": tape,
            "thumb": _png(images[0]),
            "spec": spec,
        }
        (self.history / f"{entry['id']}.json").write_text(json.dumps(entry, ensure_ascii=False))
        files = sorted(self.history.glob("*.json"))
        for old in files[:-300]:  # höchstens 300 Einträge behalten
            old.unlink()

    def list_history(self) -> list[dict]:
        if not self.history.is_dir():
            return []
        out = []
        for f in sorted(self.history.glob("*.json"), reverse=True):
            try:
                e = json.loads(f.read_text())
                e.pop("spec", None)
                out.append(e)
            except Exception:  # noqa: BLE001
                continue
        return out

    def _history_path(self, hid: str) -> Path:
        if not re.fullmatch(r"[\w-]+", hid or ""):
            raise ValueError("ungültiger Eintrag")
        return self.history / f"{hid}.json"

    # --- Vorlagen ----------------------------------------------------------------

    def _tpl_path(self, name: str) -> Path:
        safe = re.sub(r"[^\w äöüÄÖÜß.-]", "_", name).strip()[:80] or "Vorlage"
        return self.templates / f"{safe}.json"

    def list_templates(self) -> list[str]:
        if not self.templates.is_dir():
            return []
        return sorted(p.stem for p in self.templates.glob("*.json"))


def _png(img) -> str:
    buf = io.BytesIO()
    img.convert("1").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _title(spec: dict, values: list) -> str:
    texts = []
    for it in spec.get("items", []):
        t = it.get("text") if it.get("type") in ("text", "number") else it.get("data") if it.get("type") in ("qr", "barcode") else None
        if t and t.strip() and t.strip() != "{n}":
            texts.append(t.replace("{n}", "#").split("\n")[0].strip())
    title = " · ".join(texts[:2])[:60]
    if values:
        rng = values[0] if len(values) == 1 else f"{values[0]} – {values[-1]}"
        title = f"{rng}  {title}".strip()
    return title or "Etikett"


def make_handler(studio: Studio):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # Zugriffe nicht protokollieren
            pass

        def _json(self, data, code: int = 200):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def _file(self, path: Path, ctype: str):
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path in ("/", "/index.html"):
                    return self._file(STATIC / "index.html", "text/html; charset=utf-8")
                if u.path == "/api/config":
                    return self._json({
                        "fonts": list(layout.list_fonts().keys()),
                        "tapes": list(protocol.TAPE_SPECS.keys()),
                        "settings": studio.settings,
                        "transport": studio.transport,
                        "status": studio.status,
                        "templates": studio.list_templates(),
                        "defaults": layout.DEFAULT_SPEC,
                    })
                if u.path == "/api/status":
                    if q.get("refresh"):
                        return self._json(studio.submit(studio.do_status, timeout=90))
                    return self._json({**studio.status, "busy": studio.busy})
                if u.path == "/api/template":
                    p = studio._tpl_path(q.get("name", [""])[0])
                    return self._json(json.loads(p.read_text()))
                if u.path == "/api/history":
                    return self._json(studio.list_history())
                if u.path == "/api/history/item":
                    p = studio._history_path(q.get("id", [""])[0])
                    return self._json(json.loads(p.read_text()))
                self._json({"error": "nicht gefunden"}, 404)
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

        def do_POST(self):
            u = urlparse(self.path)
            try:
                data = self._body()
                if u.path == "/api/preview":
                    return self._json(studio.preview(data))
                if u.path == "/api/print":
                    try:
                        return self._json(studio.submit(lambda: studio.do_print(data)))
                    except Exception as e:  # noqa: BLE001
                        return self._json({"ok": False, "error": str(e), "status": studio.status})
                if u.path == "/api/template":
                    studio.templates.mkdir(exist_ok=True)
                    p = studio._tpl_path(str(data.get("name", "")))
                    p.write_text(json.dumps(data.get("spec", {}), ensure_ascii=False, indent=1))
                    return self._json({"ok": True, "templates": studio.list_templates()})
                if u.path == "/api/template/delete":
                    p = studio._tpl_path(str(data.get("name", "")))
                    if p.exists():
                        p.unlink()
                    return self._json({"ok": True, "templates": studio.list_templates()})
                if u.path == "/api/settings":
                    return self._json(studio.save_settings(data))
                if u.path == "/api/history/delete":
                    p = studio._history_path(str(data.get("id", "")))
                    if p.exists():
                        p.unlink()
                    return self._json({"ok": True})
                if u.path == "/api/quit":
                    self._json({"ok": True})
                    studio.stop.set()
                    return
                self._json({"error": "nicht gefunden"}, 404)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    return Handler


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="ptouch-studio")
    ap.add_argument("--home", default=".", help="Ordner für Vorlagen und Einstellungen")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    studio = Studio(Path(args.home).resolve())
    server = None
    for attempt in range(2):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(studio))
            break
        except OSError:
            if attempt:
                break
            # läuft noch ein älteres Studio? Dann beenden, damit die neue Version startet
            log("Beende das bereits laufende Studio …")
            try:
                import urllib.request
                req = urllib.request.Request(f"http://127.0.0.1:{args.port}/api/quit", b"{}",
                                             {"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5).read()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
    if server is None:
        sys.exit(f"Port {args.port} ist belegt – bitte das andere Studio-Fenster schließen und neu starten.")
    url = f"http://127.0.0.1:{args.port}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"Etiketten-Studio läuft: {url}  (Verbindung: {studio.transport})")
    log("Zum Beenden dieses Fenster schließen oder im Studio auf „Beenden“ klicken.")
    if not args.no_browser:
        webbrowser.open(url)
    # erste Statusabfrage im Hintergrund anstoßen
    threading.Thread(target=lambda: studio.submit(studio.do_status, timeout=120), daemon=True).start()
    try:
        studio.run_worker()
    except KeyboardInterrupt:
        pass
    server.shutdown()
    log("Studio beendet.")


if __name__ == "__main__":
    main()
