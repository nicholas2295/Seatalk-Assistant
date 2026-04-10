#!/usr/bin/env bash
# Send a message to a SeaTalk group via webhook.
#
# Usage:
#   seatalk-reply.sh "message"
#   seatalk-reply.sh --format text "plain message"
#   seatalk-reply.sh --stdin < body.md      # preserves newlines (recommended for multi-line)
#   printf '%b' 'a\nb' | seatalk-reply.sh --stdin
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/config.sh"

FORMAT="${SEATALK_REPLY_FORMAT:-text}"
MESSAGE=""
READ_STDIN="false"
# If true, expand C-style escapes in the final message (\n -> newline). Off by default
# to avoid breaking Windows paths; use --expand-escapes when passing literal \n in argv.
EXPAND_ESCAPES="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --format|-f) FORMAT="$2"; shift 2 ;;
        --stdin) READ_STDIN="true"; shift ;;
        --expand-escapes|-e) EXPAND_ESCAPES="true"; shift ;;
        --help|-h)
            cat <<'EOF'
Usage: seatalk-reply.sh [options] "message"

Options:
  --format, -f        text | markdown (default: markdown)
  --stdin             Read message from stdin (keeps real newlines; best for lists)
  --expand-escapes,-e After reading message, run printf '%b' so argv "a\nb" becomes two lines
  --help, -h          Show this help

Line breaks (important):
  Bash double quotes do NOT turn \n into a newline — SeaTalk will show a literal "\n".
  Use one of:
    1) seatalk-reply.sh --stdin <<'EOF'
       Line 1
       Line 2
       EOF
    2) seatalk-reply.sh $'Line 1\n\nLine 2'
    3) seatalk-reply.sh --expand-escapes "**A**\n- B\n- C"

Examples:
  seatalk-reply.sh "Task completed."
  seatalk-reply.sh --format markdown "**Done.** All tests passed."
EOF
            exit 0 ;;
        *) MESSAGE="$1"; shift ;;
    esac
done

if [[ "$READ_STDIN" == "true" ]]; then
    MESSAGE=$(cat)
elif [[ -z "$MESSAGE" ]]; then
    echo "Error: message required (or use --stdin)" >&2
    echo "Usage: seatalk-reply.sh [options] \"message\"" >&2
    exit 1
fi

if [[ "$EXPAND_ESCAPES" == "true" ]]; then
    MESSAGE=$(printf '%b' "$MESSAGE")
fi

if [[ -z "${SEATALK_WEBHOOK_URL:-}" ]]; then
    echo "Error: SEATALK_WEBHOOK_URL not configured" >&2
    echo "Set it in seatalk-listener.conf or as an environment variable." >&2
    exit 1
fi

# Payload shapes: official group webhook documents `tag: text` + text.content (no format field).
# Some tenants support `tag: markdown`; default to text so messages reliably appear in the client.
if command -v jq &>/dev/null; then
    case "$FORMAT" in
        markdown) payload=$(jq -n --arg m "$MESSAGE" '{tag:"markdown",markdown:{content:$m}}') ;;
        text|*)   payload=$(jq -n --arg m "$MESSAGE" '{tag:"text",text:{content:$m}}') ;;
    esac
else
    escaped=$(printf '%s' "$MESSAGE" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')
    case "$FORMAT" in
        markdown) payload="{\"tag\":\"markdown\",\"markdown\":{\"content\":${escaped}}}" ;;
        text|*)   payload="{\"tag\":\"text\",\"text\":{\"content\":${escaped}}}" ;;
    esac
fi

http_code=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    "$SEATALK_WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "$payload" 2>/dev/null)

if [[ "$http_code" == "200" ]]; then
    echo "Sent (${FORMAT})"
else
    echo "Error: webhook returned HTTP ${http_code}" >&2
    exit 1
fi
