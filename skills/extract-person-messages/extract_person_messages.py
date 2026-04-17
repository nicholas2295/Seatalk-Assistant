#!/usr/bin/env python3
"""Extract all SeaTalk messages involving a target person over a time window.

Collects:
  - Full 1:1 private chat with the target person
  - All messages in groups where the target is a member (full context)
  - Thread replies in those groups (from Redux + mdb)

Optionally pre-loads history by switching to each chat and scrolling up
to maximize the amount of cached data available for extraction.

Requires SeaTalk with CDP (same as cdp-reader.py).

Examples:
  python3 extract_person_messages.py --target-id 15757 --last-days 30
  python3 extract_person_messages.py --target-id 15757 --last-days 30 --scroll-steps 120
  python3 extract_person_messages.py --target-id 15757 --since-date 2026-03-17 --until-date 2026-04-16
  python3 extract_person_messages.py --target-id 15757 --last-days 30 --skip-preload
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

import importlib.util

# cdp-reader.py lives in use-seatalk/scripts/ which may be elsewhere in the repo.
# _load_cdp_reader() walks up from this script's directory to find it, or
# accepts an explicit override via --use-seatalk-dir.
_CDP_READER_OVERRIDE: Optional[str] = None  # set by main() before _load_cdp_reader()

def _find_cdp_reader(override_dir: Optional[str] = None) -> str:
    """Locate use-seatalk/scripts/cdp-reader.py by walking up from this file."""
    if override_dir:
        p = os.path.join(override_dir, "scripts", "cdp-reader.py")
        if os.path.isfile(p):
            return p
        raise FileNotFoundError(f"cdp-reader.py not found at {p}")
    d = _SCRIPT_DIR
    for _ in range(6):
        candidate = os.path.join(d, "use-seatalk", "scripts", "cdp-reader.py")
        if os.path.isfile(candidate):
            return candidate
        # Also check if we're already inside use-seatalk/scripts/
        candidate2 = os.path.join(d, "cdp-reader.py")
        if os.path.isfile(candidate2):
            return candidate2
        d = os.path.dirname(d)
    raise FileNotFoundError(
        "Cannot find use-seatalk/scripts/cdp-reader.py. "
        "Pass --use-seatalk-dir or place this script inside the Seatalk Bridge repo."
    )

def _load_cdp_reader():
    cdp_path = _find_cdp_reader(_CDP_READER_OVERRIDE)
    spec = importlib.util.spec_from_file_location("cdp_reader", cdp_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

_mod: Any = None  # loaded lazily on first use

def _ensure_cdp():
    global _mod
    if _mod is None:
        _mod = _load_cdp_reader()
    return _mod

def _get_js(name: str) -> str:
    return getattr(_ensure_cdp(), name)


# ── Time window helpers (same as redux_related_messages.py) ────────

def _local_range_last_days(n: float) -> Tuple[int, int]:
    t1 = int(datetime.now().timestamp())
    t0 = t1 - int(n * 86400)
    return t0, t1


def _local_range_last_hours(n: float) -> Tuple[int, int]:
    t1 = int(datetime.now().timestamp())
    t0 = t1 - int(n * 3600)
    return t0, t1


def _local_range_dates(since_date: str, until_date: str) -> Tuple[int, int]:
    d0 = datetime.strptime(since_date, "%Y-%m-%d")
    d1 = datetime.strptime(until_date, "%Y-%m-%d")
    if d1 < d0:
        raise ValueError("--until-date must be >= --since-date")
    t0 = int(d0.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    end = d1 + timedelta(days=1)
    t1 = int(end.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()) - 1
    return t0, t1


# ── CDP helpers ────────────────────────────────────────────────────

def _try_switch_group(s, group_id: int) -> bool:
    """Switch UI to group chat. Returns True on success."""
    cur = s.evaluate("window.store.getState().messages.selectedSession")
    if isinstance(cur, dict) and cur.get("id") == group_id and cur.get("type") == "group":
        return True
    sw = s.evaluate(_get_js("SWITCH_CHAT_JS") % str(group_id))
    if isinstance(sw, dict) and sw.get("__need_search__"):
        gi = s.evaluate(
            f"(function(){{ var g = window.store.getState().contact.groupInfo[{group_id}]; "
            f"return g ? g.name : null; }})()"
        )
        search_text = gi if gi else str(group_id)
        r = s.evaluate(_get_js("SWITCH_CHAT_SEARCH_JS") % json.dumps(search_text))
        if isinstance(r, dict) and r.get("__error__"):
            return False
        time.sleep(1.5)
        sw = s.evaluate(_get_js("SWITCH_CHAT_CLICK_RESULT_JS") % str(group_id))
    if isinstance(sw, dict) and "__error__" in sw:
        return False
    time.sleep(0.8)
    return True


def _try_switch_buddy(s, buddy_id: int) -> bool:
    """Switch UI to private chat. Returns True on success."""
    cur = s.evaluate("window.store.getState().messages.selectedSession")
    if isinstance(cur, dict) and cur.get("id") == buddy_id and cur.get("type") == "buddy":
        return True
    result = s.evaluate(_get_js("SWITCH_BUDDY_JS") % str(buddy_id))
    if isinstance(result, dict) and result.get("__need_search__"):
        ui = s.evaluate(
            f"(function(){{ var u = window.store.getState().contact.userInfo[{buddy_id}]; "
            f"return u ? (u.name || u.nickname) : null; }})()"
        )
        search_text = ui if ui else str(buddy_id)
        r = s.evaluate(_get_js("SWITCH_CHAT_SEARCH_JS") % json.dumps(search_text))
        if not (isinstance(r, dict) and r.get("__error__")):
            time.sleep(1.5)
            result = s.evaluate(_get_js("SWITCH_CHAT_CLICK_RESULT_JS") % str(buddy_id))
    if isinstance(result, dict) and "__error__" in result:
        return False
    time.sleep(0.8)
    return True


def _scroll_up(s, steps: int) -> int:
    """Scroll the message list up N steps to load older messages. Returns steps that moved."""
    ini = s.evaluate(_get_js("FIND_MESSAGE_SCROLL_CONTAINER_JS"))
    if not (isinstance(ini, dict) and ini.get("ok")):
        return 0
    moved_count = 0
    stuck = 0
    for _ in range(steps):
        r = s.evaluate(_get_js("SCROLL_MESSAGE_LIST_STEP_JS") % json.dumps("older"))
        time.sleep(0.15)
        if isinstance(r, dict) and not r.get("moved"):
            stuck += 1
            if stuck >= 5:
                break
        else:
            stuck = 0
            moved_count += 1
    return moved_count


# ── Group membership discovery JS ─────────────────────────────────

FIND_SHARED_GROUPS_JS = r"""
(function(targetId) {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var st = store.getState();
  var gi = (st.contact && st.contact.groupInfo) || {};
  var gm = (st.contact && st.contact.groupMembers) || {};
  var msgs = (st.messages && st.messages.messages) || {};

  var result = [];
  var membershipGroups = {};
  var senderGroups = {};

  // Method 1: Check groupMembers for target ID
  var gids = Object.keys(gi);
  for (var i = 0; i < gids.length; i++) {
    var gid = parseInt(gids[i], 10);
    var members = gm[gid];
    if (!members || !members.length) continue;
    for (var j = 0; j < members.length; j++) {
      if (String(members[j].id) === String(targetId)) {
        membershipGroups[gid] = true;
        break;
      }
    }
  }

  // Method 2: Scan messages for target sender (fallback for groups not in groupMembers)
  for (var k in msgs) {
    if (!Object.prototype.hasOwnProperty.call(msgs, k)) continue;
    if (k.indexOf('group-') !== 0) continue;
    var m = msgs[k];
    if (!m || typeof m !== 'object') continue;
    if (String(m.senderId) === String(targetId)) {
      var parts = k.split('-');
      if (parts.length >= 3) {
        var sgid = parseInt(parts[1], 10);
        if (!isNaN(sgid)) senderGroups[sgid] = true;
      }
    }
  }

  // Merge both methods
  var allGids = {};
  for (var mg in membershipGroups) allGids[mg] = 'member';
  for (var sg in senderGroups) {
    if (!allGids[sg]) allGids[sg] = 'sender';
  }

  for (var ag in allGids) {
    var g = gi[ag];
    result.push({
      groupId: parseInt(ag, 10),
      groupName: g ? (g.name || String(ag)) : String(ag),
      source: allGids[ag]
    });
  }

  // Also resolve target name
  var ui = (st.contact && st.contact.userInfo) || {};
  var tu = ui[targetId];
  var targetName = tu ? (tu.name || tu.nickname || String(targetId)) : String(targetId);

  return {targetName: targetName, sharedGroups: result};
})(%s)
"""


# ── Main extraction JS ─────────────────────────────────────────────

def build_extraction_js(target_id: int, shared_group_ids: List[int],
                        t0: int, t1: int, max_text: int) -> Tuple[str, str]:
    """Build JS that extracts all messages for the target person's conversations."""
    result_key = f"__extractPerson_{target_id}_{t0}__"
    group_set_js = json.dumps(shared_group_ids)
    js = r"""
(function() {
  var TARGET_ID = %d;
  var SHARED_GIDS_ARR = %s;
  var T0 = %d;
  var T1 = %d;
  var MAX_TEXT = %d;
  var RESULT_KEY = %s;

  var SHARED_GIDS = {};
  for (var gi_idx = 0; gi_idx < SHARED_GIDS_ARR.length; gi_idx++) {
    SHARED_GIDS[SHARED_GIDS_ARR[gi_idx]] = true;
  }

  var store = window.store;
  if (!store || typeof store.getState !== 'function') {
    window[RESULT_KEY] = { error: 'no store' };
    return 'scheduled';
  }
  var st = store.getState();
  var msgs = st.messages && st.messages.messages;
  if (!msgs) {
    window[RESULT_KEY] = { error: 'no messages.messages' };
    return 'scheduled';
  }
  var gi = (st.contact && st.contact.groupInfo) || {};
  var ui = (st.contact && st.contact.userInfo) || {};

  function groupName(gid) {
    var g = gi[gid];
    return g ? (g.name || String(gid)) : String(gid);
  }
  function buddyName(bid) {
    var u = ui[bid];
    return u ? (u.name || u.nickname || String(bid)) : String(bid);
  }
  function senderName(id) {
    if (id == null) return '';
    var u = ui[id];
    return u ? (u.name || u.nickname || String(id)) : String(id);
  }
  function extractText(m) {
    var c = m.content;
    if (!c) return '';
    if (typeof c === 'object' && c.text) return String(c.text);
    if (typeof c === 'string') {
      try {
        var p = JSON.parse(c);
        if (p && p.text) return String(p.text);
      } catch (e) {}
      return c;
    }
    return '';
  }
  function msgTs(m) {
    var fields = [m.timeStamp, m.ts, m.message_sent_time, m.createTime, m.serverTime, m.sentTime];
    var ts = 0;
    for (var fi = 0; fi < fields.length; fi++) {
      var t = fields[fi];
      if (t == null || t === '') continue;
      var n = Number(t);
      if (!isFinite(n) || n <= 0) continue;
      ts = n;
      break;
    }
    if (ts <= 0) return 0;
    return ts > 1e12 ? Math.floor(ts / 1000) : Math.floor(ts);
  }

  // ── Phase 1: Collect all Redux messages + discover threadIds ──
  var parsed = [];
  var parsedKeys = {};
  var threadIds = {};

  for (var k in msgs) {
    if (!Object.prototype.hasOwnProperty.call(msgs, k)) continue;
    var m = msgs[k];
    if (!m || typeof m !== 'object') continue;
    var ts = msgTs(m);
    var sid = m.senderId;
    var text = extractText(m).replace(/\r/g, '\n');
    var tag = m.tag || '';

    if (k.indexOf('group-') === 0) {
      var parts = k.split('-');
      if (parts.length < 3) continue;
      var gid = parseInt(parts[1], 10);

      // Only include groups where target is a member
      if (!SHARED_GIDS[gid]) continue;

      var inThread = parts.length > 3;
      var threadId = inThread ? parts.slice(0, parts.length - 1).join('-') : '';

      // Discover threads
      if (!inThread && m.threadInfo && m.threadInfo.replyCount > 0) {
        var tid = 'group-' + gid + '-' + (m.mid || m.id || parts[2]);
        threadIds[tid] = gid;
      }
      if (inThread && threadId) {
        threadIds[threadId] = gid;
      }

      if (ts < T0 || ts > T1) continue;
      parsedKeys[k] = 1;
      parsed.push({
        key: k, ts: ts, senderId: sid, senderName: senderName(sid), tag: tag, text: text,
        kind: 'group', groupId: gid, sessionName: groupName(gid),
        threadId: inThread ? threadId : '', inThread: inThread
      });

    } else if (k.indexOf('buddy-') === 0) {
      var p2 = k.split('-');
      if (p2.length < 3) continue;
      var bid = parseInt(p2[1], 10);

      // Only include the buddy chat with the target
      if (bid !== TARGET_ID) continue;

      if (ts < T0 || ts > T1) continue;
      var inTh = p2.length > 3;
      var tId = inTh ? p2.slice(0, p2.length - 1).join('-') : '';
      parsedKeys[k] = 1;
      parsed.push({
        key: k, ts: ts, senderId: sid, senderName: senderName(sid), tag: tag, text: text,
        kind: 'buddy', buddyId: bid, sessionName: buddyName(bid),
        threadId: inTh ? tId : '', inThread: inTh
      });
    }
  }

  // Source C: Redux messages.lists (hydrated thread panels)
  var listMap = st.messages && st.messages.lists;
  if (listMap) {
    for (var listKey in listMap) {
      if (!Object.prototype.hasOwnProperty.call(listMap, listKey)) continue;
      if (listKey.indexOf('group-') !== 0) continue;
      var segs = listKey.split('-');
      if (segs.length < 3) continue;
      var gHydr = parseInt(segs[1], 10);
      if (isNaN(gHydr) || !SHARED_GIDS[gHydr]) continue;
      threadIds[listKey] = gHydr;
      var mids = listMap[listKey];
      if (!mids || !mids.length) continue;
      for (var hi = 0; hi < mids.length; hi++) {
        var fkh = listKey + '-' + mids[hi];
        if (parsedKeys[fkh]) continue;
        var mh = msgs[fkh];
        if (!mh || typeof mh !== 'object') continue;
        var tsh = msgTs(mh);
        if (tsh < T0 || tsh > T1) continue;
        var sidh = mh.senderId;
        var texth = extractText(mh).replace(/\r/g, '\n');
        var tagh = mh.tag || '';
        parsedKeys[fkh] = 1;
        parsed.push({
          key: fkh, ts: tsh, senderId: sidh, senderName: senderName(sidh), tag: tagh, text: texth,
          kind: 'group', groupId: gHydr, sessionName: groupName(gHydr),
          threadId: listKey, inThread: true
        });
      }
    }
  }

  // ── Phase 2: mdb thread enrichment ──
  function finalize(mdbRows) {
    for (var mi = 0; mi < mdbRows.length; mi++) {
      var batch = mdbRows[mi];
      if (!batch.rows || !batch.rows.length) continue;
      var bGid = batch.groupId;
      if (!SHARED_GIDS[bGid]) continue;
      for (var ri = 0; ri < batch.rows.length; ri++) {
        var row = batch.rows[ri];
        if (!row || typeof row !== 'object') continue;
        var rmid = row.mid || row.id || '';
        if (!rmid) continue;
        var rkey = batch.threadId + '-' + rmid;
        if (parsedKeys[rkey]) continue;
        var rts = msgTs(row);
        if (rts < T0 || rts > T1) continue;
        var rsid = row.senderId;
        parsedKeys[rkey] = 1;
        parsed.push({
          key: rkey, ts: rts, senderId: rsid, senderName: senderName(rsid),
          tag: row.tag || '', text: extractText(row).replace(/\r/g, '\n'),
          kind: 'group', groupId: bGid, sessionName: groupName(bGid),
          threadId: batch.threadId, inThread: true
        });
      }
    }

    // ── Phase 3: Build output (no "related to me" filter — include everything) ──
    var out = [];
    for (var j = 0; j < parsed.length; j++) {
      var x = parsed[j];
      var textTrim = (x.text || '').replace(/\s+/g, ' ').trim();
      if (x.tag === 'image') {
        if (!textTrim) textTrim = '[image]';
      }
      if (textTrim.length > MAX_TEXT) textTrim = textTrim.slice(0, MAX_TEXT) + '\u2026';
      var row_out = {
        ts: x.ts,
        sender: x.senderName,
        senderId: x.senderId,
        text: textTrim,
        inThread: x.inThread || false,
        threadId: x.threadId || '',
        tag: x.tag
      };
      if (x.kind === 'group') {
        row_out.channel = 'group';
        row_out.groupId = x.groupId;
        row_out.groupName = x.sessionName;
      } else {
        row_out.channel = 'buddy';
        row_out.buddyId = x.buddyId;
        row_out.buddyName = x.sessionName;
      }
      out.push(row_out);
    }

    out.sort(function(a, b) { return a.ts - b.ts; });

    window[RESULT_KEY] = {
      targetId: TARGET_ID,
      window: { startUnixSec: T0, endUnixSec: T1 },
      sharedGroupIds: SHARED_GIDS_ARR,
      mdbThreadsQueried: Object.keys(threadIds).length,
      totalMessages: out.length,
      messages: out
    };
  }

  var mdb = window.mdb;
  if (!mdb || typeof mdb.getMessagesOfThread !== 'function') {
    finalize([]);
    return 'scheduled';
  }

  // Source D: mdb.getAllFollowedThreads()
  var followedPromise = (typeof mdb.getAllFollowedThreads === 'function')
    ? Promise.resolve(mdb.getAllFollowedThreads())
        .then(function(r) {
          var items = (r && r.items) || r || [];
          for (var fi = 0; fi < items.length; fi++) {
            var item = items[fi];
            var smid = item.sessionMid || '';
            if (!smid) continue;
            var sp = smid.split('-');
            if (sp.length < 3 || sp[0] !== 'group') continue;
            var fgid = parseInt(sp[1], 10);
            if (!isNaN(fgid) && SHARED_GIDS[fgid] && !threadIds[smid]) {
              threadIds[smid] = fgid;
            }
          }
        })
        .catch(function() {})
    : Promise.resolve();

  followedPromise.then(function() {
    var tidKeys = Object.keys(threadIds);
    if (tidKeys.length === 0) {
      finalize([]);
      return;
    }
    var promises = [];
    for (var ti = 0; ti < tidKeys.length; ti++) {
      (function(thId, gId) {
        promises.push(
          Promise.resolve(mdb.getMessagesOfThread(thId))
            .then(function(rows) { return { threadId: thId, groupId: gId, rows: rows || [] }; })
            .catch(function() { return { threadId: thId, groupId: gId, rows: [] }; })
        );
      })(tidKeys[ti], threadIds[tidKeys[ti]]);
    }
    Promise.all(promises)
      .then(function(results) { finalize(results); })
      .catch(function() { finalize([]); });
  });

  return 'scheduled';
})()
""" % (
        target_id,
        group_set_js,
        t0, t1,
        max_text,
        json.dumps(result_key),
    )
    return js, result_key


