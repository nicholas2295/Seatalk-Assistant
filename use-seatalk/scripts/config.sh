#!/usr/bin/env bash
# Shared defaults for the use-seatalk skill.
# Sourced by seatalk-listener.sh.

SEATALK_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEATALK_SKILL_ROOT="$(cd "$SEATALK_SCRIPT_DIR/.." && pwd)"
SEATALK_WORKSPACE_ROOT="$(cd "$SEATALK_SKILL_ROOT/../../.." && pwd)"

# Config loading: exactly ONE file is sourced (not merged).
#   1. $SEATALK_CONFIG_FILE (explicit override)
#   2. ~/.use-seatalk/seatalk-listener.conf (user-level — if present, skill-local file is ignored)
#   3. $SEATALK_SKILL_ROOT/seatalk-listener.conf (skill-local fallback)
if [[ -z "${SEATALK_CONFIG_FILE:-}" ]]; then
    if [[ -f "$HOME/.use-seatalk/seatalk-listener.conf" ]]; then
        SEATALK_CONFIG_FILE="$HOME/.use-seatalk/seatalk-listener.conf"
    elif [[ -f "$SEATALK_SKILL_ROOT/seatalk-listener.conf" ]]; then
        SEATALK_CONFIG_FILE="$SEATALK_SKILL_ROOT/seatalk-listener.conf"
    fi
fi
if [[ -n "${SEATALK_CONFIG_FILE:-}" && -f "$SEATALK_CONFIG_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$SEATALK_CONFIG_FILE"
fi

# ── Core ──────────────────────────────────────────────────────────
SEATALK_WATCH_GROUPS="${SEATALK_WATCH_GROUPS:-}"            # CSV group IDs: "4172747,188215"
SEATALK_AGENT_TARGET="${SEATALK_AGENT_TARGET:-main}"

# ── CDP connection ────────────────────────────────────────────────
SEATALK_CDP_HOST="${SEATALK_CDP_HOST:-127.0.0.1}"
SEATALK_CDP_PORT="${SEATALK_CDP_PORT:-19222}"
SEATALK_CDP_POLL="${SEATALK_CDP_POLL:-2}"                   # seconds between queue drains
export SEATALK_CDP_HOST SEATALK_CDP_PORT SEATALK_CDP_POLL

# Set to "1" so `start` will run seatalk-restart-debug.sh when CDP port is not listening.
SEATALK_AUTO_RESTART_WITH_DEBUG="${SEATALK_AUTO_RESTART_WITH_DEBUG:-}"
export SEATALK_AUTO_RESTART_WITH_DEBUG

# ── Message filtering (whitelist) ─────────────────────────────────
# Only messages from these user IDs are forwarded. CSV of numeric IDs.
# Empty = forward all messages.
SEATALK_ADMIN_IDS="${SEATALK_ADMIN_IDS:-}"
export SEATALK_ADMIN_IDS

# ── tmux session ──────────────────────────────────────────────────
SEATALK_TMUX_SESSION="${SEATALK_TMUX_SESSION:-seatalk-listener}"

# ── Reply (outbound) ──────────────────────────────────────────────
# Webhook URL for sending replies to the SeaTalk group.
# Primary reply method; CDP send is fallback.
SEATALK_WEBHOOK_URL="${SEATALK_WEBHOOK_URL:-}"
# Reply format: "text" (default, matches Open Platform webhook docs) or "markdown" if your tenant supports it
SEATALK_REPLY_FORMAT="${SEATALK_REPLY_FORMAT:-text}"

# ── CDP Send (write access) ──────────────────────────────────────
# Allow cdp-reader.py "send" command to type & send messages via CDP.
# Disabled by default for safety. Set to "true" to enable.
SEATALK_ALLOW_SEND="${SEATALK_ALLOW_SEND:-false}"
export SEATALK_ALLOW_SEND

# ── Reconnect (exponential backoff) ───────────────────────────────
SEATALK_RETRY_INITIAL="${SEATALK_RETRY_INITIAL:-3}"         # first retry delay (seconds)
SEATALK_RETRY_MAX="${SEATALK_RETRY_MAX:-120}"               # max retry delay cap (seconds)
SEATALK_RETRY_MULTIPLIER="${SEATALK_RETRY_MULTIPLIER:-2}"   # backoff multiplier

# ── Disconnect alert ─────────────────────────────────────────────
# "true" = send a webhook message to the group when connection drops / recovers
SEATALK_ALERT_ON_DISCONNECT="${SEATALK_ALERT_ON_DISCONNECT:-true}"

# ── Staleness filter ──────────────────────────────────────────────
SEATALK_MAX_MSG_AGE="${SEATALK_MAX_MSG_AGE:-300}"                 # max message age in seconds (default 5min); older messages are dropped

# ── Watchdog ─────────────────────────────────────────────────────
SEATALK_WATCHDOG_INTERVAL="${SEATALK_WATCHDOG_INTERVAL:-30}"      # seconds between tmux checks

# ── Python-level listen reconnect ────────────────────────────────
# These control the INTERNAL reconnect inside cmd_listen (Python).
# The bash-level retry (SEATALK_RETRY_*) is the outer loop if Python exits entirely.
export SEATALK_LISTEN_HEALTH_INTERVAL="${SEATALK_LISTEN_HEALTH_INTERVAL:-30}"  # polls between health checks
export SEATALK_LISTEN_RETRY_INITIAL="${SEATALK_LISTEN_RETRY_INITIAL:-2}"       # first reconnect delay (s)
export SEATALK_LISTEN_RETRY_MAX="${SEATALK_LISTEN_RETRY_MAX:-120}"             # max reconnect delay cap (s)
export SEATALK_LISTEN_RETRY_MULT="${SEATALK_LISTEN_RETRY_MULT:-2}"             # backoff multiplier

# ── Paths ─────────────────────────────────────────────────────────
SEATALK_LOG_DIR="${SEATALK_LOG_DIR:-$SEATALK_SKILL_ROOT/logs}"
SEATALK_LISTENER_LOG="${SEATALK_LISTENER_LOG:-$SEATALK_LOG_DIR/seatalk-listener.log}"
# agent-manager: spex-ai repo uses skills/agent-manager; work-assistant uses .agent/skills/agent-manager
if [[ -z "${SEATALK_AGENT_MANAGER_SCRIPT:-}" ]]; then
    if [[ -x "$SEATALK_WORKSPACE_ROOT/skills/agent-manager/scripts/agent-manager.sh" ]]; then
        SEATALK_AGENT_MANAGER_SCRIPT="$SEATALK_WORKSPACE_ROOT/skills/agent-manager/scripts/agent-manager.sh"
    else
        SEATALK_AGENT_MANAGER_SCRIPT="$SEATALK_WORKSPACE_ROOT/.agent/skills/agent-manager/scripts/agent-manager.sh"
    fi
fi
# Repo-relative path for safe-reply hint in forwarded messages (footer)
if [[ -z "${SEATALK_SAFE_REPLY_PATH_REL:-}" ]]; then
    if [[ -f "$SEATALK_WORKSPACE_ROOT/skills/use-seatalk/scripts/safe-reply.sh" ]]; then
        SEATALK_SAFE_REPLY_PATH_REL="skills/use-seatalk/scripts/safe-reply.sh"
    else
        SEATALK_SAFE_REPLY_PATH_REL=".agent/skills/use-seatalk/scripts/safe-reply.sh"
    fi
fi
export SEATALK_AGENT_MANAGER_SCRIPT SEATALK_SAFE_REPLY_PATH_REL
