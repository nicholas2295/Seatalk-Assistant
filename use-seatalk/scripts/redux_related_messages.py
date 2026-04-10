#!/usr/bin/env python3
"""Query SeaTalk Desktop for messages *related to* a user in a time window.

Related = you sent it, @mention matches your display patterns, full buddy chat if you
posted there in-window, and full **group** chat (main channel + thread replies) if
you posted anywhere in that group in-window.

**Thread reply sources (two layers):**
1. Redux ``messages.messages`` / ``messages.lists`` — only while thread panel is open.
2. Local DB via ``window.mdb.getMessagesOfThread(threadId)`` — persists even after
   the thread drawer is closed (same source ``cdp-reader.py thread-messages`` uses).

The script first collects Redux messages, identifies group root messages that carry
``threadInfo`` (indicating they have replies), then batch-queries ``mdb`` for each
discovered threadId to pull replies from the local desktop database. This means
**thread replies are now included even if the thread panel is closed**, as long as
the desktop client has synced those messages at some point.

Requires SeaTalk with CDP (same as cdp-reader.py).

Examples:
  SEATALK_SELF_ID=47934 python3 scripts/redux_related_messages.py --today-local
  python3 scripts/redux_related_messages.py --user-id 47934 --last-days 7
  python3 scripts/redux_related_messages.py --user-id 47934 --since-unix 1774540800 --until-unix 1774617600
  python3 scripts/redux_related_messages.py --user-id 47934 --since-date 2026-03-20 --until-date 2026-03-27
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import importlib.util

_spec = importlib.util.spec_from_file_location("cdp_reader", os.path.join(_SCRIPT_DIR, "cdp-reader.py"))
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def _local_range_today() -> Tuple[int, int]:
    d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    t0 = int(d.timestamp())
    t1 = int(datetime.now().timestamp())
    return t0, t1


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


def build_js(my_id: int, t0: int, t1: int, max_text: int, display_name: str = "") -> str:
    result_key = f"__relatedMsgs_{my_id}_{t0}__"
    return (
        r"""
