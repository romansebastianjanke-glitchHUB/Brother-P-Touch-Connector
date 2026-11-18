# Brother P-Touch Connector

Etiketten auf einem **Brother PT-P710BT** per **Bluetooth** drucken – vom Mac aus,
ohne Brother-Software. Spricht direkt das Raster-Protokoll des Druckers.

## Einrichtung (macOS)

1. **Drucker koppeln:** PT-P710BT einschalten, Bluetooth-Taste drücken (blaue LED),
   dann *Systemeinstellungen → Bluetooth* → „PT-P710BT…“ verbinden.
2. **Installieren:**

   ```bash
   git clone https://github.com/romansebastianjanke-glitchHUB/Brother-P-Touch-Connector.git
   cd Brother-P-Touch-Connector
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   ```

3. **Port prüfen:**

   ```bash
   ptouch ports
   ```

   Erwartet: etwas wie `/dev/cu.PT-P710BT1234`.

## Benutzung

```bash
ptouch status                         # Band, Akku, Fehler abfragen
ptouch preview "Hallo Welt"           # preview.png ansehen, nichts wird gedruckt
ptouch print "Hallo Welt"             # drucken (Bandbreite wird automatisch erkannt)
ptouch print "Zeile 1\nZeile 2"       # zweizeilig
ptouch print --image logo.png         # Bild drucken (wird auf Bandhöhe skaliert)
ptouch print "Kabel" --copies 5 --half-cut
```

Wichtige Optionen für `print`:

| Option | Wirkung |
|---|---|
| `--port` | Port manuell angeben |
| `--font` | eigene Schrift (`.ttf`/`.ttc`) |
| `--margin` | Rand vor/nach dem Text in Punkten (14 ≈ 2 mm) |
| `--no-cut` / `--half-cut` | Schnittverhalten |
| `--flip` | Ausdruck um 180° drehen, falls er verkehrt herum kommt |
| `--dry-run` | Druckauftrag nur als Datei schreiben |

## Unterstützte Bänder

TZe 3,5 / 6 / 9 / 12 / 18 / 24 mm (180 dpi, 128-Pin-Druckkopf).

## Fehlersuche

- **`ptouch ports` findet nichts:** Drucker in den Bluetooth-Einstellungen entfernen
  und neu koppeln; `ls /dev/cu.*` prüfen.
- **`Keine Statusantwort`:** Drucker ist evtl. eingeschlafen – kurz Ein/Aus drücken.
- **Text steht auf dem Kopf oder ist gespiegelt:** `--flip` versuchen und Bescheid geben.

## Entwicklung

```bash
pytest
```

Der Code ist in `src/ptouch_connector/`:
`protocol.py` (Raster-Befehle, Status), `render.py` (Text/Bild → Rasterzeilen),
`transport.py` (Bluetooth-Seriell), `cli.py` (Kommandozeile).
