# All-Groups Tag Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the seatalk-brief skill to scan all SeaTalk groups with unread messages for explicit mentions of Nicholas (ID 47934), not just the 3 monitored groups, and include 20-message-before / 10-message-after context (or full thread if threaded).

**Architecture:** The new scan runs after the existing monitored-group fetches in Step 2, using a JS eval to get all group IDs with unread counts from the Redux store. For each unread group (excluding already-monitored ones), we switch-chat and read messages into `/tmp/st-tagged-groups/`. The pre-filter script gains a new `proc_tagged_group` path that checks for mentions of Nicholas and builds a windowed context block per tag. The briefing format gains a new **Tagged in Groups** section.

**Tech Stack:** Python 3 (inline scripts in SKILL.md), SeaTalk CDP via `cdp-reader.py` eval + switch-chat + read + thread-messages commands, zsh.

---

## File Structure

| File | Change |
|------|--------|
| `/Users/nicholas.lim/.claude/skills/seatalk-brief/SKILL.md` | Modify Step 2 (fetch), Step 3 (pre-filter), Step 4 (format) |

No new files created — all logic is inline in the skill.

---

### Task 1: Add unread-group discovery + fetch to Step 2

**Files:**
- Modify: `/Users/nicholas.lim/.claude/skills/seatalk-brief/SKILL.md` — Step 2 bash block

**Context:** The existing buddy unread-scan pattern works like this:
1. Run a JS eval to get all buddy IDs with unread > 0
2. Filter out known/important ones
3. Loop: switch-buddy → read-buddy → save to `/tmp/st-buddies/<id>.json`

We apply the same pattern for groups.

- [ ] **Step 1: Append the tagged-group fetch block to the Step 2 bash section**

At the end of the Step 2 bash block in `SKILL.md`, after the buddies loop, add:

```bash
# Groups with unread messages that might tag Nicholas (all groups except monitored)
mkdir -p /tmp/st-tagged-groups
MONITORED_GROUPS="3259178 3708121 787950"
UNREAD_GROUPS=$(python3 "$S/cdp-reader.py" eval "JSON.stringify(Object.entries(window.store.getState().messages.unreadCounts||{}).filter(([k,v])=>k.startsWith('group-')&&v>0).map(([k])=>k.replace('group-','')))" 2>/dev/null \
  | python3 -c "import json,sys; known=set('$MONITORED_GROUPS'.split()); [print(x) for x in json.loads(sys.stdin.read().strip()) if x not in known]" 2>/dev/null)
for G in $UNREAD_GROUPS; do
    python3 "$S/cdp-reader.py" switch-chat --group "$G" 2>/dev/null && python3 "$S/cdp-reader.py" read --group "$G" 2>/dev/null > "/tmp/st-tagged-groups/$G.json"
done
```

- [ ] **Step 2: Commit**

```bash
cd "/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge"
git add /Users/nicholas.lim/.claude/skills/seatalk-brief/SKILL.md
git commit -m "feat(seatalk-brief): fetch all unread groups for tag scanning"
```

---

### Task 2: Add tagged-group processing to Step 3 pre-filter

**Files:**
- Modify: `/Users/nicholas.lim/.claude/skills/seatalk-brief/SKILL.md` — Step 3 Python script

**Context:** The pre-filter reads raw JSON files and outputs compact filtered JSON. We need a new function `proc_tagged_group(msgs, group_id)` that:
1. Scans all messages for any that mention Nicholas (47934 in `mentionedIds`)
2. For each tagged message:
   - If it has a `threadId` field → mark for thread fetch (handled in Task 3)
   - Otherwise → extract a window of 20 messages before + 10 after
3. Outputs deduplicated context blocks under key `Tagged:<group_name_or_id>`

- [ ] **Step 1: Add imports and the `proc_tagged_group` function to the Step 3 script**

In the Python script in Step 3, add after the existing `proc` and `load` function definitions:

```python
CONTEXT_BEFORE = 20
CONTEXT_AFTER = 10

def proc_tagged_group(msgs, src):
    """Find messages that tag Nicholas and return windowed context."""
    if not msgs:
        return
    # sort by timestamp ascending for stable indexing
    msgs_sorted = sorted(msgs, key=lambda m: m.get('timestamp', 0))
    # filter to messages after last brief
    msgs_sorted = [m for m in msgs_sorted if m.get('timestamp', 0) >= since]
    if not msgs_sorted:
        return

    tagged_indices = [
        i for i, m in enumerate(msgs_sorted)
        if str(NICHOLAS) in str(m.get('mentionedIds', []))
    ]
    if not tagged_indices:
        return

    # Collect context windows (deduplicated by message index)
    included = set()
    out = []
    for ti in tagged_indices:
        window_start = max(0, ti - CONTEXT_BEFORE)
        window_end = min(len(msgs_sorted), ti + CONTEXT_AFTER + 1)
        for i in range(window_start, window_end):
            if i in included:
                continue
            included.add(i)
            m = msgs_sorted[i]
            tag = m.get('tag', 'text')
            text = (m.get('text') or '').strip()
            if not text and tag == 'text':
                continue
            e = {
                't': fmt(m['timestamp']),
                'from': m.get('senderName', '?'),
                'sid': m.get('senderId'),
            }
            if text:
                e['msg'] = text[:MAX] + ('…' if len(text) > MAX else '')
            if tag != 'text':
                e['type'] = tag
            if i == ti:
                e['tagged'] = True   # mark the actual tagged message
            # Note thread ID if present — thread fetch is a separate step
            thread_id = m.get('threadId') or m.get('replyToThread') or m.get('threadRootId')
            if thread_id:
                e['threadId'] = str(thread_id)
            out.append(e)
    if out:
        results[src] = out
```

- [ ] **Step 2: Add the call to `proc_tagged_group` at the bottom of the pre-filter, after the buddies loop**

```python
for path in sorted(glob.glob('/tmp/st-tagged-groups/*.json')):
    try:
        gid = path.split('/')[-1].replace('.json', '')
        msgs = json.load(open(path))
        proc_tagged_group(msgs, f'Tagged:{gid}')
    except:
        pass
```

- [ ] **Step 3: Commit**

```bash
cd "/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge"
git add /Users/nicholas.lim/.claude/skills/seatalk-brief/SKILL.md
git commit -m "feat(seatalk-brief): pre-filter tagged-group messages with windowed context"
```

---

### Task 3: Add thread fetch for threaded tagged messages

**Files:**
- Modify: `/Users/nicholas.lim/.claude/skills/seatalk-brief/SKILL.md` — between Step 2 and Step 3

**Context:** When `proc_tagged_group` finds a tagged message with a `threadId`, the full thread context is better than the ±window. We need a small bash block that runs after the main group/buddy fetches but before Step 3, to fetch thread messages for any tagged threads.

The cdp-reader command is:
```bash
python3 "$S/cdp-reader.py" thread-messages --thread THREAD_ID
```

- [ ] **Step 1: Add a new "Step 2b — Fetch threads for tagged messages" bash block in SKILL.md**

Insert this between Step 2 and Step 3:

````markdown
### Step 2b — Fetch threads for tagged messages

Run a quick pre-scan to discover any thread IDs in the tagged-group files, then fetch those threads.

```bash
S="/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge/use-seatalk/scripts"
mkdir -p /tmp/st-tagged-threads

python3 - <<'EOF'
import json, glob, os
NICHOLAS = 47934
for path in sorted(glob.glob('/tmp/st-tagged-groups/*.json')):
    try:
        msgs = json.load(open(path))
        for m in msgs:
            if NICHOLAS not in [int(x) for x in (m.get('mentionedIds') or []) if str(x).isdigit()]:
                continue
            thread_id = m.get('threadId') or m.get('replyToThread') or m.get('threadRootId')
            if thread_id:
                print(thread_id)
    except:
        pass
EOF
```

For each thread ID printed above, run:

```bash
THREAD_ID=<id_from_above>
python3 "$S/cdp-reader.py" thread-messages --thread "$THREAD_ID" 2>/dev/null > "/tmp/st-tagged-threads/$THREAD_ID.json"
```

> **Note:** If the pre-scan prints nothing, skip this step entirely.
````

- [ ] **Step 2: Update `proc_tagged_group` to prefer thread data over window when a threadId is present**

In the pre-filter Python script (Step 3), add a helper `load_thread_msgs` and update the tagged-group processing call:

