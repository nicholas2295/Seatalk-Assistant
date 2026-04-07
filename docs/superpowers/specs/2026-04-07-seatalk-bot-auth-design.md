# Seatalk Bot Auth — Design Spec
**Date:** 2026-04-07
**Status:** Approved

---

## Overview

Migrate the Seatalk Bridge from System Account (webhook-based, send-only) to Bot API (OAuth, bidirectional). Replace static `bot_token` and per-group `webhook_url` with App ID + Secret OAuth flow, enabling richer interactions in future (receive messages, get group info, chat history).

---

## Goals

- Authenticate via App ID + Secret (OAuth access token)
- Send messages via Bot "Send Message to Group Chat" API
- Fetch chat history via Bot "Get Chat History" API
- Fetch live group info (name, members) via Bot "Get Group Info" API
- Remove all webhook URL and static bot token references

## Non-Goals

- Receiving messages / event callbacks (future)
- DM support (future)
- Auto-discovering groups from API (future)

---

## Architecture

```
seatalk_mcp_server.py
    │
    ├── send_message(group, message)
    ├── fetch_messages(group, limit)
    ├── list_groups()
    └── get_group_info(group)         ← new
         │
         ▼
seatalk_client.py                     ← updated: all calls use bot API
         │
         ▼
auth.py                               ← new: token fetch, cache, refresh
         │
         ▼
Seatalk OpenAPI (app_id + app_secret → access_token)
```

---

## Config Changes

`config.json` — remove `webhook_url` and `bot_token`, add `app_id`, `app_secret`, `group_id` per group:

```json
{
  "app_id": "<from Seatalk Developer Portal>",
  "app_secret": "<from Seatalk Developer Portal>",
  "signing_secret": "<from Seatalk Developer Portal>",
  "groups": {
    "example-group": {
      "name": "JNT Test Group",
      "group_id": "<seatalk-group-id>"
    }
  }
}
```

`config.example.json` updated to match.

### Config model changes (`config.py`)

- `GroupConfig`: remove `webhook_url`, add `group_id: str`
- `Config`: remove `bot_token`, add `app_id: str`, `app_secret: str`
- Validation: `app_id` and `app_secret` required; `group_id` required per group

---

## New: `auth.py`

Responsibilities:
- Call Seatalk "Get App Access Token" API with `app_id` + `app_secret`
- Cache the token in memory with its expiry time
- Auto-refresh when expired (or within 60s of expiry)
- Raise a clear error if token fetch fails

```
get_token(config) → str
    if cached token valid: return it
    else: POST /auth/app_access_token → cache + return new token
```

Token endpoint (from docs): requires `app_id` + `app_secret`, returns `access_token` + `expire_in`.

---

## Updated: `seatalk_client.py`

All functions call `auth.get_token(config)` to obtain a bearer token before each request.

### `send_message(config, group, message)`
- Look up `group_id` for group key
- POST to Bot "Send Message to Group Chat" API
- Payload: `{"tag": "text", "text": {"content": message}}`
- Auth: `Authorization: Bearer <token>`

### `fetch_messages(config, group, limit)`
- Look up `group_id`
- GET Bot "Get Chat History" API with `group_id` + `limit`
- Format and return messages as before

### `list_groups(config)`
- Unchanged in behaviour: returns `key (name)` for each group
- No API call needed

### `get_group_info(config, group)` ← new
- Look up `group_id`
- GET Bot "Get Group Info" API
- Return: group name, member count, any other available fields

---

## New MCP Tool: `get_group_info`

```python
@mcp.tool()
async def get_group_info(group: str) -> str:
    """Fetch live info for a named Seatalk group (name, member count, etc.)."""
    return await seatalk_client.get_group_info(config, group)
```

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| Token fetch fails | Clear error with status code + message |
| Token expired mid-session | Auto-refresh on next call |
| Unknown group key | `"Group 'X' not found. Configured groups: [list]"` |
| API call fails | Return HTTP status + Seatalk error body |
| `app_id`/`app_secret` missing | Fail at startup with descriptive error |
| `group_id` missing | Fail at config load with descriptive error |

---

## File Changes Summary

| File | Change |
|------|--------|
| `auth.py` | New — token fetch + cache |
| `config.py` | Remove `webhook_url`/`bot_token`, add `app_id`/`app_secret`/`group_id` |
| `seatalk_client.py` | Replace webhook calls with bot API calls; add `get_group_info` |
| `seatalk_mcp_server.py` | Add `get_group_info` tool |
| `config.json` | Update credentials + group entries |
| `config.example.json` | Update to reflect new schema |
| `tests/test_config.py` | Update for new fields |
| `tests/test_seatalk_client.py` | Update for bot API + new function |

---

## Testing Plan

1. Token fetch — mock Seatalk auth endpoint, verify token cached and reused
2. Token refresh — simulate expiry, verify auto-refresh
3. `send_message` — mock bot API, verify correct payload + auth header
4. `fetch_messages` — mock chat history API, verify formatting
5. `get_group_info` — mock group info API, verify fields returned
6. Config validation — missing `app_id`, missing `group_id` → error at load time
