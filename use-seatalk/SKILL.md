---
name: use-seatalk
description: Read SeaTalk Desktop messages via Chrome DevTools Protocol and reply via webhook. Connects to SeaTalk Electron's in-memory Redux store for real-time message capture.
---

# use-seatalk

## How It Works

1. SeaTalk Desktop is an Electron app with a Redux store (`window.store`) holding all messages in memory.
2. `cdp-reader.py` connects via CDP WebSocket to the renderer process.
3. A `store.subscribe()` callback detects new messages and queues them.
4. The Python listener polls the queue every 2 seconds and outputs JSON-lines.
5. `seatalk-listener.sh` pipes each message through a formatter and forwards it to a target agent via `agent-manager`.

The listener runs in a **fixed tmux session** (`seatalk-listener`) for easy management.

## Prerequisites

**Python dependency:**

```bash
pip3 install websocket-client
```

**Node.js dependency (for app.asar patching):**

```bash
npm install -g asar   # or: npx asar (auto-installed on first use)
```

**SeaTalk must be launched with remote debugging enabled.**

**Recommended: One-shot patch + restart** (handles Electron 30+ automatically):

```bash
bash scripts/seatalk-restart-debug.sh
```

This script automatically:
1. Extracts `app.asar`, injects the CDP switch into `src/index.js`
2. Repacks `app.asar` (original saved as `app.asar.bak`)
3. Restarts SeaTalk and verifies CDP is listening

### Why patching is needed (Electron 30+ / SeaTalk 2.9.3+)

Electron 30+ (Chromium 134+) **silently ignores** `--remote-debugging-port` as a CLI argument. The only way to enable CDP is via `app.commandLine.appendSwitch()` inside the app code. `seatalk-restart-debug.sh` automatically detects and applies this patch.

The patch survives normal restarts but **SeaTalk auto-updates will overwrite app.asar** — just re-run `seatalk-restart-debug.sh` after an update.

**Optional: Install the auto-debug LaunchAgent** (ensures SeaTalk runs with CDP after reboots, Dock clicks, macOS app-restore):

```bash
bash scripts/seatalk-debug-autostart.sh install
```

This installs a macOS LaunchAgent that:
- Launches SeaTalk with `--remote-debugging-port=19222` at login
- Checks every 30 seconds — if SeaTalk is running without the debug port, it kills and relaunches with debug flags
- Covers all launch scenarios: Dock, Finder, Spotlight, reboot, crash recovery

> **Note:** On Electron 30+ (SeaTalk 2.9.3+), you must run `seatalk-restart-debug.sh` at least once to apply the app.asar patch before the LaunchAgent can work. The CLI arg alone is insufficient.

Management:

```bash
bash scripts/seatalk-debug-autostart.sh status     # check if installed + working
bash scripts/seatalk-debug-autostart.sh uninstall   # remove the LaunchAgent
```

## Quick Start

```bash
# 1. Start the listener (runs in tmux session "seatalk-listener")
bash scripts/seatalk-listener.sh start

# 2. Check status
bash scripts/seatalk-listener.sh status

# 3. Attach to see live output
bash scripts/seatalk-listener.sh attach
# (detach with Ctrl-B D)

# 4. Stop
bash scripts/seatalk-listener.sh stop
```

## Troubleshooting: messages not reaching Cursor

