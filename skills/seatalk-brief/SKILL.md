---
name: seatalk-brief
description: Use when the user invokes /seatalk-brief or asks for a SeaTalk message summary — fetches messages from monitored groups and important people's private chats since the last briefing, then sends a formatted summary to the Claude SeaTalk group.
---

# SeaTalk Daily Brief

## Configuration

| Item | Value |
|------|-------|
| Scripts dir | `$SEATALK_SCRIPTS_DIR` if set (used by the automated launchd run from a local clone), else `/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge/use-seatalk/scripts/` |
| Checkpoint | `~/.use-seatalk/seatalk-brief-checkpoint.json` |
| Webhook | `https://openapi.seatalk.io/webhook/group/cEeKES9-SLGobUUDZZndIQ` |
| Nicholas ID | `47934` |

**Monitored groups:** SIP core leads `3259178`, Swarm marketing `3708121`, SIP leads `787950`

**Important people:** Wang Shuning `15757`, Qiang Wei `30070`, Cuiwei `521986`, Irene Ling `193233`, Emily Lin `548236`

## Workflow

### Step 1 — Read checkpoint

```bash
cat ~/.use-seatalk/seatalk-brief-checkpoint.json 2>/dev/null || echo '{"last_briefing_ts": 0}'
```

Compute `72h_ago`: `$(date -v-72H +%s)`. Then determine the effective `since` timestamp:
- If `last_briefing_ts` is `0` or missing: use `72h_ago`
- Otherwise: use `min(last_briefing_ts, 72h_ago)` — whichever is further back in time, giving the **longer** lookback period

This ensures the brief always covers at least 72 hours of messages, and if the last briefing was longer than 72h ago, it covers everything since then.

> **CRITICAL RULE:** The `since` timestamp from Step 1 is the sole time boundary for this brief. NEVER include or analyze messages older than `since`. ONLY use the pre-filter output from Step 3 as your data source — NEVER read the raw `/tmp/st-*.json` files directly.

### Step 2 — Fetch messages (non-invasive, no UI switching)

> **Design principle:** `read` and `read-buddy` are pure Redux memory reads — they never click the UI, never mark messages as read, and never interfere with the user's active chat. Only use `switch-chat`/`switch-buddy` in Phase B when data is missing and the user is not actively using SeaTalk.

#### Phase A — Read from Redux cache (no UI interaction)

```bash
# Clean stale temp files from previous runs
rm -rf /tmp/st-buddies /tmp/st-tagged-groups /tmp/st-tagged-threads
rm -f /tmp/st-g-*.json /tmp/st-b-*.json /tmp/st-cached-sessions.json /tmp/st-missing.txt

S="${SEATALK_SCRIPTS_DIR:-/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge/use-seatalk/scripts}"

# Check what sessions are already cached in Redux
python3 "$S/cdp-reader.py" cached-sessions 2>/dev/null > /tmp/st-cached-sessions.json

# Read monitored groups directly (no switch)
python3 "$S/cdp-reader.py" read --group 3259178 2>/dev/null > /tmp/st-g-sip-core.json
python3 "$S/cdp-reader.py" read --group 3708121 2>/dev/null > /tmp/st-g-swarm.json
python3 "$S/cdp-reader.py" read --group 787950  2>/dev/null > /tmp/st-g-sip-leads.json

# Read important people directly (no switch)
python3 "$S/cdp-reader.py" read-buddy --buddy 15757  2>/dev/null > /tmp/st-b-shuning.json
python3 "$S/cdp-reader.py" read-buddy --buddy 30070  2>/dev/null > /tmp/st-b-qiangwei.json
python3 "$S/cdp-reader.py" read-buddy --buddy 521986 2>/dev/null > /tmp/st-b-cuiwei.json
python3 "$S/cdp-reader.py" read-buddy --buddy 193233 2>/dev/null > /tmp/st-b-irene.json
python3 "$S/cdp-reader.py" read-buddy --buddy 548236 2>/dev/null > /tmp/st-b-emily.json

# Other buddies with unread messages — read from cache only
mkdir -p /tmp/st-buddies
KNOWN="15757 30070 521986 193233 548236"
UNREAD=$(python3 "$S/cdp-reader.py" eval "JSON.stringify(Object.entries(window.store.getState().messages.unreadCounts||{}).filter(([k,v])=>k.startsWith('buddy-')&&v>0).map(([k])=>k.replace('buddy-','')))" 2>/dev/null \
  | python3 -c "import json,sys; known=set('$KNOWN'.split()); [print(x) for x in json.loads(sys.stdin.read().strip()) if x not in known]" 2>/dev/null)
for B in $UNREAD; do
    python3 "$S/cdp-reader.py" read-buddy --buddy "$B" 2>/dev/null > "/tmp/st-buddies/$B.json"
done

# Groups with unread messages that might tag Nicholas — read from cache only
mkdir -p /tmp/st-tagged-groups
MONITORED_GROUPS="3259178 3708121 787950"
UNREAD_GROUPS=$(python3 "$S/cdp-reader.py" eval "JSON.stringify(Object.entries(window.store.getState().messages.unreadCounts||{}).filter(([k,v])=>k.startsWith('group-')&&v>0).map(([k])=>k.replace('group-','')))" 2>/dev/null \
  | python3 -c "import json,sys; known=set('$MONITORED_GROUPS'.split()); [print(x) for x in json.loads(sys.stdin.read().strip()) if x not in known]" 2>/dev/null)
for G in $UNREAD_GROUPS; do
    python3 "$S/cdp-reader.py" read --group "$G" 2>/dev/null > "/tmp/st-tagged-groups/$G.json"
done
```

