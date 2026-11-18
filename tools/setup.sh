# Wird von den .command-Dateien eingebunden: Python-Umgebung prüfen und einrichten.
pause() { echo; read -r -p "Enter drücken, um das Fenster zu schließen …" _; exit "${1:-0}"; }

ptouch_setup() {
    if ! python3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >/dev/null 2>&1; then
        echo "Python 3 fehlt noch."
        echo "macOS zeigt gleich ein Fenster zur Installation der 'Befehlszeilentools'."
        echo "Dort auf 'Installieren' klicken, warten bis es fertig ist,"
        echo "und danach diese Datei noch einmal doppelklicken."
        xcode-select --install >/dev/null 2>&1
        pause 1
    fi
    if [ ! -x .venv/bin/python ]; then
        echo "Erster Start: richte alles ein (dauert ca. 1 Minute, braucht Internet) …"
        python3 -m venv .venv || { echo "FEHLER: Konnte .venv nicht anlegen."; pause 1; }
        .venv/bin/python -m pip install --quiet --upgrade pip
    fi
    if ! .venv/bin/python -c "import PIL, serial, qrcode, barcode" >/dev/null 2>&1; then
        echo "Installiere Bausteine (einmalig) …"
        if ! .venv/bin/python -m pip install --quiet "pillow>=10.1" "pyserial>=3.5" "qrcode>=7.4" "python-barcode>=0.15"; then
            echo "FEHLER: Installation fehlgeschlagen. Internetverbindung prüfen und erneut starten."
            pause 1
        fi
    fi
    if [ "$(uname)" = "Darwin" ] && ! .venv/bin/python -c "import IOBluetooth" >/dev/null 2>&1; then
        echo "Installiere Bluetooth-Baustein (einmalig) …"
        .venv/bin/python -m pip install --quiet --only-binary :all: "pyobjc-framework-IOBluetooth>=10,<12" \
            || echo "Hinweis: Bluetooth-Baustein konnte nicht installiert werden."
    fi
}

ptouch() { PYTHONUNBUFFERED=1 PYTHONPATH="$PWD/src" .venv/bin/python -m ptouch_connector.cli "$@"; }
