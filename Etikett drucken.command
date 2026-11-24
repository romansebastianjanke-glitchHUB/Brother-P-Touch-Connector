#!/bin/bash
# Doppelklick-Starter für den Brother PT-P710BT (macOS).
# Holt sich bei jedem Start die neueste Version von GitHub, richtet beim ersten Start
# alles im Ordner ".venv" ein und fragt dann nach Etikettentexten.
# Alles, was im Fenster erscheint, wird zusätzlich in "letzter-lauf.txt" gespeichert.

cd "$(dirname "$0")" || exit 1
SELF="$(basename "$0")"

# 0) Aktualisieren (ältere Versionen dieser Datei kennen tools/ noch nicht)
if [ -f tools/update.sh ]; then
    . tools/update.sh
    ptouch_update
elif [ -z "$PTOUCH_UPDATED2" ]; then
    # Übergang: einmal komplett neu holen, dann mit tools/ weitermachen
    ZIP_URL="https://codeload.github.com/romansebastianjanke-glitchHUB/Brother-P-Touch-Connector/zip/refs/heads/main"
    TMP="$(mktemp -d)"
    if curl -fsSL --max-time 30 "$ZIP_URL" -o "$TMP/u.zip" 2>/dev/null && unzip -q "$TMP/u.zip" -d "$TMP" 2>/dev/null; then
        NEW="$(find "$TMP" -maxdepth 1 -type d -name 'Brother-P-Touch-Connector-*' | head -1)"
        if [ -n "$NEW" ] && [ -d "$NEW/tools" ]; then
            rm -rf tools && cp -R "$NEW/tools" ./tools
            rm -rf "$TMP"
            exec /bin/bash "./$SELF"
        fi
    fi
    rm -rf "$TMP"
fi

exec > >(tee "letzter-lauf.txt") 2>&1
. tools/setup.sh

echo "==============================================="
echo "  Brother PT-P710BT – Etikett drucken"
echo "==============================================="
echo
ptouch_setup

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
# alte Einstellung ".flip" glich einen Fehler aus, der jetzt im Programm behoben ist
rm -f .flip
FLIP=()
[ -f .mirror ] && FLIP=(--mirror)
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
                if [ ${#FLIP[@]} -gt 0 ]; then FLIP=(); rm -f .mirror; else FLIP=(--mirror); touch .mirror; fi
                echo "Gemerkt – Ausrichtung geändert. Bitte einmal neu testen." ;;
            j|J) touch .ausrichtung-geprueft ;;
            *) echo "Notiert. Bitte 'letzter-lauf.txt' an Claude schicken." ;;
        esac
    fi
done

echo
echo "Fertig. Die Ausgabe steht auch in 'letzter-lauf.txt' in diesem Ordner."
pause 0