#### Phase A check — Identify missing data

After Phase A, check which files are empty (session had no cached data). Run this inline script to list sessions that need preloading:

```bash
python3 - <<'CHECK_EOF'
import json, os
missing = []
for gid, path in [("3259178","/tmp/st-g-sip-core.json"),("3708121","/tmp/st-g-swarm.json"),("787950","/tmp/st-g-sip-leads.json")]:
    try:
        data = json.load(open(path))
        if not data: missing.append(f"group:{gid}")
    except: missing.append(f"group:{gid}")
for bid, path in [("15757","/tmp/st-b-shuning.json"),("30070","/tmp/st-b-qiangwei.json"),("521986","/tmp/st-b-cuiwei.json"),("193233","/tmp/st-b-irene.json"),("548236","/tmp/st-b-emily.json")]:
    try:
        data = json.load(open(path))
        if not data: missing.append(f"buddy:{bid}")
    except: missing.append(f"buddy:{bid}")
for bid in os.listdir("/tmp/st-buddies") if os.path.isdir("/tmp/st-buddies") else []:
    bid = bid.replace(".json","")
    try:
        data = json.load(open(f"/tmp/st-buddies/{bid}.json"))
        if not data: missing.append(f"buddy:{bid}")
    except: missing.append(f"buddy:{bid}")
for gid in os.listdir("/tmp/st-tagged-groups") if os.path.isdir("/tmp/st-tagged-groups") else []:
    gid = gid.replace(".json","")
    try:
        data = json.load(open(f"/tmp/st-tagged-groups/{gid}.json"))
        if not data: missing.append(f"group:{gid}")
    except: missing.append(f"group:{gid}")
if missing:
    with open("/tmp/st-missing.txt","w") as f:
        f.write("\n".join(missing))
    print(f"Missing: {len(missing)} sessions: {', '.join(missing)}")
else:
    print("All sessions cached — no UI switching needed")
CHECK_EOF
```

If the check prints "All sessions cached", skip Phase B entirely and proceed to Step 2b.

#### Phase B — Preload missing data (optional, uses UI switching)

> **Only run this if Phase A check reported missing sessions.** This phase switches chats (marks messages as read, briefly takes over the UI). Skip it to keep the brief fully non-invasive — the briefing will simply omit data for uncached sessions.