# ── Preload phase ──────────────────────────────────────────────────

def _reconnect_cdp():
    """Re-establish CDP connection. Returns new session or raises."""
    print("  Reconnecting CDP...", file=sys.stderr, end=" ", flush=True)
    s = _ensure_cdp().connect()
    print("OK", file=sys.stderr)
    return s


def preload_chats(s, target_id: int, shared_groups: List[Dict],
                  scroll_steps: int):
    """Switch to each relevant chat and scroll up to load older messages.
    Returns the (possibly reconnected) CDP session."""
    total = 1 + len(shared_groups)
    print(f"Pre-loading history for {total} chats ({scroll_steps} scroll steps each)...",
          file=sys.stderr)
    skipped = 0

    # Buddy chat first
    print(f"  [1/{total}] Buddy chat {target_id}...", file=sys.stderr, end=" ", flush=True)
    try:
        if _try_switch_buddy(s, target_id):
            moved = _scroll_up(s, scroll_steps)
            print(f"scrolled {moved}/{scroll_steps} steps", file=sys.stderr)
        else:
            print("SKIP (could not switch)", file=sys.stderr)
            skipped += 1
    except Exception as e:
        print(f"ERROR ({e})", file=sys.stderr)
        skipped += 1
        try:
            s.close()
        except Exception:
            pass
        s = _reconnect_cdp()
    time.sleep(1.0)

    # Shared groups
    for idx, grp in enumerate(shared_groups):
        gid = grp["groupId"]
        gname = grp.get("groupName", str(gid))
        label = f"  [{idx + 2}/{total}] Group \"{gname}\" ({gid})..."
        print(label, file=sys.stderr, end=" ", flush=True)
        try:
            if _try_switch_group(s, gid):
                moved = _scroll_up(s, scroll_steps)
                print(f"scrolled {moved}/{scroll_steps} steps", file=sys.stderr)
            else:
                print("SKIP (could not switch)", file=sys.stderr)
                skipped += 1
        except Exception as e:
            print(f"ERROR ({e})", file=sys.stderr)
            skipped += 1
            try:
                s.close()
            except Exception:
                pass
            s = _reconnect_cdp()
        time.sleep(1.5)

    print(f"Pre-load complete. {skipped}/{total} chats skipped.", file=sys.stderr)
    return s


