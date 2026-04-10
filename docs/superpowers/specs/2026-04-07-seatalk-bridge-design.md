# Seatalk Bridge — Design Spec
**Date:** 2026-04-07
**Status:** Approved

---

## Overview

A local MCP (Model Context Protocol) server that bridges Claude Code to Seatalk, enabling Claude to send messages to Seatalk groups and fetch messages on demand. Designed to support a single group initially, with a multi-group registry architecture that scales toward full workspace-level Seatalk integration.

---

## Goals

- Send messages from Claude Code to a Seatalk group (both manually and as task summaries)
- Fetch messages from Seatalk on demand — only when explicitly asked, never passively
- Support multiple groups via a named group registry
- Lay the groundwork for broader Seatalk workspace integration (all groups, DMs)

## Non-Goals (for this phase)

- Passive monitoring or real-time event streaming from Seatalk
- Sending/receiving direct messages (DMs)
- Full Seatalk workspace management

---

## Architecture

### Components

```
Claude Code
    │
    │ MCP protocol (stdio)
    ▼
seatalk_mcp_server.py          ← local process, registered in ~/.claude/claude_desktop_config.json
    │
    ├── Tool: send_message      → HTTP POST → Seatalk Webhook URL (per group)
    ├── Tool: fetch_messages    → HTTP GET  → Seatalk OpenAPI (bot token)
    └── Tool: list_groups       → reads config, returns available group names
```

### Language & Dependencies

- **Python 3.11+**
- `mcp` — MCP Python SDK
- `httpx` — async HTTP client
- `python-dotenv` — environment variable loading

---

## Project Structure

```
Seatalk Bridge/
├── seatalk_mcp_server.py     ← MCP server entry point, tool definitions
├── seatalk_client.py         ← Seatalk API calls (send, fetch, list groups)
├── config.py                 ← Load and validate config.json
├── config.json               ← Group registry + credentials (gitignored)
├── config.example.json       ← Template committed to repo
├── requirements.txt          ← mcp, httpx, python-dotenv
└── docs/superpowers/specs/   ← Design documents
```

---

## Configuration

`config.json` (gitignored):
```json
{
  "groups": {
    "my-team": {
      "webhook_url": "https://openapi.seatalk.io/webhook/group/<id>"
    }
  },
  "bot_token": "<seatalk-bot-token>",
  "signing_secret": "<seatalk-signing-secret>"
}
```

`config.example.json` (committed):
```json
{
  "groups": {
    "example-group": {
      "webhook_url": "https://openapi.seatalk.io/webhook/group/<your-group-id>"
    }
  },
  "bot_token": "<obtain from Seatalk Developer Portal>",
  "signing_secret": "<obtain from Seatalk Developer Portal>"
}
```

Adding a new group = one new entry in `groups`. No code changes required.

---

## MCP Tools

### `send_message`
Send a text message to a named Seatalk group.

| Parameter | Type | Description |
|-----------|------|-------------|
| `group` | string | Name of the group as defined in config (e.g. `"my-team"`) |
| `message` | string | Text content to send |

**Flow:**
1. Look up `webhook_url` for the given group name
2. POST `{"tag": "text", "text": {"content": "<message>"}}` to the webhook URL
3. Return success confirmation or error detail

---

### `fetch_messages`
Fetch recent messages from a named Seatalk group (on-demand only).

| Parameter | Type | Description |
|-----------|------|-------------|
| `group` | string | Name of the group as defined in config |
| `limit` | int | Number of recent messages to retrieve (default: 10) |

**Flow:**
1. Call Seatalk OpenAPI group history endpoint using `bot_token`
2. Return list of messages: sender name, timestamp, content
3. If `bot_token` is missing, return actionable error message

**Note:** Requires bot token with group message read scope. Token obtained from the Seatalk Developer Portal under the system account.

---

### `list_groups`
List all configured group names.

No parameters. Returns the names of all groups registered in `config.json`.

---

## Data Flow

### Sending
```
Claude → send_message("my-team", "Hello") 
    → config lookup → webhook_url
    → POST {"tag": "text", "text": {"content": "Hello"}}
    → Seatalk delivers to group
    → MCP returns "Message sent successfully"
```

### Fetching
```
Claude → fetch_messages("my-team", limit=10)
    → Seatalk OpenAPI /group/messages with bot_token
    → Returns [{sender, timestamp, content}, ...]
    → Claude displays in conversation
```

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Unknown group name | `"Group 'X' not found. Configured groups: [list]"` |
| Webhook POST fails | Return HTTP status code + Seatalk error body |
| Bot token missing | `"fetch_messages requires bot_token in config.json — see config.example.json"` |
| Bot token expired/invalid | Return Seatalk API error with hint to refresh token |
| Network timeout | Retry once (3s timeout), then fail with clear message |
| Malformed config | Fail at startup with descriptive validation error |

---

## Signing Secret

The signing secret is used to verify HMAC-SHA256 signatures on incoming webhook payloads from Seatalk — confirming payloads are genuinely from Seatalk and not spoofed. 

For this phase, the signing secret is stored in config but not actively used (no inbound listener). It is wired in now so that adding an inbound listener later requires no config changes.

---

## Claude Code Registration

Add to `~/.claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "seatalk": {
      "command": "python",
      "args": ["/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge/seatalk_mcp_server.py"]
    }
  }
}
```

After registration, `send_message`, `fetch_messages`, and `list_groups` appear natively as Claude tools.

---

## Testing Plan

1. **Send test** — call `send_message("my-team", "Hello from Claude")` → confirm message appears in Seatalk group
2. **List groups** — call `list_groups` → confirms config loads and returns group names
3. **Fetch test** — call `fetch_messages("my-team", limit=5)` once bot token is set up → confirms read path works

---

## Future Expansion

The design deliberately separates per-group webhook config from the bot token auth layer. Expanding toward full Seatalk workspace access only requires:
- Adding Seatalk OpenAPI calls for listing all groups/channels dynamically
- Populating the group registry from the API rather than manually
- Adding DM support as new tools (`send_dm`, `fetch_dm`)

No structural changes to the MCP server or config schema are needed.