```bash
S="${SEATALK_SCRIPTS_DIR:-/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge/use-seatalk/scripts}"

# Save user's current session so we can restore it after
SAVED_SESSION=$(python3 "$S/cdp-reader.py" save-session 2>/dev/null)

# Preload and re-read each missing session
while IFS= read -r entry; do
    TYPE="${entry%%:*}"
    ID="${entry##*:}"
    if [ "$TYPE" = "group" ]; then
        python3 "$S/cdp-reader.py" switch-chat --group "$ID" 2>/dev/null
        sleep 0.3
        # Re-read into the appropriate file
        if [ "$ID" = "3259178" ]; then python3 "$S/cdp-reader.py" read --group "$ID" 2>/dev/null > /tmp/st-g-sip-core.json
        elif [ "$ID" = "3708121" ]; then python3 "$S/cdp-reader.py" read --group "$ID" 2>/dev/null > /tmp/st-g-swarm.json
        elif [ "$ID" = "787950" ]; then python3 "$S/cdp-reader.py" read --group "$ID" 2>/dev/null > /tmp/st-g-sip-leads.json
        else python3 "$S/cdp-reader.py" read --group "$ID" 2>/dev/null > "/tmp/st-tagged-groups/$ID.json"
        fi
    elif [ "$TYPE" = "buddy" ]; then
        python3 "$S/cdp-reader.py" switch-buddy --buddy "$ID" 2>/dev/null
        sleep 0.3
        if   [ "$ID" = "15757" ];  then python3 "$S/cdp-reader.py" read-buddy --buddy "$ID" 2>/dev/null > /tmp/st-b-shuning.json
        elif [ "$ID" = "30070" ];  then python3 "$S/cdp-reader.py" read-buddy --buddy "$ID" 2>/dev/null > /tmp/st-b-qiangwei.json
        elif [ "$ID" = "521986" ]; then python3 "$S/cdp-reader.py" read-buddy --buddy "$ID" 2>/dev/null > /tmp/st-b-cuiwei.json
        elif [ "$ID" = "193233" ]; then python3 "$S/cdp-reader.py" read-buddy --buddy "$ID" 2>/dev/null > /tmp/st-b-irene.json
        elif [ "$ID" = "548236" ]; then python3 "$S/cdp-reader.py" read-buddy --buddy "$ID" 2>/dev/null > /tmp/st-b-emily.json
        else python3 "$S/cdp-reader.py" read-buddy --buddy "$ID" 2>/dev/null > "/tmp/st-buddies/$ID.json"
        fi
    fi
done < /tmp/st-missing.txt

# Restore user's original session
RESTORE_TYPE=$(echo "$SAVED_SESSION" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('type',''))" 2>/dev/null)
RESTORE_ID=$(echo "$SAVED_SESSION" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null)
if [ -n "$RESTORE_TYPE" ] && [ -n "$RESTORE_ID" ]; then
    python3 "$S/cdp-reader.py" restore-session --"$RESTORE_TYPE" "$RESTORE_ID" 2>/dev/null
fi
```

### Step 2b — Fetch threads for tagged messages

Run a quick pre-scan to discover thread IDs in the tagged-group files, then fetch those threads. Skip this step entirely if no thread IDs are found.

```bash
S="${SEATALK_SCRIPTS_DIR:-/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge/use-seatalk/scripts}"
mkdir -p /tmp/st-tagged-threads

python3 - <<'SCAN_EOF'
import json, glob
NICHOLAS = 47934
seen = set()
for path in sorted(glob.glob('/tmp/st-tagged-groups/*.json')):
    try:
        msgs = json.load(open(path))
        for m in msgs:
            try:
                mids = [int(x) for x in (m.get('mentionedIds') or []) if str(x).isdigit()]
            except:
                mids = []
            if NICHOLAS not in mids:
                continue
            thread_id = m.get('threadId') or m.get('replyToThread') or m.get('threadRootId')
            if thread_id:
                seen.add(str(thread_id))
    except:
        pass
for tid in sorted(seen):
    print(tid)
SCAN_EOF
```

For each thread ID printed above, run:

```bash
THREAD_ID=<id_from_above>
python3 "$S/cdp-reader.py" thread-messages --thread "$THREAD_ID" 2>/dev/null > "/tmp/st-tagged-threads/$THREAD_ID.json"
```

> **Note:** If the scan prints nothing, skip this step — no threaded tags were found.

### Step 3 — Pre-filter (run before reading any data into context)

Run this script with `last_briefing_ts` as argument. Read only the compact output — **never read the raw JSON files**.

