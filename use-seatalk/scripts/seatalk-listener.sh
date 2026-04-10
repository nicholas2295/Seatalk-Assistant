#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/config.sh"

# ── Fixed tmux session name ───────────────────────────────────────
TMUX_SESSION="${SEATALK_TMUX_SESSION:-seatalk-listener}"
TMUX_SOCKET="/tmp/seatalk-tmux-${UID}.socket"
CDP_READER="$SCRIPT_DIR/cdp-reader.py"
STATE_FILE="${SEATALK_LOG_DIR:-.}/seatalk-listener.state"

timestamp() { date '+%Y-%m-%dT%H:%M:%S%z'; }
epoch()     { date +%s; }

log_msg() {
    printf '[%s] [%s] %s\n' "$(timestamp)" "${1:-INFO}" "${2:-}"
}

ensure_dirs() {
    mkdir -p "$SEATALK_LOG_DIR"
}

is_running() {
    tmux -L "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null
}

is_port_listening() {
    local port="${1:?port required}"
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && return 0
    fi
    if command -v nc >/dev/null 2>&1; then
        nc -z 127.0.0.1 "$port" >/dev/null 2>&1 && return 0
    fi
    return 1
}

write_state() {
    local key="${1:?}" val="${2:?}"
    ensure_dirs
    local tmp="$STATE_FILE.tmp"
    if [[ -f "$STATE_FILE" ]]; then
        grep -v "^${key}=" "$STATE_FILE" > "$tmp" 2>/dev/null || true
    else
        : > "$tmp"
    fi
    echo "${key}=${val}" >> "$tmp"
    mv "$tmp" "$STATE_FILE"
}

read_state() {
    local key="${1:?}" default="${2:-}"
    if [[ -f "$STATE_FILE" ]]; then
        local val
        val=$(grep "^${key}=" "$STATE_FILE" 2>/dev/null | tail -1 | cut -d= -f2-)
        echo "${val:-$default}"
    else
        echo "$default"
    fi
}

format_message() {
    local json_line="${1:?json line required}"
    python3 - "$json_line" <<'PY'
import json, sys, os
raw = sys.argv[1].strip()
if not raw:
    raise SystemExit(1)
obj = json.loads(raw)
group   = obj.get("groupName") or f"group-{obj.get('sessionId', '?')}"
gid     = obj.get("sessionId", "?")
sender  = obj.get("senderName", "unknown")
sid     = obj.get("senderId", "?")
ts      = obj.get("timestamp", 0)
mid     = obj.get("mid", "?")
tag     = obj.get("tag", "text")
text    = (obj.get("text") or "").strip()

image_path = obj.get("imagePath", "")
image_info = obj.get("imageInfo")

if tag == "image":
    if image_path:
        text = f"[image message — saved to {image_path}]"
        if image_info:
            text += f"\n  dimensions: {image_info.get('width', '?')}x{image_info.get('height', '?')}, size: {image_info.get('size', '?')} bytes"
    elif image_info:
        text = f"[image message — download failed]"
        text += f"\n  fileId: {image_info.get('fileId', '?')}"
        text += f"\n  dimensions: {image_info.get('width', '?')}x{image_info.get('height', '?')}, size: {image_info.get('size', '?')} bytes"
    else:
        text = "[image message]"
elif not text:
    if tag != "text":
        text = f"[{tag} message]"
    else:
        text = "[empty]"

lines = [
    f"[SeaTalk Incoming Message]",
    f"- group: {group} ({gid})",
    f"- sender: {sender} ({sid})",
    f"- time: {ts}",
    f"- message_id: {mid}",
    f"- content:",
    text,
]
if image_path:
    lines.append(f"- image_path: {image_path}")
lines.append("")
_rel = os.environ.get("SEATALK_SAFE_REPLY_PATH_REL", "scripts/safe-reply.sh")
lines.append("> To reply, use the `use-seatalk` skill (safe-reply.sh — dedup + safe stdin to webhook): "
             f"bash {_rel} 'your message'")
lines.append("> Tip: single-quote the message if it contains backticks or dollar signs; multi-line → seatalk-reply.sh --stdin (see SKILL.md).")
print("\n".join(lines))
PY
}

