#!/bin/bash
# Doppelklick: startet das Etiketten-Studio im Browser (Vorschau, Gestaltung, Drucken).
# Das Terminal-Fenster muss offen bleiben, solange das Studio benutzt wird.

cd "$(dirname "$0")" || exit 1
SELF="$(basename "$0")"

. tools/update.sh
ptouch_update

exec > >(tee "studio-log.txt") 2>&1
. tools/setup.sh

echo "==============================================="
echo "  Etiketten-Studio für den Brother PT-P710BT"
echo "==============================================="
echo
ptouch_setup
echo
echo "Das Studio öffnet sich gleich im Browser."
echo "Dieses Fenster bitte offen lassen – Schließen beendet das Studio."
echo

PYTHONUNBUFFERED=1 PYTHONPATH="$PWD/src" .venv/bin/python -m ptouch_connector.studio --home "$PWD"
echo
echo "Studio beendet."
pause 0