```bash
python3 - LAST_TS <<'EOF'
import json, glob, sys
from datetime import datetime, timezone, timedelta
SGT = timezone(timedelta(hours=8))
since = int(sys.argv[1])
IMPORTANT = {15757, 30070, 521986, 193233, 548236}
NICHOLAS = 47934
MAX = 300
results = {}

def fmt(ts): return datetime.fromtimestamp(ts, tz=SGT).strftime('%m-%d %H:%M')

def keep_group(m):
    if int(m.get('senderId', 0)) in IMPORTANT: return True
    text = (m.get('text') or '').lower()
    if str(NICHOLAS) in str(m.get('mentionedIds', [])): return True
    return any(k in text for k in ['?','please','pls','urgent','asap','decision','approve','action required'])

def proc(msgs, src, mode):
    out = []
    for m in msgs:
        if m.get('timestamp', 0) < since: continue
        tag = m.get('tag', 'text')
        text = (m.get('text') or '').strip()
        if not text and tag == 'text': continue
        if mode == 'group' and not keep_group(m): continue
        e = {'t': fmt(m['timestamp']), 'from': m.get('senderName','?'), 'sid': m.get('senderId')}
        if text: e['msg'] = text[:MAX] + ('…' if len(text) > MAX else '')
        if tag != 'text': e['type'] = tag
        out.append(e)
    if out: results[src] = out

def load(path, src, mode):
    try:
        proc(json.load(open(path)), src, mode)
    except: pass

CONTEXT_BEFORE = 20
CONTEXT_AFTER = 10

def proc_tagged_group(msgs, src):
    """Find messages that tag Nicholas; prefer full thread context, fall back to ±window."""
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
            thread_path = f'/tmp/st-tagged-threads/{thread_id}.json'
            try:
                thread_msgs = json.load(open(thread_path))
                if not thread_msgs:
                    raise FileNotFoundError
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

        # Window fallback: CONTEXT_BEFORE before + CONTEXT_AFTER after
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

load('/tmp/st-g-sip-core.json',  'SIP Core',   'group')
load('/tmp/st-g-swarm.json',     'Swarm Mktg', 'group')
load('/tmp/st-g-sip-leads.json', 'SIP Leads',  'group')

for bid, name, path in [
    (15757,'Shuning','/tmp/st-b-shuning.json'),
    (30070,'Qiang Wei','/tmp/st-b-qiangwei.json'),
    (521986,'Cuiwei','/tmp/st-b-cuiwei.json'),
    (193233,'Irene','/tmp/st-b-irene.json'),
    (548236,'Emily','/tmp/st-b-emily.json'),
]:
    load(path, f'DM:{name}', 'buddy')

for path in sorted(glob.glob('/tmp/st-buddies/*.json')):
    try:
        msgs = json.load(open(path))
        name = next((m.get('senderName', '?') for m in msgs if str(m.get('senderId')) != str(NICHOLAS)), '?')
        proc(msgs, f'DM:{name}', 'buddy')
    except: pass

for path in sorted(glob.glob('/tmp/st-tagged-groups/*.json')):
    try:
        gid = path.split('/')[-1].replace('.json', '')
        msgs = json.load(open(path))
        proc_tagged_group(msgs, f'Tagged:{gid}')
    except:
        pass

print(json.dumps(results, ensure_ascii=False, separators=(',',':')))
EOF
```

Replace `LAST_TS` with the effective `since` timestamp computed in Step 1 (NOT the raw `last_briefing_ts`). The output is the **only data to analyze** — compact JSON, all sources merged, already filtered.

### Step 4 — Format briefing

```
**SeaTalk Brief** — {date} {time} SGT

**Executive Summary**
2–4 sentences. Most urgent items, decisions, risks.

---
**Important Messages**
• [{source}] {Name}: {1-line summary} — {time SGT}

---
**Needs a Reply**
• {Name}: "{summary}" — reply by {deadline or "soon"}
(omit section if nothing)

---
**Tagged in Groups** ⚑
• [{group ID from source key, e.g. "3259178"}] Context: {brief summary of thread leading up to tag} — {time SGT}
  ↳ @Nicholas: "{the message that tagged you}"
(omit section if no tagged messages)

---
**Can Wait**
• [{source}] {Name}: {1-line summary}
(omit section if nothing)
```

Rules: lead with most important; deduplicate cross-source topics; skip images/files with no caption; SGT = UTC+8. For Tagged in Groups: use the `tagged: true` flag to identify the tag message; summarise preceding context as a brief thread lead-in; if entry has a `thread` key, append "(thread)" to the bracket label, e.g. [3259178 (thread)].

### Step 5 — Send via webhook

```bash
cat <<'PAYLOAD_EOF' | python3 -c "
import sys, json, urllib.request
body = sys.stdin.read()
data = json.dumps({'tag': 'text', 'text': {'content': body}}).encode()
req = urllib.request.Request('https://openapi.seatalk.io/webhook/group/cEeKES9-SLGobUUDZZndIQ', data=data, headers={'Content-Type': 'application/json'}, method='POST')
print(urllib.request.urlopen(req).read().decode())
"
**SeaTalk Brief** — {date time SGT}

{briefing body}
PAYLOAD_EOF
```

Verify `"code":0` before updating checkpoint.

### Step 6 — Update checkpoint (only on success)

```bash
echo "{\"last_briefing_ts\": $(date +%s), \"last_briefing_date\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > ~/.use-seatalk/seatalk-brief-checkpoint.json
```