1. **CDP disconnected / port not listening** — Run `bash scripts/seatalk-listener.sh status`. If `cdp_listening: no` or the log shows `Connection refused` on port 19222, SeaTalk was started without `--remote-debugging-port` (e.g. after reboot or clicking the Dock icon). **One-shot fix:** `bash scripts/seatalk-restart-debug.sh` (patches app.asar for Electron 30+ + restarts SeaTalk) then `seatalk-listener.sh restart`. **Permanent fix:** also install the LaunchAgent: `bash scripts/seatalk-debug-autostart.sh install`.
2. **Historical replay flood after reconnect / re-inject** — Restarting SeaTalk or CDP re-inject clears in-page dedup. `cdp-reader.py` mitigates this by: seeding `__ST_CDP_SEEN__` from current Redux lists on inject; not using `Date.now()` as a fake message timestamp; and when the **first** drained batch after connect/re-inject is larger than `SEATALK_INITIAL_BATCH_SKIP_THRESHOLD` (default **12**), **only messages with server time within the last `SEATALK_REPLAY_FRESH_SEC` seconds** (default **180**) are forwarded—older rows in that batch are dropped (no whole-batch discard, so a new real message is not lost with the replay). Zero-timestamp messages are still dropped when `last_forwarded_ts` is already set.
3. **CDP lost mid-session** — Log shows `Connection to remote host was lost`. Same recovery as (1).
4. **Same-second bursts dropped (fixed in cdp-reader)** — The listener used `timestamp <= last` for dedup, which dropped every message after the first in the same second. Dedup now uses strict `<` so bursts are forwarded.
5. **Admin filter** — With `SEATALK_ADMIN_IDS`, only listed numeric IDs pass. Sender IDs are normalized (int/str) in JS and Python.
6. **agent-manager** — If `Forwarded → main` appears in `logs/seatalk-listener.log` but Cursor shows nothing, check the agent provider / `agent-manager.sh send` path.
7. **Webhook replies accepted but not visible** — Group system-account webhooks are documented as `tag: text` + `text.content`. Default reply format is **`text`** (no undocumented `format` field). Use `SEATALK_REPLY_FORMAT=markdown` only if your tenant supports it.

## Commands

| Command | Description |
|---------|-------------|
| `start` | Start listener in a background tmux session |
| `stop` | Kill the tmux session |
| `status` | Show running state and config |
| `attach` | Attach to the tmux session (live view) |
| `restart` | Stop + start |
| `restart-with-debug` | Restart SeaTalk with CDP (`seatalk-restart-debug.sh`) + restart listener |
| `once` | Read currently cached messages once (no loop) |
| `reply "msg"` | Send a reply to the watched group via webhook |

### Sending replies

```bash
# Safe reply (recommended) - prevents duplicates and handles special characters
bash scripts/safe-reply.sh "Task completed successfully."

# Traditional reply (direct)
bash scripts/seatalk-reply.sh "Task completed successfully."

# Plain text
bash scripts/seatalk-reply.sh --format text "plain message"

# Also available via the listener wrapper
bash scripts/seatalk-listener.sh reply "message"
```

#### Safe Reply Features

The `safe-reply.sh` script provides enhanced reliability:

- **Deduplication**: Prevents sending similar messages within 30 seconds
- **Safe escaping**: Uses stdin method to avoid shell escaping issues with special characters
- **Error logging**: Logs all reply attempts for debugging
- **Dry run mode**: Test messages without sending

```bash
# Safe reply with deduplication
bash scripts/safe-reply.sh "Message with `backticks` and $variables"

# Force send (skip deduplication)
bash scripts/safe-reply.sh --force "Send even if duplicate"

# Dry run (check without sending)
bash scripts/safe-reply.sh --dry-run "Test message"

# Plain text format
bash scripts/safe-reply.sh --format text "Plain message"
```

**Multi-line / 换行（Agent 必读）**

在 bash 里用普通双引号写 `"行1\n行2"` 时，`\n` **不会**变成换行，只会原样发给 SeaTalk，客户端看起来像「没有换行」。

推荐写法（任选其一）：

```bash
# 1) 从 stdin 读入（真实换行，最适合列表）
bash scripts/seatalk-reply.sh --stdin <<'EOF'
**标题**

- 第一点
- 第二点
EOF

# 2) ANSI-C 引号 $'...'
bash scripts/seatalk-reply.sh $'**标题**\n\n- 第一点\n- 第二点'

# 3)  argv 里写了字面量 \n 时，加 --expand-escapes
bash scripts/seatalk-reply.sh --expand-escapes "**标题**\n\n- 第一点\n- 第二点"
```

