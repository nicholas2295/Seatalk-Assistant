---
name: seatalk-brief
description: Use when the user invokes /seatalk-brief or asks for a SeaTalk message summary — fetches messages from monitored groups and important people's private chats since the last briefing, then sends a formatted summary to the Claude SeaTalk group.
---

# SeaTalk Daily Brief

## Configuration

| Item | Value |
|------|-------|
| Scripts dir | `/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge/use-seatalk/scripts/` |
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

If `last_briefing_ts` is `0`, use 72h ago: `$(date -v-72H +%s)`.

### Step 2 — Fetch messages (switch before each read)

```bash
S="/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge/use-seatalk/scripts"

python3 "$S/cdp-reader.py" switch-chat --group 3259178 2>/dev/null && python3 "$S/cdp-reader.py" read --group 3259178 2>/dev/null > /tmp/st-g-sip-core.json
python3 "$S/cdp-reader.py" switch-chat --group 3708121 2>/dev/null && python3 "$S/cdp-reader.py" read --group 3708121 2>/dev/null > /tmp/st-g-swarm.json
python3 "$S/cdp-reader.py" switch-chat --group 787950  2>/dev/null && python3 "$S/cdp-reader.py" read --group 787950  2>/dev/null > /tmp/st-g-sip-leads.json

python3 "$S/cdp-reader.py" switch-buddy --buddy 15757  2>/dev/null && python3 "$S/cdp-reader.py" read-buddy --buddy 15757  2>/dev/null > /tmp/st-b-shuning.json
python3 "$S/cdp-reader.py" switch-buddy --buddy 30070  2>/dev/null && python3 "$S/cdp-reader.py" read-buddy --buddy 30070  2>/dev/null > /tmp/st-b-qiangwei.json
python3 "$S/cdp-reader.py" switch-buddy --buddy 521986 2>/dev/null && python3 "$S/cdp-reader.py" read-buddy --buddy 521986 2>/dev/null > /tmp/st-b-cuiwei.json
python3 "$S/cdp-reader.py" switch-buddy --buddy 193233 2>/dev/null && python3 "$S/cdp-reader.py" read-buddy --buddy 193233 2>/dev/null > /tmp/st-b-irene.json
python3 "$S/cdp-reader.py" switch-buddy --buddy 548236 2>/dev/null && python3 "$S/cdp-reader.py" read-buddy --buddy 548236 2>/dev/null > /tmp/st-b-emily.json

# Other buddies with unread messages
mkdir -p /tmp/st-buddies
KNOWN="15757 30070 521986 193233 548236"
UNREAD=$(python3 "$S/cdp-reader.py" eval "JSON.stringify(Object.entries(window.store.getState().messages.unreadCounts||{}).filter(([k,v])=>k.startsWith('buddy-')&&v>0).map(([k])=>k.replace('buddy-','')))" 2>/dev/null \
  | python3 -c "import json,sys; known=set('$KNOWN'.split()); [print(x) for x in json.loads(sys.stdin.read().strip()) if x not in known]" 2>/dev/null)
for B in $UNREAD; do
    python3 "$S/cdp-reader.py" switch-buddy --buddy "$B" 2>/dev/null && python3 "$S/cdp-reader.py" read-buddy --buddy "$B" 2>/dev/null > "/tmp/st-buddies/$B.json"
done

# Groups with unread messages that might tag Nicholas (all groups except monitored)
mkdir -p /tmp/st-tagged-groups
MONITORED_GROUPS="3259178 3708121 787950"
UNREAD_GROUPS=$(python3 "$S/cdp-reader.py" eval "JSON.stringify(Object.entries(window.store.getState().messages.unreadCounts||{}).filter(([k,v])=>k.startsWith('group-')&&v>0).map(([k])=>k.replace('group-','')))" 2>/dev/null \
  | python3 -c "import json,sys; known=set('$MONITORED_GROUPS'.split()); [print(x) for x in json.loads(sys.stdin.read().strip()) if x not in known]" 2>/dev/null)
for G in $UNREAD_GROUPS; do
    python3 "$S/cdp-reader.py" switch-chat --group "$G" 2>/dev/null && python3 "$S/cdp-reader.py" read --group "$G" 2>/dev/null > "/tmp/st-tagged-groups/$G.json"
done
```

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
    """Find messages that tag Nicholas and return windowed context."""
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
                e['tagged'] = True
            thread_id = m.get('threadId') or m.get('replyToThread') or m.get('threadRootId')
            if thread_id:
                e['threadId'] = str(thread_id)
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

Replace `LAST_TS` with the actual `last_briefing_ts` value. The output is the **only data to analyze** — compact JSON, all sources merged, already filtered.

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
**Can Wait**
• [{source}] {Name}: {1-line summary}
(omit section if nothing)
```

Rules: lead with most important; deduplicate cross-source topics; skip images/files with no caption; SGT = UTC+8.

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
