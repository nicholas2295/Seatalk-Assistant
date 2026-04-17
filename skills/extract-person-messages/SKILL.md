---
name: extract-person-messages
description: Use when the user wants to extract, download, or export all SeaTalk messages involving a specific person — 1:1 chats and group messages — over a time window. Outputs JSON and human-readable transcript files.
---

# Extract Person Messages

Extracts all SeaTalk messages involving a target person from the local SeaTalk Desktop cache via CDP.

## What It Collects

- Full 1:1 private chat with the target person
- All messages in groups where the target is a member (full conversation context, not just their messages)
- Thread replies in those groups (from Redux + mdb local database)

## Prerequisites

1. **use-seatalk skill must be installed** — this skill depends on `use-seatalk/scripts/cdp-reader.py` for CDP connectivity.
2. **SeaTalk Desktop must be running with CDP enabled** — see the `use-seatalk` skill for setup (`seatalk-restart-debug.sh`).
3. **Python dependency:** `pip3 install websocket-client`

## Finding a Target Person's SeaTalk ID

Run this via cdp-reader to look up a person by name:

```bash
S="<repo-root>/use-seatalk/scripts"
python3 "$S/cdp-reader.py" eval "
(function() {
  var ui = window.store.getState().contact.userInfo || {};
  var results = [];
  for (var id in ui) {
    var u = ui[id];
    var name = u.name || u.nickname || '';
    if (name.toLowerCase().indexOf('SEARCH_TERM') !== -1) {
      results.push({id: id, name: name});
    }
  }
  return JSON.stringify(results);
})()
"
```

Replace `SEARCH_TERM` with a lowercase substring of the person's name. The returned `id` is the `--target-id` value.

## Usage

The script is at `skills/extract-person-messages/extract_person_messages.py` (relative to the repo root).

### Quick extraction (cache only, fastest)

```bash
python3 skills/extract-person-messages/extract_person_messages.py \
  --target-id <ID> --last-days 30 --skip-preload
```

### Full extraction with history pre-loading (slower but more complete)

```bash
python3 skills/extract-person-messages/extract_person_messages.py \
  --target-id <ID> --last-days 30 --scroll-steps 120
```

### Date range extraction

```bash
python3 skills/extract-person-messages/extract_person_messages.py \
  --target-id <ID> --since-date 2026-03-01 --until-date 2026-03-31 --skip-preload
```

### All flags

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--target-id` | Yes | — | SeaTalk numeric user ID of the target person |
| `--last-days N` | One of | — | Extract last N days (mutually exclusive with other time flags) |
| `--last-hours N` | One of | — | Extract last N hours |
| `--since-date` | One of | — | Start date `YYYY-MM-DD` (requires `--until-date`) |
| `--until-date` | — | — | End date `YYYY-MM-DD` (inclusive) |
| `--since-unix` | One of | — | Start timestamp (Unix seconds) |
| `--until-unix` | — | now | End timestamp (Unix seconds) |
| `--skip-preload` | No | false | Skip scrolling phase; use existing cache only |
| `--scroll-steps` | No | 80 | Scroll steps per chat during pre-load (higher = more history, slower) |
| `--self-id` | No | env `SEATALK_SELF_ID` | Your own SeaTalk user ID (adds `fromSelf` field to output) |
| `--output-dir` | No | `output/` | Directory for output files |
| `--max-text` | No | 5000 | Max characters per message text |
| `--use-seatalk-dir` | No | auto-discover | Explicit path to `use-seatalk/` folder |

## Output

Two files are written to `--output-dir` (default `output/`):

### JSON (`<name>_messages_<timestamp>.json`)

```json
{
  "targetId": 15757,
  "targetName": "Wang Shuning",
  "window": { "start": "2026-03-17", "end": "2026-04-16" },
  "totalMessages": 427,
  "conversationCount": 8,
  "conversations": [
    {
      "type": "buddy",
      "name": "Wang Shuning (1:1)",
      "id": 15757,
      "messageCount": 52,
      "coverage": { "earliestDate": "2026-03-18", "latestDate": "2026-04-16" },
      "messages": [
        {
          "ts": 1742345678,
          "datetime": "2026-03-19 10:14:38",
          "sender": "Wang Shuning",
          "senderId": 15757,
          "text": "Let's sync on the Q2 plan",
          "inThread": false,
          "tag": "text"
        }
      ]
    },
    {
      "type": "group",
      "name": "SIP Core Leads",
      "id": 3259178,
      "messageCount": 120,
      "messages": [ ... ]
    }
  ]
}
```

### Transcript (`<name>_messages_<timestamp>.txt`)

Human-readable format grouped by conversation with timestamps:

```
SeaTalk Message Extract: Wang Shuning
Window: 2026-03-17 to 2026-04-16
Total messages: 427 across 8 conversations

============================================================
1:1 Chat: Wang Shuning (1:1)
Coverage: 2026-03-18 to 2026-04-16 (52 messages)
============================================================

[2026-03-19 10:14:38] Wang Shuning: Let's sync on the Q2 plan
[2026-03-19 10:15:02] Nicholas: Sure, 3pm?
  [Thread] [2026-03-19 10:16:00] Wang Shuning: Works for me

============================================================
Group: SIP Core Leads (ID: 3259178)
Coverage: 2026-03-17 to 2026-04-16 (120 messages)
============================================================

[2026-03-17 09:00:12] Emily Lin: Morning standup notes...
```

## Recommendations

- **Default to `--skip-preload` for non-invasive reading.** This reads from the Redux cache only — no UI clicking, no marking messages as read, and the user can keep using SeaTalk. SeaTalk keeps recent messages in memory; this is usually sufficient for the last few days.
- **Use `--scroll-steps`** (without `--skip-preload`) only when you need deeper history **and** the user is okay with the script taking over the SeaTalk UI temporarily. This scrolls through each chat to force-load older messages into cache. Higher values = more history but slower and less stable. **This will mark messages as read and cause visible UI navigation.**
- **Cache-only mode is reliable.** The scroll pre-loading phase can crash if there are many groups. If it fails, fall back to `--skip-preload`.
- Add `output/` to your `.gitignore` to avoid committing extracted data.

## UI Impact

| Mode | UI Interaction | Marks Read | User Can Use SeaTalk |
|------|---------------|------------|---------------------|
| `--skip-preload` | None (Redux read only) | No | Yes |
| Default (with preload) | Scrolls through chats | Yes | No |
| `--scroll-steps N` | Heavy scrolling | Yes | No |