Replies are sent via **SeaTalk webhook** (fast, reliable, no UI dependency). Configure `SEATALK_WEBHOOK_URL` in `seatalk-listener.conf`.

### cdp-reader.py (low-level)

```bash
python3 scripts/cdp-reader.py targets                           # list CDP targets
python3 scripts/cdp-reader.py explore                            # probe Redux store structure
python3 scripts/cdp-reader.py eval "expression"                  # run arbitrary JS in renderer
python3 scripts/cdp-reader.py read --group 4172747               # read cached group messages (JSON)
python3 scripts/cdp-reader.py read-buddy --buddy 205031          # read cached private chat messages
python3 scripts/cdp-reader.py listen --group 4172747             # stream new messages (JSON-lines)
python3 scripts/cdp-reader.py threads --group 499098             # list threads in a group
python3 scripts/cdp-reader.py thread-messages --thread THREAD_ID # read thread conversation
python3 scripts/cdp-reader.py current-chat                       # read current chat (group or private)
python3 scripts/cdp-reader.py current-group                      # read currently opened group
python3 scripts/cdp-reader.py current-group --limit 10           # last 10 messages only
python3 scripts/cdp-reader.py current-thread                     # read currently opened thread
python3 scripts/cdp-reader.py current-thread --limit 20          # last 20 messages only
python3 scripts/cdp-reader.py thread-messages --thread TID --output /tmp/out.json  # write to file
SEATALK_ALLOW_SEND=true python3 scripts/cdp-reader.py send "Hello world"         # send message
```

### `redux_related_messages.py` — time-window, “related to me” (Redux + mdb)

Scans **Redux `messages.messages`** + **local DB (`window.mdb`)** for a **Unix time range** and returns JSON of rows **related to a user**:

- messages **you sent** (`senderId` = you);
- messages whose text **@ / mentions** common display patterns (Nicholas Lim, etc.);
- **full private chat** in-window if you sent anything there in that window;
- **full group** in-window (main channel + **thread replies from both Redux and local mdb**) if you sent anything in that group in that window.

**Thread discovery sources (4 layers):**
1. **Source A: Redux threadInfo** — root messages in `messages.messages` with `threadInfo.replyCount > 0`.
2. **Source B: Redux reply keys** — message keys with 4+ segments (`group-{gid}-{rootMid}-{replyMid}`) imply a threadId.
3. **Source C: Redux messages.lists** — hydrated thread panels with `lists[threadId]` arrays.
4. **Source D: mdb.getAllFollowedThreads()** — all threads the user follows, persisted in the local desktop DB even if the group was never opened in this session. This is the key source for DoD/support scenarios where threads accumulate across many groups.

After discovery, all threadIds are batch-queried via `mdb.getMessagesOfThread(threadId)` to pull replies from local DB.

Output includes `inThread` (boolean) and `threadId` fields per message, plus top-level `mdbThreadsQueried` count.

**Limits:** Only data in the desktop client (Redux + local DB). Not a server-side history API. Source D (`getAllFollowedThreads`) covers threads the user auto-follows (replied to), which greatly improves coverage for active participants.

Set your numeric SeaTalk id with **`--user-id`** or **`SEATALK_SELF_ID`**.

```bash
# Local calendar today 00:00 .. now
SEATALK_SELF_ID=12345 python3 scripts/redux_related_messages.py --today-local

# Rolling window
python3 scripts/redux_related_messages.py --user-id 12345 --last-days 7
python3 scripts/redux_related_messages.py --user-id 12345 --last-hours 48

# Absolute Unix seconds (inclusive)
python3 scripts/redux_related_messages.py --user-id 12345 --since-unix 1774540800 --until-unix 1774627200

# Local calendar dates (inclusive, end of until-date 23:59:59 local)
python3 scripts/redux_related_messages.py --user-id 12345 --since-date 2026-03-20 --until-date 2026-03-27

# Longer snippets per message
python3 scripts/redux_related_messages.py --user-id 12345 --last-days 1 --max-text 2000
```