# ── Output formatting ──────────────────────────────────────────────

def _ts_to_datetime(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _ts_to_date(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def group_messages_by_conversation(raw: Dict[str, Any], target_name: str,
                                   self_id: Optional[int]) -> Dict[str, Any]:
    """Transform flat message list into conversations grouped by chat."""
    messages = raw.get("messages", [])
    convos: Dict[str, Dict[str, Any]] = {}

    for msg in messages:
        ch = msg.get("channel", "")
        if ch == "buddy":
            key = f"buddy-{msg.get('buddyId', 0)}"
            if key not in convos:
                convos[key] = {
                    "type": "buddy",
                    "name": f"{msg.get('buddyName', target_name)} (1:1)",
                    "id": msg.get("buddyId", 0),
                    "messages": [],
                }
        elif ch == "group":
            key = f"group-{msg.get('groupId', 0)}"
            if key not in convos:
                convos[key] = {
                    "type": "group",
                    "name": msg.get("groupName", "Unknown Group"),
                    "id": msg.get("groupId", 0),
                    "messages": [],
                }
        else:
            continue

        row = {
            "ts": msg["ts"],
            "datetime": _ts_to_datetime(msg["ts"]),
            "sender": msg.get("sender", ""),
            "senderId": msg.get("senderId"),
            "text": msg.get("text", ""),
            "inThread": msg.get("inThread", False),
            "threadId": msg.get("threadId", ""),
            "tag": msg.get("tag", ""),
        }
        if self_id is not None:
            row["fromSelf"] = str(msg.get("senderId")) == str(self_id)
        convos[key]["messages"].append(row)

    # Sort messages within each conversation and compute coverage
    convo_list = []
    for ckey in sorted(convos.keys()):
        c = convos[ckey]
        c["messages"].sort(key=lambda m: m["ts"])
        msgs_list = c["messages"]
        if msgs_list:
            c["coverage"] = {
                "earliest": msgs_list[0]["ts"],
                "earliestDate": _ts_to_date(msgs_list[0]["ts"]),
                "latest": msgs_list[-1]["ts"],
                "latestDate": _ts_to_date(msgs_list[-1]["ts"]),
            }
        else:
            c["coverage"] = {"earliest": 0, "earliestDate": "", "latest": 0, "latestDate": ""}
        c["messageCount"] = len(msgs_list)
        convo_list.append(c)

    # Sort: buddy first, then groups by name
    convo_list.sort(key=lambda c: (0 if c["type"] == "buddy" else 1, c["name"]))

    return {
        "targetId": raw.get("targetId"),
        "targetName": target_name,
        "window": raw.get("window", {}),
        "windowDates": {
            "start": _ts_to_date(raw.get("window", {}).get("startUnixSec", 0)),
            "end": _ts_to_date(raw.get("window", {}).get("endUnixSec", 0)),
        },
        "mdbThreadsQueried": raw.get("mdbThreadsQueried", 0),
        "totalMessages": raw.get("totalMessages", 0),
        "conversationCount": len(convo_list),
        "conversations": convo_list,
    }


def write_transcript(data: Dict[str, Any], path: str) -> None:
    """Write human-readable transcript file."""
    lines: List[str] = []
    lines.append(f"SeaTalk Message Extract: {data['targetName']}")
    lines.append(f"Window: {data['windowDates']['start']} to {data['windowDates']['end']}")
    lines.append(f"Total messages: {data['totalMessages']} across {data['conversationCount']} conversations")
    lines.append(f"Threads queried (mdb): {data['mdbThreadsQueried']}")
    lines.append("")

    for convo in data["conversations"]:
        cov = convo["coverage"]
        if convo["type"] == "buddy":
            lines.append(f"{'=' * 60}")
            lines.append(f"1:1 Chat: {convo['name']}")
        else:
            lines.append(f"{'=' * 60}")
            lines.append(f"Group: {convo['name']} (ID: {convo['id']})")
        lines.append(
            f"Coverage: {cov.get('earliestDate', '?')} to {cov.get('latestDate', '?')} "
            f"({convo['messageCount']} messages)"
        )
        lines.append(f"{'=' * 60}")
        lines.append("")

        for msg in convo["messages"]:
            prefix = "  [Thread] " if msg.get("inThread") else ""
            tag_label = ""
            if msg.get("tag") and msg["tag"] not in ("text", ""):
                tag_label = f" [{msg['tag']}]"
            text = msg.get("text", "")
            lines.append(f"{prefix}[{msg['datetime']}] {msg['sender']}{tag_label}: {text}")

        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── Main ───────────────────────────────────────────────────────────

def run(target_id: int, t0: int, t1: int, max_text: int,
        scroll_steps: int, skip_preload: bool,
        self_id: Optional[int], output_dir: str) -> None:

    s = _ensure_cdp().connect()

    # Step 1: Discover shared groups
    print("Discovering shared groups...", file=sys.stderr)
    discovery = s.evaluate(FIND_SHARED_GROUPS_JS % str(target_id))
    if isinstance(discovery, dict) and "__error__" in discovery:
        print(f"ERROR: {discovery['__error__']}", file=sys.stderr)
        s.close()
        sys.exit(1)

    target_name = discovery.get("targetName", str(target_id))
    shared_groups = discovery.get("sharedGroups", [])
    print(f"Target: {target_name} (ID: {target_id})", file=sys.stderr)
    print(f"Found {len(shared_groups)} shared group(s):", file=sys.stderr)
    for grp in shared_groups:
        print(f"  - {grp['groupName']} ({grp['groupId']}) [{grp['source']}]", file=sys.stderr)

    shared_group_ids = [g["groupId"] for g in shared_groups]

    # Step 2: Pre-load history
    if not skip_preload and scroll_steps > 0:
        s = preload_chats(s, target_id, shared_groups, scroll_steps)
        time.sleep(2.0)
    else:
        print("Skipping pre-load (--skip-preload or --scroll-steps 0).", file=sys.stderr)

    # Step 3: Extract messages
    print("Extracting messages from Redux + mdb...", file=sys.stderr)
    js_code, result_key = build_extraction_js(target_id, shared_group_ids, t0, t1, max_text)
    s.evaluate(js_code, await_promise=False)

    poll_js = f"window[{json.dumps(result_key)}]"
    result = None
    for _ in range(90):
        time.sleep(0.5)
        val = s.evaluate(poll_js, await_promise=False)
        if val is not None:
            result = val
            break

    s.evaluate(f"delete window[{json.dumps(result_key)}]", await_promise=False)
    s.close()

    if result is None:
        print("ERROR: Timeout waiting for extraction to complete.", file=sys.stderr)
        sys.exit(1)
    if "error" in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)

    total = result.get("totalMessages", 0)
    print(f"Extracted {total} messages.", file=sys.stderr)

    # Step 4: Format and write output
    grouped = group_messages_by_conversation(result, target_name, self_id)

    os.makedirs(output_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = target_name.lower().replace(" ", "_")

    json_path = os.path.join(output_dir, f"{safe_name}_messages_{date_str}.json")
    txt_path = os.path.join(output_dir, f"{safe_name}_messages_{date_str}.txt")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(grouped, f, indent=2, ensure_ascii=False)
    print(f"JSON written to: {json_path}", file=sys.stderr)

    write_transcript(grouped, txt_path)
    print(f"Transcript written to: {txt_path}", file=sys.stderr)

    # Print summary
    print(f"\n--- Summary ---", file=sys.stderr)
    print(f"Target: {target_name} (ID: {target_id})", file=sys.stderr)
    print(f"Window: {grouped['windowDates']['start']} to {grouped['windowDates']['end']}",
          file=sys.stderr)
    print(f"Total: {grouped['totalMessages']} messages in {grouped['conversationCount']} conversations",
          file=sys.stderr)
    for convo in grouped["conversations"]:
        cov = convo["coverage"]
        ctype = "1:1" if convo["type"] == "buddy" else "Group"
        print(
            f"  {ctype}: {convo['name']} — {convo['messageCount']} msgs "
            f"({cov.get('earliestDate', '?')} to {cov.get('latestDate', '?')})",
            file=sys.stderr,
        )


def main() -> None:
    p = argparse.ArgumentParser(
        description="Extract all SeaTalk messages involving a target person."
    )
    p.add_argument("--target-id", type=int, required=True,
                   help="SeaTalk numeric user ID of the target person.")
    p.add_argument("--self-id", type=int, default=None,
                   help="Your own SeaTalk user ID (or set SEATALK_SELF_ID).")
    p.add_argument("--max-text", type=int, default=5000,
                   help="Max chars per message text in output (default: 5000).")
    p.add_argument("--scroll-steps", type=int, default=80,
                   help="Scroll steps per chat during pre-load (default: 80). "
                        "Higher = more history but slower.")
    p.add_argument("--skip-preload", action="store_true",
                   help="Skip the scroll pre-loading phase (use existing cache only).")
    p.add_argument("--output-dir", type=str, default="output",
                   help="Directory for output files (default: output/).")
    p.add_argument("--use-seatalk-dir", type=str, default=None,
                   help="Path to use-seatalk/ folder (auto-discovered if omitted).")

    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--last-days", type=float, metavar="N",
                   help="Last N days until now.")
    g.add_argument("--last-hours", type=float, metavar="N",
                   help="Last N hours until now.")
    g.add_argument("--since-date", type=str, metavar="YYYY-MM-DD",
                   help="Local date range start (inclusive). With --until-date.")
    g.add_argument("--since-unix", type=int, metavar="SEC",
                   help="Inclusive start (Unix sec). Use with --until-unix.")
    p.add_argument("--until-date", type=str, metavar="YYYY-MM-DD",
                   help="Local date range end (inclusive).")
    p.add_argument("--until-unix", type=int, default=None, metavar="SEC",
                   help="Inclusive end (Unix sec). Default: now.")

    args = p.parse_args()

    global _CDP_READER_OVERRIDE
    _CDP_READER_OVERRIDE = args.use_seatalk_dir

    self_id = args.self_id
    if self_id is None:
        raw = os.environ.get("SEATALK_SELF_ID", "").strip()
        if raw.isdigit():
            self_id = int(raw)

    if args.since_unix is not None:
        t0 = args.since_unix
        t1 = args.until_unix if args.until_unix is not None else int(datetime.now().timestamp())
    elif args.since_date is not None:
        if not args.until_date:
            p.error("--since-date requires --until-date")
        try:
            t0, t1 = _local_range_dates(args.since_date, args.until_date)
        except ValueError as e:
            p.error(str(e))
    elif args.last_days is not None:
        t0, t1 = _local_range_last_days(args.last_days)
    else:
        t0, t1 = _local_range_last_hours(args.last_hours)

    if t1 < t0:
        p.error("Time window is empty (end before start).")

    print(f"Time window: {_ts_to_datetime(t0)} to {_ts_to_datetime(t1)}", file=sys.stderr)

    run(
        target_id=args.target_id,
        t0=t0, t1=t1,
        max_text=args.max_text,
        scroll_steps=args.scroll_steps,
        skip_preload=args.skip_preload,
        self_id=self_id,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
