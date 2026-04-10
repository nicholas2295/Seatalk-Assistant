#!/usr/bin/env bash
# Restart SeaTalk (macOS) with Chrome DevTools Protocol enabled for cdp-reader.py.
# Port defaults to SEATALK_CDP_PORT or 19222.
#
# Electron 30+ (Chromium 134+) ignores --remote-debugging-port as a CLI arg.
# We patch app.asar to inject app.commandLine.appendSwitch() before any app code.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.sh
source "${SCRIPT_DIR}/config.sh"

PORT="${SEATALK_CDP_PORT:-19222}"
APP_ASAR="/Applications/SeaTalk.app/Contents/Resources/app.asar"
APP_ASAR_BAK="${APP_ASAR}.bak"
PATCH_MARKER="remote-debugging-port"

log() { printf '[seatalk-restart-debug] %s\n' "$*"; }

# ── Patch app.asar to enable CDP (Electron 30+) ─────────────────
patch_app_asar() {
    if ! command -v npx >/dev/null 2>&1; then
        log "WARN: npx not found, skipping app.asar patch (CDP may not work on Electron 30+)"
        return 1
    fi
    if [[ ! -f "$APP_ASAR" ]]; then
        log "WARN: $APP_ASAR not found"
        return 1
    fi

    local tmpdir
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' RETURN

    log "Extracting app.asar..."
    npx --yes asar extract "$APP_ASAR" "$tmpdir/app" 2>/dev/null

    local index_js="$tmpdir/app/src/index.js"
    if [[ ! -f "$index_js" ]]; then
        log "WARN: src/index.js not found in app.asar"
        return 1
    fi

    if grep -q "$PATCH_MARKER" "$index_js"; then
        log "app.asar already patched for CDP port ${PORT}"
        return 0
    fi

    [[ ! -f "$APP_ASAR_BAK" ]] && cp "$APP_ASAR" "$APP_ASAR_BAK"

    local inject
    inject=$(cat <<'INJECT'

// [CDP Patch] Electron 30+ requires app.commandLine, not CLI args
const __CDP_PORT = process.env.SEATALK_CDP_PORT || '__PORT__';
require('electron').app.commandLine.appendSwitch('remote-debugging-port', __CDP_PORT);
require('electron').app.commandLine.appendSwitch('remote-allow-origins', 'http://127.0.0.1:' + __CDP_PORT);
INJECT
)
    inject="${inject//__PORT__/$PORT}"

    # Insert after the first non-comment, non-empty line (the squirrel-startup guard)
    local marker="if (require('electron-squirrel-startup')) return;"
    if grep -qF "$marker" "$index_js"; then
        sed -i.tmp "/$marker/a\\
${inject//$/\\$}
" "$index_js" 2>/dev/null || {
            # macOS sed quoting is painful; use python as fallback
            python3 -c "
import pathlib, sys
p = pathlib.Path('$index_js')
src = p.read_text()
marker = '''$marker'''
inject = '''$inject'''
if marker in src:
    src = src.replace(marker, marker + inject, 1)
    p.write_text(src)
    print('Patched via python')
else:
    print('Marker not found', file=sys.stderr)
    sys.exit(1)
"
        }
    else
        log "WARN: squirrel-startup guard not found, prepending patch"
        { echo "$inject"; cat "$index_js"; } > "$index_js.new"
        mv "$index_js.new" "$index_js"
    fi

    rm -f "$index_js.tmp"

    log "Repacking app.asar with CDP patch (port ${PORT})..."
    npx --yes asar pack "$tmpdir/app" "$APP_ASAR" 2>/dev/null
    log "app.asar patched successfully"
}

# ── Main ─────────────────────────────────────────────────────────
log "Ensuring app.asar has CDP patch..."
patch_app_asar || log "WARN: patch failed, falling back to CLI args (may not work on Electron 30+)"

log "Quitting SeaTalk..."
pkill -f SeaTalk 2>/dev/null || killall SeaTalk 2>/dev/null || true
sleep 2

log "Launching SeaTalk..."
open -a SeaTalk --args --remote-debugging-port="${PORT}" --remote-allow-origins="http://127.0.0.1:${PORT}"

log "Waiting for CDP on port ${PORT}..."
for i in $(seq 1 30); do
    if curl -sS --connect-timeout 2 "http://127.0.0.1:${PORT}/json" 2>/dev/null | head -c 80 | grep -q .; then
        log "OK: http://127.0.0.1:${PORT}/json responds"
        curl -sS "http://127.0.0.1:${PORT}/json" 2>/dev/null | head -c 300
        echo ""
        exit 0
    fi
    sleep 1
done

log "WARN: CDP not ready after ~30s. Is SeaTalk installed as /Applications/SeaTalk.app ?" >&2
exit 1
