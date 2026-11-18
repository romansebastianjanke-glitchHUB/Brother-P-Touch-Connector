#!/bin/bash
# Doppelklick-Starter für den Brother PT-P710BT (macOS).
# Holt sich bei jedem Start die neueste Version von GitHub, richtet beim ersten Start
# alles im Ordner ".venv" ein und fragt dann nach Etikettentexten.
# Alles, was im Fenster erscheint, wird zusätzlich in "letzter-lauf.txt" gespeichert.

cd "$(dirname "$0")" || exit 1
SELF="$(basename "$0")"
ZIP_URL="https://codeload.github.com/romansebastianjanke-glitchHUB/Brother-P-Touch-Connector/zip/refs/heads/main"

# 0) Aktualisieren (einmal pro Start; ohne Internet einfach weiter)
if [ -z "$PTOUCH_UPDATED" ]; then
    echo "Suche nach Aktualisierungen …"
    TMP="$(mktemp -d)"
    if curl -fsSL --max-time 20 "$ZIP_URL" -o "$TMP/u.zip" 2>/dev/null && unzip -q "$TMP/u.zip" -d "$TMP" 2>/dev/null; then
        NEW="$(find "$TMP" -maxdepth 1 -type d -name 'Brother-P-Touch-Connector-*' | head -1)"
        if [ -n "$NEW" ] && [ -d "$NEW/src" ] && [ -f "$NEW/$SELF" ]; then
            rm -rf src.alt && mv src src.alt 2>/dev/null
            if cp -R "$NEW/src" ./src; then rm -rf src.alt; else rm -rf src; mv src.alt src; fi
            cp "$NEW/README.md" ./README.md 2>/dev/null
            chmod +x "$NEW/$SELF"
            # neue Datei per mv einsetzen, damit dieses laufende Skript nicht kaputtgeht
            mv -f "$NEW/$SELF" "./$SELF"
            echo "Auf dem neuesten Stand."
            rm -rf "$TMP"
            PTOUCH_UPDATED=1 exec /bin/bash "./$SELF"
        fi
    fi
    rm -rf "$TMP"
    echo "(Keine Aktualisierung möglich – mache mit der vorhandenen Version weiter.)"
fi

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

# 2b) Bluetooth-Baustein für den direkten Weg (einmalig, ca. 20 MB)
if ! .venv/bin/python -c "import IOBluetooth" >/dev/null 2>&1; then
    echo "Installiere Bluetooth-Baustein (einmalig) …"
    .venv/bin/python -m pip install --quiet --only-binary :all: "pyobjc-framework-IOBluetooth>=10,<12" \
        || echo "Hinweis: Bluetooth-Baustein konnte nicht installiert werden."
    echo
fi

ptouch() { PYTHONUNBUFFERED=1 PYTHONPATH="$PWD/src" .venv/bin/python -m ptouch_connector.cli "$@"; }

# 3) Verbindung testen
echo "Verbinde mit dem Drucker …"
echo "Falls macOS fragt, ob Terminal Bluetooth verwenden darf: bitte 'Erlauben'."
echo
EXTRA=()
TRANSPORT=""
[ -f .transport ] && TRANSPORT="$(cat .transport)"
if [ -n "$TRANSPORT" ] && ptouch status --transport "$TRANSPORT"; then
    :
elif echo "Teste alle Verbindungswege (dauert bis zu 1 Minute) …" && ptouch diagnose --save .transport --verbose; then
    TRANSPORT="$(cat .transport)"
else
    TRANSPORT="bt"
    echo
    echo "Der Drucker antwortet auf keinem Weg."
    echo "Bitte prüfen: Drucker an? Handy-App (Brother) getrennt?"
    echo
    echo "Wir können trotzdem einen Testdruck über den direkten Bluetooth-Weg versuchen."
    read -r -p "Welche Bandbreite ist eingelegt? (z. B. 12 – leer lassen = abbrechen): " TAPE
    echo "(Bandbreite: $TAPE)"
    [ -z "$TAPE" ] && pause 1
    EXTRA=(--no-status --tape "$TAPE")
fi
EXTRA+=(--transport "$TRANSPORT")

# 5) Drucken
FLIP=()
[ -f .flip ] && FLIP=(--flip)
while true; do
    echo
    echo "-----------------------------------------------"
    echo "Etikettentext eingeben und Enter drücken."
    echo "Zweite Zeile: \\n dazwischen schreiben, z. B.  Kabel\\nRouter"
    read -r -p "Text (leer lassen + Enter = beenden): " TEXT
    [ -z "$TEXT" ] && break
    echo "(Text: $TEXT)"
    ptouch print "${FLIP[@]}" "${EXTRA[@]}" -- "$TEXT"

    if [ ! -f .ausrichtung-geprueft ]; then
        echo
        read -r -p "Kam ein Etikett heraus und steht der Text richtig herum? (j = ja / n = verkehrt / x = kein Etikett): " OK
        echo "(Antwort: $OK)"
        case "$OK" in
            n|N)
                if [ ${#FLIP[@]} -gt 0 ]; then FLIP=(); rm -f .flip; else FLIP=(--flip); touch .flip; fi
                echo "Gemerkt – Ausrichtung geändert. Bitte einmal neu testen." ;;
            j|J) touch .ausrichtung-geprueft ;;
            *) echo "Notiert. Bitte 'letzter-lauf.txt' an Claude schicken." ;;
        esac
    fi
done

echo
echo "Fertig. Die Ausgabe steht auch in 'letzter-lauf.txt' in diesem Ordner."
pause 0