Stdout is JSON: `selfId`, `window` (`startUnixSec` / `endUnixSec`), `mdbThreadsQueried`, `includedRows`, `messages` (`ts`, `session`, `channel` = `group`|`buddy`, `sender`, `fromSelf`, `text`, `inThread`, `threadId`).

**Thread commands:**

- `threads --group GROUP_ID` — Scans group messages for thread metadata (`threadInfo`). Returns thread list with ID, reply count, creator, timestamps. Data comes from the Redux store, so only threads from cached messages are visible.
- `thread-messages --thread THREAD_ID` — Reads thread messages from the local desktop cache by merging `window.mdb.getMessagesOfThread(threadId)` replies with Redux root/in-memory rows. Thread IDs use format `group-{gid}-{rootMid}`.
- `current-chat` — Reads messages from the currently selected chat (group or private). Auto-detects session type. This is the recommended command for "read whatever is currently open".
- `current-group` — Reads messages from the currently selected group chat in SeaTalk UI. No need to know the group ID.
- `current-thread` — Automatically detects and reads the currently opened thread in SeaTalk UI. No need to know the thread ID.

**Private chat (buddy) commands:**

- `read-buddy --buddy BUDDY_ID` — Reads cached messages from a specific private chat. Returns messages with `buddyId`, `buddyName`, `senderId`, `senderName`, `tag`, `text`, `timestamp`. Image messages include `imageInfo` with `fileId`, `width`, `height`, `size`.
- `switch-buddy --buddy BUDDY_ID` — Switches SeaTalk UI to a private chat. Uses sidebar click or global search fallback.

**DoD workflow commands (for automated heartbeat-driven workflows):**

```bash
# Query unread counts for specific groups
python3 scripts/cdp-reader.py unread --groups 499098,1743938

# Query all groups with unread > 0
python3 scripts/cdp-reader.py unread

# Mark every group + private chat with unread as read (opens each session via CDP; SeaTalk clears badge)
python3 scripts/cdp-reader.py mark-all-read

# Mark every thread in a group as read (scrolls main chat to load virtual list, open+close each thread root)
python3 scripts/cdp-reader.py mark-threads-read --group 499098 --only-unread --sync-timeout-sec 35
python3 scripts/cdp-reader.py mark-threads-read --group 499098 --max 50 --scroll-steps 120 --dwell-ms 600 --sync-timeout-sec 35

# Read unread messages (after ack cursor) for a group
python3 scripts/cdp-reader.py read-unread --group 1743938
python3 scripts/cdp-reader.py read-unread --group 1743938 --limit 10

# List joined groups (with optional name filter)
python3 scripts/cdp-reader.py list-groups
python3 scripts/cdp-reader.py list-groups --filter "Spex"

# Switch SeaTalk UI to a group chat
python3 scripts/cdp-reader.py switch-chat --group 499098

# Read a private chat by buddy ID
python3 scripts/cdp-reader.py read-buddy --buddy 205031
python3 scripts/cdp-reader.py read-buddy --buddy 205031 --limit 10

# Switch SeaTalk UI to a private chat
python3 scripts/cdp-reader.py switch-buddy --buddy 205031

# Open a thread panel for a specific message
python3 scripts/cdp-reader.py open-thread --group 499098 --message 5cce1a42b7e60299

# Close the currently open thread panel
python3 scripts/cdp-reader.py close-thread

# Reply in a thread (switch + open + send, one step)
SEATALK_ALLOW_SEND=true python3 scripts/cdp-reader.py reply-thread --group 499098 --message MID "reply text"
```

