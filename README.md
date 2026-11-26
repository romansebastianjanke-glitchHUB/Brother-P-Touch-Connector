# Brother P-Touch Connector

Etiketten auf einem **Brother PT-P710BT** per **Bluetooth** drucken – vom Mac aus,
ohne Brother-Software. Spricht direkt das Raster-Protokoll des Druckers.

## Etiketten-Studio (empfohlen)

**`Etiketten-Studio.command`** doppelklicken – im Browser öffnet sich eine Oberfläche mit
Live-Vorschau (in der echten Bandfarbe) und Druckknopf:

- **Schnellstart:** Nummernschild (große Nummer + Trennlinie) und QR-Nummer (QR mit 5-stelliger Zahl)
- **Elemente** nebeneinander: Nummer, Text, Bild, Symbol, QR-Code, Barcode, Abstand – verschieben, duplizieren
- **Nummer:** groß links oder rechts am Rand verankert, optional mit Trennlinie
- **Text:** jede installierte Schrift, automatische oder feste Größe, fett/kursiv/unterstrichen, mehrzeilig
- **Bilder:** kopieren und **⌘V** drücken oder ins Fenster ziehen – werden proportional aufs Band
  skaliert und in Schwarzweiß gerastert (Fotos) oder per Schwelle (Logos), mit Kontrast, Drehen, Invertieren
- **Symbole:** 30 eingebaute Icons oder beliebiges Emoji
- **QR-Codes** (inkl. WLAN-Zugang) und **Barcodes** (Code 128/39, EAN, UPC, ISBN)
- **Gestaltung:** Rahmen, invertiert, feste Etikettlänge, Abstände
- **Tabellen-Serien (Batches):** beliebige Spalten (z. B. `{name}`, `{nr}`, `{raum}`), aus Excel/Sheets
  einfügen (Tab oder `;` getrennt), Zeilen per Häkchen auswählen und nur die ausgewählten oder alle drucken,
  Filter, **einheitliche Schriftgröße** über alle Etiketten; als Vorlage speichern
- **Schnellstart Transponder:** 24 mm, 37 mm, drei Zeilen zentriert und fett
- **Serien:** fortlaufende Nummern oder eingefügte Liste, als bearbeitbare Liste unter der Vorschau –
  jeder Wert änderbar, jedes Etikett auf Wunsch komplett einzeln gestaltbar (Platzhalter `{n}`, `{datum}`)
- **Bandfarbe & Schriftfarbe** für die Vorschau wählbar (oder automatisch vom Drucker)
- **Druck:** Kopien, Schnitt/Halbschnitt, Rand, Serie als ein Auftrag; Spiegeln als Geräte-Einstellung
- **Anordnung per Ziehen:** jedes Element in die Bereiche Links / Mitte / Rechts ziehen
  (links/rechts bleiben bei fester Länge am Rand, optional mit Trennlinie)
- **Etikettlänge = echte Gesamtlänge** inkl. Rand; passt der Inhalt nicht, wird er automatisch verkleinert.
  Einmalige **Längenkorrektur** zum Einmessen (Tab Druck)
- **Verlauf:** jeder Druck wird gespeichert (Ordner `verlauf/`) – öffnen, nochmal drucken, suchen
- **Vorlagen** speichern und laden (Ordner `vorlagen/`)

Das Terminal-Fenster muss offen bleiben, solange das Studio läuft.

## Am einfachsten: per Doppelklick (ohne Terminal-Kenntnisse)

1. Auf der GitHub-Seite grüner Button **„Code“ → „Download ZIP“**, ZIP im Finder doppelklicken.
2. Drucker einschalten, Bluetooth-Taste drücken, dann *Systemeinstellungen → Bluetooth* →
   „PT-P710BT…“ **verbinden**.
3. Im entpackten Ordner **`Etikett drucken.command`** doppelklicken.
   - Meldet macOS *„kann nicht geöffnet werden, da sie von einem nicht verifizierten Entwickler stammt“*:
     Rechtsklick auf die Datei → **Öffnen** → **Öffnen**. Ab macOS 15 stattdessen:
     *Systemeinstellungen → Datenschutz & Sicherheit* → ganz unten **„Trotzdem öffnen“**.
   - Fragt macOS nach den **„Befehlszeilentools“**: installieren, danach erneut doppelklicken.
4. Beim ersten Start wird ca. 1 Minute eingerichtet, danach Text eingeben und Enter drücken.

Alles bleibt in diesem einen Ordner. **Rückgängig machen = Ordner löschen.**
Die Ausgabe jedes Laufs steht in `letzter-lauf.txt` – die kannst du bei Problemen weitergeben.

## Einrichtung über das Terminal (macOS)

1. **Drucker koppeln:** PT-P710BT einschalten, Bluetooth-Taste drücken (blaue LED),
   dann *Systemeinstellungen → Bluetooth* → „PT-P710BT…“ verbinden.
2. **Installieren:**

   ```bash
   git clone https://github.com/romansebastianjanke-glitchHUB/Brother-P-Touch-Connector.git
   cd Brother-P-Touch-Connector
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"   # Studio zusätzlich: pip install qrcode python-barcode
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

## Verbindungswege

- `--transport serial` (Standard): über `/dev/cu.PT-P710BT…`.
- `--transport bt`: direkter Bluetooth-Kanal über das macOS-Framework IOBluetooth
  (`pip install "pyobjc-framework-IOBluetooth>=10,<12"`). Nötig, wenn über die serielle
  Schnittstelle keine Daten ankommen.

`ptouch diagnose` probiert beide Wege und zeigt, welcher funktioniert.
Die Doppelklick-Datei macht das automatisch.

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
`transport.py` (Bluetooth), `cli.py` (Kommandozeile), `layout.py` (Etiketten-Layout),
`studio.py` + `static/index.html` (Etiketten-Studio).
