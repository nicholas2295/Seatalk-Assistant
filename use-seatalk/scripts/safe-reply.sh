#!/usr/bin/env bash
# Safe SeaTalk reply wrapper with error handling and deduplication
# Prevents duplicate messages due to shell escaping issues

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/config.sh"

# Deduplication state file
DEDUP_STATE_FILE="${SEATALK_LOG_DIR:-$SCRIPT_DIR/../logs}/reply-dedup.state"
DEDUP_WINDOW_SEC=30  # Don't send similar messages within 30 seconds

# Create log directory if it doesn't exist
mkdir -p "$(dirname "$DEDUP_STATE_FILE")"

# Function to compute message hash for deduplication
compute_message_hash() {
    local message="$1"
    # Normalize message: remove extra whitespace, convert to lowercase
    normalized=$(echo "$message" | tr -s ' \t\n' ' ' | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    echo -n "$normalized" | shasum -a 256 | cut -d' ' -f1
}

# Function to check if message is duplicate
is_duplicate_message() {
    local message="$1"
    local current_time=$(date +%s)
    local message_hash=$(compute_message_hash "$message")
    
    # Create state file if it doesn't exist
    touch "$DEDUP_STATE_FILE"
    chmod 600 "$DEDUP_STATE_FILE"

    # Atomically read, prune, check, and append using flock
    local is_dup=1
    {
        flock -x 9

        # Clean old entries (older than dedup window)
        local temp_file
        temp_file=$(mktemp)
        while IFS='|' read -r timestamp hash; do
            if [[ -n "$timestamp" && -n "$hash" ]] && [[ $((current_time - timestamp)) -lt $DEDUP_WINDOW_SEC ]]; then
                echo "${timestamp}|${hash}" >> "$temp_file"
            fi
        done < "$DEDUP_STATE_FILE"
        mv "$temp_file" "$DEDUP_STATE_FILE"

        # Check if current message hash exists in recent entries
        if grep -q "|${message_hash}$" "$DEDUP_STATE_FILE" 2>/dev/null; then
            is_dup=0  # Is duplicate
        else
            # Add current message to state
            echo "${current_time}|${message_hash}" >> "$DEDUP_STATE_FILE"
            is_dup=1  # Not duplicate
        fi
    } 9>>"$DEDUP_STATE_FILE.lock"
    return $is_dup
}

# Function to safely send message using stdin method
safe_send_message() {
    local message="$1"
    local format="${2:-${SEATALK_REPLY_FORMAT:-markdown}}"
    
    # Use stdin method to avoid shell escaping issues
    echo "$message" | bash "$SCRIPT_DIR/seatalk-reply.sh" --stdin --format "$format"
}

# Function to log reply attempts
log_reply_attempt() {
    local status="$1"
    local message="$2"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local log_file="${SEATALK_LOG_DIR:-$SCRIPT_DIR/../logs}/safe-reply.log"
    
    mkdir -p "$(dirname "$log_file")"
    echo "[$timestamp] [$status] ${message:0:100}..." >> "$log_file"
}

# Main function
main() {
    local message=""
    local format="${SEATALK_REPLY_FORMAT:-markdown}"
    local force_send="false"
    local dry_run="false"
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --format|-f) format="$2"; shift 2 ;;
            --force) force_send="true"; shift ;;
            --dry-run) dry_run="true"; shift ;;
            --help|-h)
                cat <<'EOF'
Usage: safe-reply.sh [options] "message"

A safer wrapper around seatalk-reply.sh with deduplication and error handling.

Options:
  --format, -f    text | markdown (default: markdown)
  --force         Skip deduplication check
  --dry-run       Check for duplicates but don't send
  --help, -h      Show this help

Features:
  - Prevents duplicate messages within 30 seconds
  - Uses stdin method to avoid shell escaping issues
  - Logs all reply attempts
  - Safe handling of special characters

Examples:
  safe-reply.sh "Task completed successfully"
  safe-reply.sh --format text "Plain text message"
  safe-reply.sh --force "Send even if duplicate"
  safe-reply.sh --dry-run "Check if this would be sent"
EOF
                exit 0 ;;
            *) message="$1"; shift ;;
        esac
    done
    
    if [[ -z "$message" ]]; then
        echo "Error: message required" >&2
        echo "Usage: safe-reply.sh [options] \"message\"" >&2
        exit 1
    fi
    
    # Check for duplicates unless forced
    if [[ "$force_send" != "true" ]] && is_duplicate_message "$message"; then
        echo "Skipped: duplicate message within ${DEDUP_WINDOW_SEC}s window" >&2
        log_reply_attempt "DUPLICATE_SKIPPED" "$message"
        exit 0
    fi
    
    # Dry run mode
    if [[ "$dry_run" == "true" ]]; then
        echo "Would send: ${message:0:100}..." >&2
        echo "Format: $format" >&2
        exit 0
    fi
    
    # Send the message
    if safe_send_message "$message" "$format"; then
        log_reply_attempt "SUCCESS" "$message"
        echo "Sent safely ($format)"
    else
        log_reply_attempt "FAILED" "$message"
        echo "Error: failed to send message" >&2
        exit 1
    fi
}

# Run main function with all arguments
main "$@"