- `unread` — Returns JSON array of `{groupId, groupName, unread, ack}`. Pass `--groups ID1,ID2` to query specific groups, or omit to get all groups with unread > 0, sorted by count descending.
- `mark-all-read` — One CDP session: finds every `group-*` and `buddy-*` key in `messages.unreadCounts` with count &gt; 0, switches the SeaTalk UI into each chat (sidebar click or global-search fallback). Opening a chat clears its unread the same way as a manual click. stderr logs progress; stdout is a JSON summary `{groupsOpened, groupsFailed, buddiesOpened, buddiesFailed}`. Requires SeaTalk desktop running with remote debugging (same as other `cdp-reader` commands).
- `mark-threads-read ...` — Opens each thread root from Redux, waits for thread messages in store, then **polls `threadInfo.unreadReplyCount` / `unreadMentionCount` on the root message** until both are 0 (or `--sync-timeout-sec`). Each poll cycle re-runs in-drawer “read” simulation (focus composer, scroll `[data-mid]`, scroll to bottom, click message area). **`threads --group`** output includes those unread fields. **`--only-unread`** limits work to threads that still show unread in Redux.
- `probe-thread-read [--group GID]` — Prints `messages` slice keys, odd `unreadCounts` keys, and a slim `threadInfo` sample from the group (for debugging why unreads do not drop).
- `read-unread` — Reads messages after the ack cursor (incremental read). Each message includes `threadInfo` if it's a thread root. Supports `--limit` and `--output`.
- `list-groups` — Lists all joined groups from `contact.groupInfo`. Use `--filter "name"` for case-insensitive name filtering.
- `switch-chat` — Clicks the group in the sidebar chat list to switch the SeaTalk UI. Outputs switched group info to stdout.
- `switch-buddy` — Switches SeaTalk UI to a private chat (buddy). Uses sidebar click with global search fallback.
- `open-thread` — Opens a thread panel for the specified message. Auto-switches group if needed. The message must be visible in the chat list (recently loaded).
- `close-thread` — Closes the currently open thread panel by clicking the back button.
- `reply-thread` — One-step compound: switch group + open thread + send message. Controlled by `SEATALK_ALLOW_SEND`.

**Output control (applies to `thread-messages`, `current-thread`, `current-group`, `read-unread`):**

| Flag | Description |
|------|-------------|
| `--limit N` | Only return the last N messages (tail). Useful for large threads. |
| `--output FILE` | Write JSON to file instead of stdout. |

If neither flag is set and the thread exceeds 50 messages, output is **auto-redirected** to `/tmp/seatalk-thread-messages.json` to prevent context overflow.

**Send command:**

- `send "message text"` — Composes the message in the active SeaTalk editor (group or thread) and sends it via Enter. Supports `@Name` mentions that resolve to real SeaTalk mentions (not plain text).

**Mention syntax:**

| Syntax | Example | Description |
|--------|---------|-------------|
| `@Name` | `@SomeName hello` | Mention by exact name (no spaces) |
| `@{Name With Spaces}` | `@{Nicholas Lim} hi` | Mention by name with spaces |

Mentions are resolved against the current group's member list. The name must exactly match the user's display name in SeaTalk.

> **IMPORTANT: `send` and `reply-thread` are disabled by default.** You must set `SEATALK_ALLOW_SEND=true` (env var or in `seatalk-listener.conf`) to enable them. The message is sent to whatever group or thread is currently open in the SeaTalk UI.

> **Important:** `thread-messages` now prefers the local desktop cache (`mdb` + Redux), so opening the thread UI is no longer required for already-synced local history. It still does **not** call a remote history API: if this desktop client has never synced that thread, or the root row is missing from cache, output may be partial or unavailable. Use `open-thread` only when you want to hydrate Redux/UI state explicitly.

## Configuration

