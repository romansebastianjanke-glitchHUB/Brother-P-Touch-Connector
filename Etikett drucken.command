#!/bin/bash
# Doppelklick-Starter für den Brother PT-P710BT (macOS).
# Richtet beim ersten Start alles im Ordner ".venv" ein und fragt dann nach Etikettentexten.
# Alles, was im Fenster erscheint, wird zusätzlich in "letzter-lauf.txt" gespeichert.

cd "$(dirname "$0")" || exit 1
exec > >(tee "letzter-lauf.txt") 2>&1

pause() { echo; read -r -p "Enter drücken, um das Fenster zu schließen …" _; exit "${1:-0}"; }

echo "==============================================="
echo "  Brother PT-P710BT – Etikett drucken"
echo "==============================================="
echo

# 1) Python vorhanden?
if ! python3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >/dev/null 2>&1; then
    echo "Python 3 fehlt noch."
    echo "macOS zeigt gleich ein Fenster zur Installation der 'Befehlszeilentools'."
    echo "Dort auf 'Installieren' klicken, warten bis es fertig ist,"
    echo "und danach diese Datei noch einmal doppelklicken."
    xcode-select --install >/dev/null 2>&1
    pause 1
fi

# 2) Einmalige Einrichtung (nur in diesem Ordner)
if ! .venv/bin/python -c "import PIL, serial" >/dev/null 2>&1; then
    echo "Erster Start: richte alles ein (dauert ca. 1 Minute, braucht Internet) …"
    python3 -m venv .venv || { echo "FEHLER: Konnte .venv nicht anlegen."; pause 1; }
    .venv/bin/python -m pip install --quiet --upgrade pip
    if ! .venv/bin/python -m pip install --quiet "pillow>=10.1" "pyserial>=3.5"; then
        echo "FEHLER: Installation fehlgeschlagen. Internetverbindung prüfen und erneut starten."
        pause 1
    fi
    echo "Einrichtung fertig."
    echo
fi

ptouch() { PYTHONPATH="$PWD/src" .venv/bin/python -m ptouch_connector.cli "$@"; }

# 3) Drucker suchen
echo "Suche Drucker …"
PORTS=$(ptouch ports)
echo "$PORTS"
if ! echo "$PORTS" | grep -q "^/dev/"; then
    echo
    echo "Kein Drucker gefunden. Bitte prüfen:"
    echo "  1. Drucker eingeschaltet?"
    echo "  2. Systemeinstellungen → Bluetooth: steht 'PT-P710BT…' dort als verbunden?"
    echo "     Falls nicht: Bluetooth-Taste am Drucker drücken und dort 'Verbinden' klicken."
    echo
    echo "Vorhandene Schnittstellen (für die Fehlersuche):"
    ls /dev/cu.* 2>/dev/null
    pause 1
fi

# 4) Status
echo
echo "Frage Druckerstatus ab …"
if ! ptouch status; then
    echo
    echo "Der Drucker antwortet nicht. Drucker aus- und wieder einschalten und erneut versuchen."
    pause 1
fi

# 5) Drucken
FLIP=""
[ -f .flip ] && FLIP="--flip"
while true; do
    echo
    echo "-----------------------------------------------"
    echo "Etikettentext eingeben und Enter drücken."
    echo "Zweite Zeile: \\n dazwischen schreiben, z. B.  Kabel\\nRouter"
    read -r -p "Text (leer lassen + Enter = beenden): " TEXT
    [ -z "$TEXT" ] && break
    ptouch print $FLIP -- "$TEXT"

    if [ ! -f .ausrichtung-geprueft ]; then
        echo
        read -r -p "Steht der Text auf dem Etikett richtig herum? (j/n): " OK
        if [ "$OK" = "n" ] || [ "$OK" = "N" ]; then
            if [ -n "$FLIP" ]; then FLIP=""; rm -f .flip; else FLIP="--flip"; touch .flip; fi
            echo "Gemerkt – ab jetzt wird gedreht gedruckt. Bitte einmal neu testen."
        else
            touch .ausrichtung-geprueft
        fi
    fi
done

echo
echo "Fertig. Die Ausgabe steht auch in 'letzter-lauf.txt' in diesem Ordner."
pause 0