# ── Alert helper ──────────────────────────────────────────────────
send_alert() {
    local msg="${1:?message required}"
    if [[ "${SEATALK_ALERT_ON_DISCONNECT:-true}" != "true" ]]; then
        return 0
    fi
    if [[ -z "${SEATALK_WEBHOOK_URL:-}" ]]; then
        return 0
    fi
    bash "$SCRIPT_DIR/seatalk-reply.sh" --format text "$msg" >/dev/null 2>&1 || true
}

# ── Internal: the loop that runs inside tmux ──────────────────────
run_loop() {
    ensure_dirs
    local -a listen_cmd=(python3 "$CDP_READER" listen)
    if [[ -n "${SEATALK_WATCH_GROUPS:-}" ]]; then
        listen_cmd+=(--group "$SEATALK_WATCH_GROUPS")
    fi

    local retry_delay="${SEATALK_RETRY_INITIAL:-3}"
    local retry_max="${SEATALK_RETRY_MAX:-120}"
    local retry_mult="${SEATALK_RETRY_MULTIPLIER:-2}"
    local consecutive_failures=0

    # Disable errexit/pipefail for the entire loop to prevent accidental exits
    set +e +o pipefail
    trap 'log_msg "ERROR" "run_loop caught ERR at line $LINENO"' ERR

    while true; do
        log_msg "INFO" "Starting CDP listener (groups=${SEATALK_WATCH_GROUPS:-all}, target=${SEATALK_AGENT_TARGET})"
        log_msg "INFO" "CDP reader: ${listen_cmd[*]}"

        local started_at last_ts
        started_at=$(epoch)
        last_ts=$(read_state "last_forwarded_ts" "")
        if [[ -n "$last_ts" ]]; then
            export SEATALK_LAST_FORWARDED_TS="$last_ts"
        else
            unset -v SEATALK_LAST_FORWARDED_TS 2>/dev/null || true
        fi

        write_state "last_start" "$started_at"
        write_state "reconnect_count" "$consecutive_failures"

        "${listen_cmd[@]}" 2>&1 | while IFS= read -r line; do
            # Skip stderr comment lines from cdp-reader.py
            [[ "$line" == \#* ]] && { echo "$line"; continue; }
            [[ -z "$line" ]] && continue
            # Skip Python error/traceback lines that leak to stdout
            [[ "$line" == ERROR:* ]] && { log_msg "WARN" "CDP stderr leak: ${line:0:200}"; continue; }
            [[ "$line" == Traceback* ]] && { log_msg "WARN" "CDP traceback: ${line:0:200}"; continue; }

            write_state "last_msg" "$(epoch)"

            # ── Staleness filter: skip messages older than MAX_AGE_SEC (timestamps normalized to Unix seconds) ──
            local msg_ts now_ts msg_age
            local MAX_AGE_SEC="${SEATALK_MAX_MSG_AGE:-300}"  # default 5 min
            msg_ts=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); t=int(d.get('timestamp') or 0); print(t//1000 if t>10**12 else t)" 2>/dev/null || echo "0")
            now_ts=$(epoch)
            if [[ "$msg_ts" -gt 0 ]] && (( now_ts - msg_ts > MAX_AGE_SEC )); then
                msg_age=$(( now_ts - msg_ts ))
                log_msg "WARN" "Skipped stale message (age=${msg_age}s > ${MAX_AGE_SEC}s, ts=${msg_ts}): ${line:0:120}"
                continue
            fi

            local formatted
            if ! formatted="$(format_message "$line" 2>/dev/null)"; then
                log_msg "WARN" "Skipped message (format error): ${line:0:120}"
                continue
            fi

            echo "──────────────────────────────────────"
            echo "$formatted"

            if bash "$SEATALK_AGENT_MANAGER_SCRIPT" send "$SEATALK_AGENT_TARGET" "$formatted" >/dev/null 2>&1; then
                log_msg "INFO" "Forwarded → ${SEATALK_AGENT_TARGET}"
                ts=$(echo "$line" | python3 -c "import json,sys; d=json.load(sys.stdin); t=d.get('timestamp') or 0; print(int(t)) if t and t > 0 else ''" 2>/dev/null)
                [[ -n "$ts" ]] && write_state "last_forwarded_ts" "$ts"
            else
                log_msg "ERROR" "Forward failed → ${SEATALK_AGENT_TARGET}"
            fi
        done
        local pipe_rc=${PIPESTATUS[0]:-1}

        local ended_at elapsed
        ended_at=$(epoch)
        elapsed=$(( ended_at - started_at ))

        if (( elapsed > 60 )); then
            retry_delay="${SEATALK_RETRY_INITIAL:-3}"
            consecutive_failures=0
        else
            consecutive_failures=$(( consecutive_failures + 1 ))
        fi

        log_msg "WARN" "CDP listener exited (rc=${pipe_rc}) after ${elapsed}s (failures=${consecutive_failures})"
        write_state "reconnect_count" "$consecutive_failures"

        if (( consecutive_failures == 1 )); then
            send_alert "[seatalk-listener] CDP connection lost (ran ${elapsed}s). Reconnecting..."
        fi

        local jitter=$(( RANDOM % (retry_delay / 2 + 1) ))
        local wait=$(( retry_delay + jitter ))
        log_msg "INFO" "Backoff: sleeping ${wait}s before retry..."
        sleep "$wait"

        if (( retry_delay * retry_mult <= retry_max )); then
            retry_delay=$(( retry_delay * retry_mult ))
        else
            retry_delay="$retry_max"
        fi

        log_msg "INFO" "Reconnecting... (next backoff will be ${retry_delay}s)"
    done
}

# ── Commands ──────────────────────────────────────────────────────
restart_seatalk_with_debug() {
    log_msg "INFO" "Relaunching SeaTalk with remote debugging (seatalk-restart-debug.sh)..."
    bash "$SCRIPT_DIR/seatalk-restart-debug.sh" || log_msg "WARN" "seatalk-restart-debug.sh exited non-zero; continuing"
}

start_listener() {
    if is_running; then
        echo "seatalk-listener is already running (tmux session: $TMUX_SESSION)"
        echo "  attach:  bash $0 attach"
        return 0
    fi

    local port="${SEATALK_CDP_PORT:-19222}"
    if ! is_port_listening "$port"; then
        if [[ "${SEATALK_AUTO_RESTART_WITH_DEBUG:-}" == "1" ]]; then
            restart_seatalk_with_debug
        fi
        if ! is_port_listening "$port"; then
            log_msg "WARN" "CDP port ${port} is not listening. SeaTalk may not be launched with remote debugging enabled."
            echo "WARN: CDP port ${port} is not listening."
            echo "      Run:  bash $SCRIPT_DIR/seatalk-restart-debug.sh"
            echo "   or:  bash $0 restart-with-debug"
            echo "      Manual:"
            echo "        pkill -f SeaTalk.app; sleep 2"
            echo "        open -a SeaTalk --args --remote-debugging-port=${port} --remote-allow-origins=*"
        fi
    fi

    ensure_dirs
    touch "$SEATALK_LISTENER_LOG" && chmod 600 "$SEATALK_LISTENER_LOG"
    write_state "started_at" "$(epoch)"
    write_state "reconnect_count" "0"
    tmux -L "$TMUX_SOCKET" new-session -d -s "$TMUX_SESSION" \
        "bash '${BASH_SOURCE[0]}' _run 2>&1 | tee -a '$SEATALK_LISTENER_LOG'"
    chmod 700 "$TMUX_SOCKET" 2>/dev/null || true

    sleep 1
    if is_running; then
        echo "Started seatalk-listener (tmux session: $TMUX_SESSION)"
        echo "  attach:  bash $0 attach"
        echo "  log:     $SEATALK_LISTENER_LOG"
    else
        echo "ERROR: tmux session failed to start" >&2
        return 1
    fi
}

restart_listener_with_debug() {
    restart_seatalk_with_debug
    stop_listener || true
    sleep 1
    start_listener
}

stop_listener() {
    if ! is_running; then
        echo "seatalk-listener is not running"
        return 0
    fi
    tmux -L "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION"
    echo "Stopped seatalk-listener (killed tmux session: $TMUX_SESSION)"
}

attach_listener() {
    if ! is_running; then
        echo "seatalk-listener is not running" >&2
        return 1
    fi
    tmux -L "$TMUX_SOCKET" attach-session -t "$TMUX_SESSION"
}

status_listener() {
    ensure_dirs
    if is_running; then
        echo "seatalk-listener: RUNNING (tmux session: $TMUX_SESSION)"
    else
        echo "seatalk-listener: STOPPED"
    fi
    local port="${SEATALK_CDP_PORT:-19222}"
    echo "  cdp_port:       ${port}"
    if is_port_listening "$port"; then
        echo "  cdp_listening:  yes"
    else
        echo "  cdp_listening:  no  (messages will not forward until CDP is up)"
        echo "  fix:            bash \"$SCRIPT_DIR/seatalk-listener.sh\" restart-with-debug"
    fi
    echo "  watch_groups:   ${SEATALK_WATCH_GROUPS:-<all>}"
    echo "  target_agent:   $SEATALK_AGENT_TARGET"
    echo "  admin_ids:      ${SEATALK_ADMIN_IDS:-<all>}"
    echo "  poll_interval:  ${SEATALK_CDP_POLL:-2}s"
    echo "  retry:          ${SEATALK_RETRY_INITIAL:-3}s initial, x${SEATALK_RETRY_MULTIPLIER:-2}, max ${SEATALK_RETRY_MAX:-120}s"
    echo "  alert_on_dc:    ${SEATALK_ALERT_ON_DISCONNECT:-true}"
    echo "  log:            $SEATALK_LISTENER_LOG"

    local started rc last_msg
    started=$(read_state "started_at" "")
    rc=$(read_state "reconnect_count" "0")
    last_msg=$(read_state "last_msg" "")

    if [[ -n "$started" ]]; then
        local now
        now=$(epoch)
        local uptime=$(( now - started ))
        local h=$(( uptime / 3600 )) m=$(( (uptime % 3600) / 60 ))
        echo "  uptime:         ${h}h ${m}m"
    fi
    echo "  reconnects:     $rc"
    if [[ -n "$last_msg" ]]; then
        local ago=$(( $(epoch) - last_msg ))
        echo "  last_message:   ${ago}s ago"
    else
        echo "  last_message:   (none)"
    fi
}

ensure_listener() {
    if is_running; then
        return 0
    fi
    log_msg "INFO" "ensure: listener not running, starting..."
    start_listener
}

watchdog_listener() {
    local interval="${SEATALK_WATCHDOG_INTERVAL:-30}"
    log_msg "INFO" "Watchdog started (check every ${interval}s)"
    while true; do
        if ! is_running; then
            log_msg "WARN" "Watchdog: tmux session dead, restarting..."
            send_alert "[seatalk-listener] Watchdog: session died, restarting..."
            start_listener || log_msg "ERROR" "Watchdog: restart failed"
        fi
        sleep "$interval"
    done
}

# ── Reply: send a message back to the group (webhook only) ───────
reply_message() {
    bash "$SCRIPT_DIR/seatalk-reply.sh" "$@"
}

usage() {
    cat <<'EOF'
Usage: seatalk-listener.sh <command>

Commands:
  start            Start listener in a tmux session
  stop             Stop listener (kill tmux session)
  status           Show running status, config, and reconnect stats
  attach           Attach to the tmux session
  restart          Stop + start
  restart-with-debug
                   Quit SeaTalk, relaunch with --remote-debugging-port, then stop + start listener
  ensure           Start only if not already running (idempotent, cron-safe)
  watchdog         Run foreground loop: restart tmux session if it dies
  once             Read current messages once (no loop)
  reply "message"  Send a reply to the watched group (webhook)
EOF
}

main() {
    local cmd="${1:-}"
    case "$cmd" in
        start)    start_listener ;;
        stop)     stop_listener ;;
        status)   status_listener ;;
        attach)   attach_listener ;;
        restart)  stop_listener; sleep 1; start_listener ;;
        restart-with-debug) restart_listener_with_debug ;;
        ensure)   ensure_listener ;;
        watchdog) watchdog_listener ;;
        once)
            local gid="${SEATALK_WATCH_GROUPS:-}"
            local -a args=(python3 "$CDP_READER" read)
            [[ -n "$gid" ]] && args+=(--group "$gid")
            "${args[@]}"
            ;;
        reply)
            shift
            reply_message "$*"
            ;;
        _run)    run_loop ;;
        *)       usage; exit 1 ;;
    esac
}

main "$@"
