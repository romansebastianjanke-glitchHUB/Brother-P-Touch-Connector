"""Kommandozeile: `ptouch ports | diagnose | status | preview | print`."""

from __future__ import annotations

import argparse
import sys

from . import protocol, render
from .transport import find_ports, open_printer


def _open(args):
    print(f"Verbinde ({args.transport}) …", flush=True)
    return open_printer(args.transport, port=getattr(args, "port", None))


def _image(args, tape: float):
    if args.image:
        return render.load_image(args.image, tape)
    text = args.text.replace("\\n", "\n")
    return render.render_text(text, tape, font_path=args.font)


def cmd_ports(args) -> None:
    ports = find_ports()
    print("\n".join(ports) if ports else "Keine passenden Ports gefunden.")


def cmd_status(args) -> None:
    with _open(args) as p:
        print(p.status().describe())


def cmd_diagnose(args) -> None:
    """Beide Verbindungswege ausprobieren; den funktionierenden in --save schreiben."""
    import platform
    import traceback

    print(f"System: macOS {platform.mac_ver()[0] or '?'} / Python {platform.python_version()}")
    print(f"Serielle Schnittstellen: {', '.join(find_ports()) or 'keine'}")
    try:
        from .transport import paired_devices

        devs = paired_devices()
        print("Gekoppelte Bluetooth-Geräte:")
        for name, addr in devs:
            print(f"  - {name} ({addr})")
    except Exception as e:
        print(f"Bluetooth-Geräteliste nicht verfügbar: {type(e).__name__}: {e}")

    for transport in ("serial", "bt"):
        print(f"\n--- Versuch über '{transport}' ---", flush=True)
        try:
            with open_printer(transport) as p:
                st = p.status()
            print(st.describe())
            print(f"\nERFOLG über '{transport}'.")
            if args.save:
                with open(args.save, "w") as f:
                    f.write(transport)
            return
        except Exception as e:
            print(f"Fehlgeschlagen: {type(e).__name__}: {e}")
            if args.verbose:
                traceback.print_exc(file=sys.stdout)
    sys.exit("\nKein Verbindungsweg hat funktioniert.")


def cmd_preview(args) -> None:
    img = _image(args, args.tape)
    render.preview(img, args.out)
    length_mm = img.width / protocol.DPI * 25.4
    print(f"Vorschau: {args.out} ({img.width} Zeilen ≈ {length_mm:.0f} mm + Ränder)")


def cmd_print(args) -> None:
    tape = args.tape
    media_type = 0x01
    printer = None
    if not args.dry_run:
        printer = _open(args)
        if args.no_status:
            st = None
            print("Drucke ohne Statusabfrage …")
        else:
            st = printer.status()
        if st and not st.ok:
            printer.close()
            sys.exit(f"Drucker meldet Fehler:\n{st.describe()}")
        if st and st.media_width_mm:
            if tape and tape != st.media_width_mm:
                print(f"Hinweis: eingelegt ist {st.media_width_mm:g} mm, nicht {tape:g} mm – "
                      "nehme eingelegtes Band.", file=sys.stderr)
            tape = st.media_width_mm
            media_type = st.media_type_code
    if not tape:
        tape = 12
    img = _image(args, tape)
    lines = render.to_raster_lines(img, tape, flip=args.flip)
    job = protocol.build_job(
        lines,
        tape,
        media_type=media_type,
        margin_dots=args.margin,
        auto_cut=not args.no_cut,
        half_cut=args.half_cut,
    )
    if args.dry_run:
        out = args.out or "job.bin"
        with open(out, "wb") as f:
            f.write(job)
        print(f"Testlauf: {len(job)} Bytes nach {out} geschrieben ({tape:g} mm Band).")
        return
    try:
        for i in range(args.copies):
            st = printer.print_job(job, wait=8 if args.no_status else 30)
            if st is None:
                print("Daten gesendet, aber keine Rückmeldung vom Drucker – bitte Ausdruck prüfen.")
            elif not st.ok:
                sys.exit(f"Fehler beim Druck:\n{st.describe()}")
            else:
                print(f"Etikett {i + 1}/{args.copies} gedruckt ({tape:g} mm).")
    finally:
        printer.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="ptouch", description="Brother PT-P710BT über Bluetooth")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_port(p):
        p.add_argument("--transport", choices=["serial", "bt"], default="serial",
                       help="serial = /dev/cu.PT-…, bt = direkter Bluetooth-Kanal (macOS)")
        p.add_argument("--port", help="z. B. /dev/cu.PT-P710BT1234 (nur serial)")

    def add_content(p):
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("text", nargs="?", help="Etikettentext, \\n für Zeilenumbruch")
        g.add_argument("--image", help="Bilddatei statt Text")
        p.add_argument("--font", help="Pfad zu einer TTF/TTC-Schrift")
        p.add_argument("--tape", type=float, choices=list(protocol.TAPE_SPECS),
                       help="Bandbreite in mm (beim Drucken automatisch erkannt)")

    p = sub.add_parser("ports", help="Bluetooth-Ports auflisten")
    p.set_defaults(func=cmd_ports)

    p = sub.add_parser("diagnose", help="Verbindungswege testen")
    p.add_argument("--save", help="funktionierenden Weg in diese Datei schreiben")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("status", help="Druckerstatus abfragen")
    add_port(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("preview", help="Vorschau als PNG speichern, ohne zu drucken")
    add_content(p)
    p.add_argument("--out", default="preview.png")
    p.set_defaults(func=cmd_preview, tape=12)

    p = sub.add_parser("print", help="Etikett drucken")
    add_content(p)
    add_port(p)
    p.add_argument("--copies", type=int, default=1)
    p.add_argument("--margin", type=int, default=14, help="Rand in Punkten (14 ≈ 2 mm)")
    p.add_argument("--no-cut", action="store_true", help="nicht automatisch abschneiden")
    p.add_argument("--half-cut", action="store_true", help="Halbschnitt (Trägerpapier bleibt)")
    p.add_argument("--flip", action="store_true", help="Druck um 180° drehen, falls verkehrt")
    p.add_argument("--no-status", action="store_true", help="ohne Statusabfrage drucken (--tape angeben)")
    p.add_argument("--dry-run", action="store_true", help="nur Auftrag als Datei schreiben")
    p.add_argument("--out", help="Zieldatei für --dry-run")
    p.set_defaults(func=cmd_print)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except TimeoutError as e:
        sys.exit(f"FEHLER: {e}")
    except OSError as e:  # u. a. serial.SerialException
        sys.exit(f"FEHLER bei der Verbindung: {e}")


if __name__ == "__main__":
    main()