(function() {
  var MY_ID = %d;
  var T0 = %d;
  var T1 = %d;
  var MAX_TEXT = %d;
  var RESULT_KEY = %s;
  var DISPLAY_NAME = %s;

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

  function mentionsMe(text) {
    if (!text || typeof text !== 'string') return false;
    if (!DISPLAY_NAME) return false;
    var t = text;
    var escaped = DISPLAY_NAME.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (new RegExp(escaped, 'i').test(t)) return true;
    if (new RegExp('@\\s*' + escaped, 'i').test(t)) return true;
    return false;
  }

  // ── Phase 1: Collect all Redux messages + discover threadIds ──
  var parsed = [];
  var parsedKeys = {};
  var threadIds = {};  // threadId -> groupId

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
      var inThread = parts.length > 3;
      var threadId = inThread ? parts.slice(0, parts.length - 1).join('-') : '';

      // Source A: root messages with threadInfo
      if (!inThread && m.threadInfo && m.threadInfo.replyCount > 0) {
        var tid = 'group-' + gid + '-' + (m.mid || m.id || parts[2]);
        threadIds[tid] = gid;
      }
      // Source B: reply keys imply a threadId
      if (inThread && threadId) {
        threadIds[threadId] = gid;
      }

      if (ts < T0 || ts > T1) continue;
      parsedKeys[k] = 1;
      parsed.push({
        key: k, ts: ts, senderId: sid, senderName: senderName(sid), tag: tag, text: text,
        kind: 'group', groupId: gid, sessionName: groupName(gid) + (inThread ? ' [Thread]' : ''),
        threadId: inThread ? threadId : '', inThread: inThread
      });
    } else if (k.indexOf('buddy-') === 0) {
      if (ts < T0 || ts > T1) continue;
      var p2 = k.split('-');
      if (p2.length < 3) continue;
      var bid = parseInt(p2[1], 10);
      var inTh = p2.length > 3;
      var tId = inTh ? p2.slice(0, p2.length - 1).join('-') : '';
      parsedKeys[k] = 1;
      parsed.push({
        key: k, ts: ts, senderId: sid, senderName: senderName(sid), tag: tag, text: text,
        kind: 'buddy', buddyId: bid, sessionName: buddyName(bid) + (inTh ? ' [Thread]' : ''),
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
      if (isNaN(gHydr)) continue;
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
          kind: 'group', groupId: gHydr, sessionName: groupName(gHydr) + ' [Thread]',
          threadId: listKey, inThread: true
        });
      }
    }
  }

  // ── Phase 2: Discover more threads via mdb.getAllFollowedThreads(), then batch-query ──
  function finalize(mdbRows) {
    for (var mi = 0; mi < mdbRows.length; mi++) {
      var batch = mdbRows[mi];
      if (!batch.rows || !batch.rows.length) continue;
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
          kind: 'group', groupId: batch.groupId,
          sessionName: groupName(batch.groupId) + ' [Thread]',
          threadId: batch.threadId, inThread: true
        });
      }
    }

    // ── Phase 3: Filter by "related to me" ──
    var activeBuddy = {};
    var activeGroup = {};
    for (var i = 0; i < parsed.length; i++) {
      var r = parsed[i];
      if (String(r.senderId) !== String(MY_ID)) continue;
      if (r.kind === 'buddy') activeBuddy[r.buddyId] = true;
      if (r.kind === 'group') activeGroup[r.groupId] = true;
    }

    var out = [];
    for (var j = 0; j < parsed.length; j++) {
      var x = parsed[j];
      var im = String(x.senderId) === String(MY_ID);
      var men = mentionsMe(x.text);
      var include = im || men;
      if (x.kind === 'buddy' && activeBuddy[x.buddyId]) include = true;
      if (x.kind === 'group' && activeGroup[x.groupId]) include = true;
      if (!include) continue;
      var textTrim = (x.text || '').replace(/\s+/g, ' ').trim();
      if (x.tag === 'image') {
        if (!textTrim) textTrim = '[图片]';
      }
      if (textTrim.length > MAX_TEXT) textTrim = textTrim.slice(0, MAX_TEXT) + '…';
      out.push({
        ts: x.ts,
        session: x.sessionName,
        channel: x.kind,
        sender: x.senderName,
        fromSelf: im,
        text: textTrim,
        inThread: x.inThread || false,
        threadId: x.threadId || ''
      });
    }

    out.sort(function(a, b) { return a.ts - b.ts; });

    window[RESULT_KEY] = {
      selfId: MY_ID,
      window: { startUnixSec: T0, endUnixSec: T1 },
      mdbThreadsQueried: Object.keys(threadIds).length,
      includedRows: out.length,
      messages: out
    };
  }

  var mdb = window.mdb;
  if (!mdb || typeof mdb.getMessagesOfThread !== 'function') {
    finalize([]);
    return 'scheduled';
  }

  // Source D: mdb.getAllFollowedThreads() — threads the user follows, persisted in local DB
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
            if (!isNaN(fgid) && !threadIds[smid]) {
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
"""
        % (
            my_id,
            t0,
            t1,
            max_text,
            json.dumps(result_key),
            json.dumps(display_name),
        ),
        result_key,
    )


def run_query(my_id: int, t0: int, t1: int, max_text: int, display_name: str = "") -> Dict[str, Any]:
    import time

    js_code, result_key = build_js(my_id, t0, t1, max_text, display_name)
    s = _mod.connect()
    try:
        s.evaluate(js_code, await_promise=False)
        poll_js = f"window[{json.dumps(result_key)}]"
        for _ in range(60):
            time.sleep(0.5)
            val = s.evaluate(poll_js, await_promise=False)
            if val is not None:
                return val
        return {"error": "timeout waiting for mdb queries to complete"}
    finally:
        s.evaluate(f"delete window[{json.dumps(result_key)}]", await_promise=False)
        s.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Redux: messages related to a user in a time window.")
    p.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="SeaTalk numeric user id (or set SEATALK_SELF_ID).",
    )
    p.add_argument("--max-text", type=int, default=800, help="Max chars per message text in output.")

    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--today-local", action="store_true", help="Local calendar today 00:00 .. now.")
    g.add_argument("--last-days", type=float, metavar="N", help="Last N days until now.")
    g.add_argument("--last-hours", type=float, metavar="N", help="Last N hours until now.")
    g.add_argument(
        "--since-unix",
        type=int,
        metavar="SEC",
        help="Inclusive start (Unix sec). Use with --until-unix (default: now).",
    )
    p.add_argument(
        "--until-unix",
        type=int,
        default=None,
        metavar="SEC",
        help="Inclusive end (Unix sec). Default: now. Only with --since-unix.",
    )
    g.add_argument(
        "--since-date",
        type=str,
        metavar="YYYY-MM-DD",
        help="Local date range start (inclusive). With --until-date.",
    )

    p.add_argument("--until-date", type=str, metavar="YYYY-MM-DD", help="Local date range end (inclusive).")

    args = p.parse_args()

    uid = args.user_id
    if uid is None:
        raw = os.environ.get("SEATALK_SELF_ID", "").strip()
        if raw.isdigit():
            uid = int(raw)
    if uid is None:
        p.error("Pass --user-id or set SEATALK_SELF_ID")

    if args.until_unix is not None and args.since_unix is None:
        p.error("--until-unix requires --since-unix")

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
    elif args.today_local:
        t0, t1 = _local_range_today()
    elif args.last_days is not None:
        t0, t1 = _local_range_last_days(args.last_days)
    else:
        t0, t1 = _local_range_last_hours(args.last_hours)

    if t1 < t0:
        p.error("Time window is empty (end before start).")

    display_name = os.environ.get("SEATALK_DISPLAY_NAME", "").strip()
    result = run_query(uid, t0, t1, args.max_text, display_name)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
