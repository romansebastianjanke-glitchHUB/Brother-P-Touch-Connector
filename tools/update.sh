# Wird von den .command-Dateien eingebunden: holt die neueste Version von GitHub.
# Ersetzt src/, tools/, README.md und alle .command-Dateien – Vorlagen und Einstellungen bleiben.
ZIP_URL="https://codeload.github.com/romansebastianjanke-glitchHUB/Brother-P-Touch-Connector/zip/refs/heads/main"
ptouch_update() {
    [ -n "$PTOUCH_UPDATED2" ] && return 0
    echo "Suche nach Aktualisierungen …"
    local TMP NEW f
    TMP="$(mktemp -d)"
    if curl -fsSL --max-time 30 "$ZIP_URL" -o "$TMP/u.zip" 2>/dev/null && unzip -q "$TMP/u.zip" -d "$TMP" 2>/dev/null; then
        NEW="$(find "$TMP" -maxdepth 1 -type d -name 'Brother-P-Touch-Connector-*' | head -1)"
        if [ -n "$NEW" ] && [ -d "$NEW/src" ] && [ -f "$NEW/$SELF" ]; then
            for d in src tools; do
                [ -d "$NEW/$d" ] || continue
                rm -rf "$d.alt"; [ -d "$d" ] && mv "$d" "$d.alt"
                if cp -R "$NEW/$d" "./$d"; then rm -rf "$d.alt"; else rm -rf "$d"; [ -d "$d.alt" ] && mv "$d.alt" "$d"; fi
            done
            cp "$NEW/README.md" ./README.md 2>/dev/null
            for f in "$NEW"/*.command; do
                chmod +x "$f"
                # per mv einsetzen, damit das gerade laufende Skript nicht kaputtgeht
                mv -f "$f" "./$(basename "$f")"
            done
            rm -rf "$TMP"
            echo "Auf dem neuesten Stand."
            PTOUCH_UPDATED2=1 exec /bin/bash "./$SELF"
        fi
    fi
    rm -rf "$TMP"
    echo "(Keine Aktualisierung möglich – mache mit der vorhandenen Version weiter.)"
}
