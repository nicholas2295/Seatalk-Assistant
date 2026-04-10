#!/usr/bin/env bash
# Manage the SeaTalk debug-mode LaunchAgent.
#
# Usage:
#   seatalk-debug-autostart.sh install   — install + load the LaunchAgent
#   seatalk-debug-autostart.sh uninstall — unload + remove the LaunchAgent
#   seatalk-debug-autostart.sh status    — show whether installed and running
#
# The LaunchAgent ensures SeaTalk Desktop always runs with
# --remote-debugging-port (CDP), which use-seatalk depends on.
# It checks every 30 seconds and relaunches SeaTalk with debug flags
# if it was started without them (e.g., Dock click, macOS app restore).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_SRC="${SCRIPT_DIR}/com.seatalk.debug.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/com.seatalk.debug.plist"
LABEL="com.seatalk.debug"

cmd_install() {
    mkdir -p "$(dirname "$PLIST_DST")"

    if launchctl list "$LABEL" &>/dev/null; then
        echo "[auto-debug] Unloading existing agent..."
        launchctl unload "$PLIST_DST" 2>/dev/null || true
    fi

    sed "s|__SKILL_DIR__|${SKILL_DIR}|g" "$PLIST_SRC" > "$PLIST_DST"
    launchctl load "$PLIST_DST"

    echo "[auto-debug] Installed and loaded: $PLIST_DST"
    echo "[auto-debug] Skill dir baked in: $SKILL_DIR"
    echo "[auto-debug] SeaTalk will be (re)launched with debug port within 30 seconds."
    echo "[auto-debug] Log: /tmp/seatalk-debug.log"

    sleep 3
    cmd_status
}

cmd_uninstall() {
    if launchctl list "$LABEL" &>/dev/null; then
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        echo "[auto-debug] Agent unloaded."
    else
        echo "[auto-debug] Agent was not loaded."
    fi

    if [[ -f "$PLIST_DST" ]]; then
        rm "$PLIST_DST"
        echo "[auto-debug] Removed: $PLIST_DST"
    else
        echo "[auto-debug] Plist not found at $PLIST_DST"
    fi
}

cmd_status() {
    local port="${SEATALK_CDP_PORT:-19222}"
    echo "=== SeaTalk Debug Autostart Status ==="

    if [[ -f "$PLIST_DST" ]]; then
        echo "  plist:     installed ($PLIST_DST)"
    else
        echo "  plist:     NOT installed"
    fi

    if launchctl list "$LABEL" &>/dev/null; then
        echo "  agent:     loaded"
    else
        echo "  agent:     NOT loaded"
    fi

    if pgrep -f "remote-debugging-port" &>/dev/null; then
        echo "  seatalk:   running WITH debug port"
    elif pgrep -f "SeaTalk.app/Contents/MacOS/SeaTalk" &>/dev/null; then
        echo "  seatalk:   running WITHOUT debug port (will be fixed within 30s)"
    else
        echo "  seatalk:   not running (will be launched within 30s)"
    fi

    if curl -sS --connect-timeout 2 "http://127.0.0.1:${port}/json/version" &>/dev/null; then
        echo "  cdp:       reachable (port $port)"
    else
        echo "  cdp:       NOT reachable (port $port)"
    fi
}

case "${1:-status}" in
    install)   cmd_install ;;
    uninstall) cmd_uninstall ;;
    status)    cmd_status ;;
    *)
        echo "Usage: $0 {install|uninstall|status}"
        exit 1
        ;;
esac