**Config file is singular:** `config.sh` loads **either** `~/.use-seatalk/seatalk-listener.conf` **or** `seatalk-listener.conf` next to the skill — whichever exists first — **not both**. If your home file exists but omits `SEATALK_WATCH_GROUPS`, the listener watches **all** joined groups.

Set via environment variables or that one `seatalk-listener.conf`:

| Variable | Default | Description |
|----------|---------|-------------|
| `SEATALK_WATCH_GROUPS` | *(all)* | CSV of group IDs to monitor |
| `SEATALK_AGENT_TARGET` | `main` | Target agent for forwarding |
| `SEATALK_ADMIN_IDS` | *(all)* | CSV of user IDs allowed to trigger forwarding (whitelist) |
| `SEATALK_CDP_HOST` | `127.0.0.1` | CDP host |
| `SEATALK_CDP_PORT` | `19222` | CDP remote debugging port |
| `SEATALK_CDP_POLL` | `2` | Seconds between queue drain polls |
| `SEATALK_TMUX_SESSION` | `seatalk-listener` | tmux session name |
| `SEATALK_WEBHOOK_URL` | *(empty)* | SeaTalk group webhook URL for replies |
| `SEATALK_REPLY_FORMAT` | `text` | Reply format: `text` (recommended) or `markdown` if supported |
| `SEATALK_INITIAL_BATCH_SKIP_THRESHOLD` | `12` | First post-connect batch larger than this uses per-message replay filter (not full discard) |
| `SEATALK_REPLAY_FRESH_SEC` | `180` | In that large first batch, only forward messages newer than this many seconds |
| `SEATALK_ALLOW_SEND` | `false` | Enable CDP `send` command (type & send in active chat) |
| `SEATALK_SELF_ID` | *(unset)* | Your SeaTalk user id for `redux_related_messages.py` when `--user-id` omitted |

Example `seatalk-listener.conf`:

```bash
SEATALK_WATCH_GROUPS="4172747"
SEATALK_AGENT_TARGET="main"
SEATALK_ADMIN_IDS="90668"
```

## File Structure

```
scripts/
  cdp-reader.py              # CDP client — read & listen (read-only)
  redux_related_messages.py  # time-window query: messages related to a user (Redux + mdb)
  seatalk-listener.sh        # tmux-managed daemon — start/stop/attach/forward
  seatalk-reply.sh           # standalone reply script (webhook)
  seatalk-restart-debug.sh   # patch app.asar (Electron 30+) + restart SeaTalk with CDP + verify
  config.sh                  # shared defaults and config loader
seatalk-listener.conf    # local overrides (gitignored, contains webhook URL)
requirements.txt         # Python dependencies
```

## Troubleshooting

**Cannot connect to CDP:**
Run `seatalk-restart-debug.sh` — it patches app.asar (if needed) and restarts SeaTalk:

```bash
bash scripts/seatalk-restart-debug.sh
```

Verify:

```bash
curl -s http://127.0.0.1:19222/json | head
```

**CDP port closes ~3 seconds after startup (Electron 30+ / SeaTalk 2.9.3+):**
The CLI arg `--remote-debugging-port` is ignored by Electron 30+. Re-run `seatalk-restart-debug.sh` to re-apply the app.asar patch. Check with `grep remote-debugging-port` on the extracted `src/index.js` to confirm the patch is present.

**CDP stops working after SeaTalk auto-update:**
Auto-updates overwrite `app.asar`, removing the CDP patch. Re-run `seatalk-restart-debug.sh`. The original is always backed up as `app.asar.bak`.

**SeaTalk loses input focus:**
Always launch via `open -a SeaTalk --args ...`, never via the raw binary path.

**Bot/other messages still forwarded:**
Set `SEATALK_ADMIN_IDS` to your user ID. The filter is enforced in the Python drain loop regardless of JS state.

**Listener crashes / CDP disconnects:**
The `_run` loop auto-restarts after 5 seconds. Check `logs/seatalk-listener.log` for details.