```python
def proc_tagged_group(msgs, src):
    """Find messages that tag Nicholas; use thread context if available, else ±window."""
    if not msgs:
        return
    msgs_sorted = sorted(msgs, key=lambda m: m.get('timestamp', 0))
    msgs_sorted = [m for m in msgs_sorted if m.get('timestamp', 0) >= since]
    if not msgs_sorted:
        return

    tagged_indices = [
        i for i, m in enumerate(msgs_sorted)
        if str(NICHOLAS) in str(m.get('mentionedIds', []))
    ]
    if not tagged_indices:
        return

    included = set()
    out = []
    used_thread_ids = set()

    for ti in tagged_indices:
        m = msgs_sorted[ti]
        thread_id = m.get('threadId') or m.get('replyToThread') or m.get('threadRootId')

        if thread_id and str(thread_id) not in used_thread_ids:
            # Prefer full thread context
            thread_path = f'/tmp/st-tagged-threads/{thread_id}.json'
            try:
                thread_msgs = json.load(open(thread_path))
                used_thread_ids.add(str(thread_id))
                for tm in thread_msgs:
                    tag = tm.get('tag', 'text')
                    text = (tm.get('text') or '').strip()
                    if not text and tag == 'text':
                        continue
                    e = {
                        't': fmt(tm.get('timestamp', 0)),
                        'from': tm.get('senderName', '?'),
                        'sid': tm.get('senderId'),
                        'thread': str(thread_id),
                    }
                    if text:
                        e['msg'] = text[:MAX] + ('…' if len(text) > MAX else '')
                    if tag != 'text':
                        e['type'] = tag
                    out.append(e)
                continue
            except (FileNotFoundError, json.JSONDecodeError):
                pass  # fall through to window

        # Window fallback: 20 before + 10 after
        window_start = max(0, ti - CONTEXT_BEFORE)
        window_end = min(len(msgs_sorted), ti + CONTEXT_AFTER + 1)
        for i in range(window_start, window_end):
            if i in included:
                continue
            included.add(i)
            wm = msgs_sorted[i]
            tag = wm.get('tag', 'text')
            text = (wm.get('text') or '').strip()
            if not text and tag == 'text':
                continue
            e = {
                't': fmt(wm['timestamp']),
                'from': wm.get('senderName', '?'),
                'sid': wm.get('senderId'),
            }
            if text:
                e['msg'] = text[:MAX] + ('…' if len(text) > MAX else '')
            if tag != 'text':
                e['type'] = tag
            if i == ti:
                e['tagged'] = True
            out.append(e)

    if out:
        results[src] = out
```

- [ ] **Step 3: Commit**

```bash
cd "/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge"
git add /Users/nicholas.lim/.claude/skills/seatalk-brief/SKILL.md
git commit -m "feat(seatalk-brief): fetch full thread context for threaded tagged messages"
```

---

### Task 4: Add "Tagged in Groups" section to Step 4 briefing format

**Files:**
- Modify: `/Users/nicholas.lim/.claude/skills/seatalk-brief/SKILL.md` — Step 4 format template

**Context:** The new `Tagged:<gid>` keys in the pre-filter output need a dedicated section in the brief so they stand out clearly and aren't missed.

- [ ] **Step 1: Add the Tagged in Groups section to the Step 4 format block**

In the Step 4 format template, add this section before "Can Wait":

```
---
**Tagged in Groups** ⚑
• [{group name or ID}] {context: who said what, leading up to the tag} — {time SGT}
  ↳ @Nicholas: "{the message that tagged you}"
(omit section if no tagged messages)
```

- [ ] **Step 2: Add formatting rule for tagged groups**

In the Step 4 Rules line, append:

```
For Tagged in Groups: use the `tagged: true` flag to identify the actual tag message; show the preceding context as a brief thread summary; if `thread` key is present, label the source as a thread reply.
```

- [ ] **Step 3: Commit**

```bash
cd "/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge"
git add /Users/nicholas.lim/.claude/skills/seatalk-brief/SKILL.md
git commit -m "feat(seatalk-brief): add Tagged in Groups section to briefing format"
```

---

## Rollback Instructions

If the all-groups scan is too slow or consumes too many tokens:

```bash
cd "/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge"
git checkout master
# The feature branch stays intact for future reference:
# git branch feature/all-groups-tag-scan
```

To permanently discard:
```bash
git branch -D feature/all-groups-tag-scan
```

---

## Self-Review

**Spec coverage:**
- ✅ Monitored group chats — unchanged, existing Step 2 logic preserved
- ✅ Important people DMs — unchanged, existing Step 2 logic preserved
- ✅ All 1:1 DMs (unread + important people) — unchanged
- ✅ Group chats that tag Nicholas explicitly — Task 1 (fetch), Task 2 (pre-filter window), Task 3 (thread context), Task 4 (format section)
- ✅ 20 before / 10 after context window — Task 2 + Task 3 window fallback
- ✅ Full thread context for threaded replies — Task 3 thread-preference logic
- ✅ Rollback as branch — branch created before any changes; rollback section documented

**Placeholder scan:** No TBD/TODO placeholders. Thread ID field names (`threadId`, `replyToThread`, `threadRootId`) are listed as candidates since the exact field name in the Redux store is uncertain — the code tries all three with `or`.

**Type consistency:** `proc_tagged_group` uses the same `fmt`, `MAX`, `since`, `NICHOLAS`, `results` globals as the existing `proc` function. Output keys follow the same `{source: [entries]}` shape.
