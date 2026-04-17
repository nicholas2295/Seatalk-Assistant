#!/usr/bin/env python3
"""Lightweight CDP client for SeaTalk Electron app — read messages and threads.

Connects to SeaTalk's Chromium renderer via Chrome DevTools Protocol
to read messages from the in-memory Redux store.

Prerequisites:
  SeaTalk must be running with --remote-debugging-port=19222
  pkill -f SeaTalk.app; sleep 2
  open -a SeaTalk --args --remote-debugging-port=19222 --remote-allow-origins=*

Usage:
  python3 cdp-reader.py targets                         # list CDP targets
  python3 cdp-reader.py explore                          # discover store structure
  python3 cdp-reader.py eval "expression"                # run arbitrary JS
  python3 cdp-reader.py read [--group GROUP_ID]          # read cached group messages
  python3 cdp-reader.py read-buddy --buddy BUDDY_ID     # read cached private chat messages
  python3 cdp-reader.py listen [--group GROUP_ID]        # stream new messages (JSON-lines)
  python3 cdp-reader.py threads --group GROUP_ID         # list threads in a group
  python3 cdp-reader.py thread-messages --thread THREAD_ID  # read thread messages

  python3 cdp-reader.py current-chat                    # read messages from current chat (group or private)
  python3 cdp-reader.py current-group                   # read messages from current group
  python3 cdp-reader.py current-thread                  # read messages from current thread

  python3 cdp-reader.py unread --groups 499098,1743938   # query unread counts
  python3 cdp-reader.py mark-all-read                    # open all chats with unread (clears badges)
  python3 cdp-reader.py mark-threads-read --group GID [--scroll-steps 80] [--dwell-ms 400] [--sync-timeout-sec 25] [--only-unread]  # until threadInfo unread=0 in Redux, then close
  python3 cdp-reader.py probe-thread-read [--group GID]       # dump Redux thread/unread shapes (debug)
  python3 cdp-reader.py read-unread --group 1743938      # read messages after ack cursor
  python3 cdp-reader.py list-groups --filter "Spex"      # list joined groups
  python3 cdp-reader.py switch-chat --group 499098       # switch UI to group
  python3 cdp-reader.py switch-buddy --buddy 205031      # switch UI to private chat
  python3 cdp-reader.py open-thread --group 499098 --message MID  # open thread panel
  python3 cdp-reader.py close-thread                     # close thread panel
  python3 cdp-reader.py reply-thread --group GID --message MID "text"  # reply in thread
"""

from __future__ import annotations

import json
import os
import random
import re
import signal
import sys
import time
import http.client
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

try:
    import websocket  # websocket-client
except ImportError:
    print("ERROR: pip3 install websocket-client", file=sys.stderr)
    sys.exit(1)

CDP_HOST = os.environ.get("SEATALK_CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("SEATALK_CDP_PORT", "19222"))
DEFAULT_POLL_SEC = float(os.environ.get("SEATALK_CDP_POLL", "2"))
AUTO_FILE_THRESHOLD = 50
ALLOW_SEND = os.environ.get("SEATALK_ALLOW_SEND", "false").lower() in ("true", "1", "yes")
ADMIN_IDS: List[int] = []
_raw = os.environ.get("SEATALK_ADMIN_IDS", "").strip()
if _raw:
    ADMIN_IDS = [int(x) for x in _raw.split(",") if x.strip().isdigit()]


def _sender_allowed(sender_id, admin_set: Optional[set]) -> bool:
    """True if admin filter passes. Normalizes senderId — CDP may return int or str."""
    if not admin_set:
        return True
    if sender_id is None:
        return False
    try:
        n = int(sender_id)
    except (TypeError, ValueError):
        return False
    return n in admin_set


def _timestamp_to_unix_seconds(ts: Any) -> float:
    """Normalize Redux/API timestamps to Unix seconds (float).

    SeaTalk may expose seconds or milliseconds; comparisons vs time.time() must use seconds.
    """
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return 0.0
    if t <= 0:
        return 0.0
    # Milliseconds since epoch (e.g. 1.77e12) vs seconds (~1.77e9)
    if t > 1e12:
        return t / 1000.0
    return t


# ── CDP session ────────────────────────────────────────────────────


class CDPSession:
    def __init__(self, ws_url: str, timeout: int = 30):
        self.ws = websocket.create_connection(ws_url, timeout=timeout)
        self._seq = 0

    def call(self, method: str, params: Optional[dict] = None) -> dict:
        self._seq += 1
        msg: dict = {"id": self._seq, "method": method}
        if params:
            msg["params"] = params
        try:
            self.ws.send(json.dumps(msg))
            while True:
                raw = self.ws.recv()
                resp = json.loads(raw)
                if resp.get("id") == self._seq:
                    return resp
        except (ConnectionError, OSError, websocket.WebSocketException) as e:
            raise ConnectionError(f"CDP call failed: {e}") from e

    def evaluate(self, expression: str, await_promise: bool = False) -> Any:
        resp = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        result = resp.get("result", {}).get("result", {})
        exc = resp.get("result", {}).get("exceptionDetails")
        if exc:
            text = exc.get("text", "")
            inner = exc.get("exception", {}).get("description", "")
            return {"__error__": f"{text}: {inner}"}
        if result.get("subtype") == "error":
            return {"__error__": result.get("description", "")}
        if result.get("type") == "undefined":
            return None
        return result.get("value", result)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# ── HTTP helpers ───────────────────────────────────────────────────


def get_targets() -> List[Dict]:
    conn = http.client.HTTPConnection(CDP_HOST, CDP_PORT, timeout=5)
    conn.request("GET", "/json")
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    return data


def find_main_target(targets: List[Dict]) -> Optional[Dict]:
    for t in targets:
        if t.get("type") != "page":
            continue
        url = t.get("url", "")
        title = t.get("title", "").lower()
        if "index.html" in url or "seatalk" in title:
            return t
    pages = [t for t in targets if t.get("type") == "page"]
    return pages[0] if pages else None


def connect() -> CDPSession:
    targets = get_targets()
    target = find_main_target(targets)
    if not target:
        raise RuntimeError("No SeaTalk page target found")
    return CDPSession(target["webSocketDebuggerUrl"])


# ── JS injection snippets ─────────────────────────────────────────

# Injected once to install a lightweight change detector on the Redux store.
# It watches `messages.lists` for new message IDs and captures full messages.
INJECT_LISTENER_JS = r"""
(function() {
  var store = window.store;
  if (!store || typeof store.getState !== 'function') return {__error__: 'no Redux store'};

  // Version gate: bump version so any zombie subscribers from prior injections
  // silently become no-ops (they check myVer !== window.__ST_CDP_VER__).
  var ver = (window.__ST_CDP_VER__ || 0) + 1;
  window.__ST_CDP_VER__ = ver;

  // Grace period: after injection, wait 3s before emitting messages so Redux
  // store can hydrate with cached/fetched data without replaying historical msgs.
  window.__ST_CDP_GRACE_UNTIL__ = Date.now() + 3000;

  // Try to unsubscribe the most recent subscriber
  if (window.__ST_CDP_UNSUB__) { try { window.__ST_CDP_UNSUB__(); } catch(e) {} }

  // Fresh state
  window.__ST_CDP_QUEUE__ = [];
  window.__ST_CDP_SEEN__ = {};
  window.__ST_CDP_WATCH__ = null;
  window.__ST_CDP_FILTER__ = { adminIds: null };

  var prevLists = {};
  var state = store.getState().messages;
  var keys = Object.keys(state.lists);
  for (var i = 0; i < keys.length; i++) {
    prevLists[keys[i]] = (state.lists[keys[i]] || []).slice();
  }
  // Mark every already-loaded mid as seen so list growth from re-hydration does not replay history.
  for (var si = 0; si < keys.length; si++) {
    var arr = (state.lists[keys[si]] || []);
    for (var sj = 0; sj < arr.length; sj++) {
      window.__ST_CDP_SEEN__[arr[sj]] = 1;
    }
  }

  function resolveSender(id) {
    try {
      var u = store.getState().contact.userInfo[id];
      if (u) return u.name || u.nickname || String(id);
    } catch(e) {}
    return String(id);
  }

  function resolveGroupName(gid) {
    try {
      var g = store.getState().contact.groupInfo[gid];
      if (g) return g.name || String(gid);
    } catch(e) {}
    return String(gid);
  }

  // SeaTalk builds differ: timeStamp may be missing/renamed.
  // Do NOT fall back to Date.now(): that makes old messages look "fresh", bypassing
  // seatalk-listener last_forwarded_ts + SEATALK_MAX_MSG_AGE (replay storms after CDP reconnect).
  function resolveMsgTs(m) {
    var fields = [m.timeStamp, m.message_sent_time, m.createTime, m.serverTime, m.sentTime];
    for (var fi = 0; fi < fields.length; fi++) {
      var t = fields[fi];
      if (t != null && t !== '' && isFinite(Number(t))) {
        var n = Number(t);
        if (n > 0) return n;
      }
    }
    return 0;
  }

  var myVer = ver;
  window.__ST_CDP_UNSUB__ = store.subscribe(function() {
    // Zombie check: if a newer injection bumped the version, stop doing work
    if (myVer !== window.__ST_CDP_VER__) return;

    // Grace period: sync prevLists with current state but do not queue messages
    if (Date.now() < (window.__ST_CDP_GRACE_UNTIL__ || 0)) {
      var ms = store.getState().messages;
      var curLists = ms.lists;
      var listKeys = Object.keys(curLists);
      for (var i = 0; i < listKeys.length; i++) {
        prevLists[listKeys[i]] = (curLists[listKeys[i]] || []).slice();
      }
      return;
    }

    var ms = store.getState().messages;
    var curLists = ms.lists;
    var watch = window.__ST_CDP_WATCH__;
    var admins = window.__ST_CDP_FILTER__.adminIds;

    var listKeys = Object.keys(curLists);
    for (var i = 0; i < listKeys.length; i++) {
      var sk = listKeys[i];
      if (watch && !watch[sk]) continue;

      var cur = curLists[sk] || [];
      var prev = prevLists[sk] || [];
      if (cur.length <= prev.length) { prevLists[sk] = cur.slice(); continue; }

      var prevSet = {};
      for (var p = 0; p < prev.length; p++) prevSet[prev[p]] = 1;

      for (var n = 0; n < cur.length; n++) {
        var mid = cur[n];
        if (prevSet[mid] || window.__ST_CDP_SEEN__[mid]) continue;
        window.__ST_CDP_SEEN__[mid] = 1;

        var fullKey = sk + '-' + mid;
        var msg = ms.messages[fullKey];
        if (!msg) continue;

        if (admins && !admins[msg.senderId] && !admins[String(msg.senderId)]) continue;

        var parts = sk.split('-');
        var sessionType = parts[0];
        var sessionId = parseInt(parts[1], 10);

        var text = '';
        var c = msg.content;
        if (c && typeof c === 'object' && c.text) text = c.text;
        else if (typeof c === 'string') {
          try { var p = JSON.parse(c); if (p && p.text) text = p.text; else text = c; } catch(e) { text = c; }
        }

        var entry = {
          mid: mid,
          sessionKey: sk,
          sessionType: sessionType,
          sessionId: sessionId,
          groupName: sessionType === 'group' ? resolveGroupName(sessionId) : (sessionType === 'buddy' ? resolveSender(sessionId) : ''),
          senderId: msg.senderId,
          senderName: resolveSender(msg.senderId),
          tag: msg.tag || '',
          text: text,
          timestamp: resolveMsgTs(msg),
        };

        if (msg.tag === 'image' && msg.content) {
          entry.imageInfo = {
            fileId: msg.content.fileId || '',
            width: msg.content.width || 0,
            height: msg.content.height || 0,
            size: msg.content.size || 0,
          };
        }

        window.__ST_CDP_QUEUE__.push(entry);
      }
      prevLists[sk] = cur.slice();
    }
  });

  window.__ST_CDP_LISTENER__ = true;
  return 'installed (v' + ver + ')';
})()
"""

DRAIN_QUEUE_JS = r"""
(function() {
  if (!window.__ST_CDP_QUEUE__) return [];
  var q = window.__ST_CDP_QUEUE__.splice(0);
  return q;
})()
"""

SET_WATCH_JS = r"""
(function(groupIds) {
  if (!groupIds || groupIds.length === 0) { window.__ST_CDP_WATCH__ = null; return 'watching all'; }
  var m = {};
  for (var i = 0; i < groupIds.length; i++) {
    m['group-' + groupIds[i]] = 1;
    m['buddy-' + groupIds[i]] = 1;
  }
  window.__ST_CDP_WATCH__ = m;
  return 'watching ' + groupIds.join(',');
})(%s)
"""

SET_ADMINS_JS = r"""
(function(adminIdList) {
  if (!window.__ST_CDP_FILTER__) window.__ST_CDP_FILTER__ = { adminIds: null };
  if (!adminIdList || adminIdList.length === 0) {
    window.__ST_CDP_FILTER__.adminIds = null;
    return 'admin_filter: off (forwarding all)';
  }
  var m = {};
  for (var i = 0; i < adminIdList.length; i++) {
    var id = adminIdList[i];
    m[id] = 1;
    m[String(id)] = 1;
  }
  window.__ST_CDP_FILTER__.adminIds = m;
  return 'admin_filter: only ' + adminIdList.join(',');
})(%s)
"""


# ── Thread listing ─────────────────────────────────────────────────

THREAD_LIST_JS = r"""
(function(targetGroupId) {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var ms = store.getState().messages;
  var lists = ms.lists;
  var messages = ms.messages;
  var ui = store.getState().contact.userInfo;

  function senderName(id) {
    var u = ui[id]; return u ? (u.name || String(id)) : String(id);
  }

  var result = [];
  var listKeys = Object.keys(lists);
  for (var i = 0; i < listKeys.length; i++) {
    var sk = listKeys[i];
    if (!sk.startsWith('group-')) continue;
    var parts = sk.split('-');
    var gid = parseInt(parts[1], 10);
    if (targetGroupId && gid !== targetGroupId) continue;

    var ids = lists[sk];
    for (var j = 0; j < ids.length; j++) {
      var msg = messages[sk + '-' + ids[j]];
      if (!msg || !msg.threadInfo || !msg.threadInfo.created) continue;
      var ti = msg.threadInfo;
      var text = '';
      if (msg.content && msg.content.text) text = msg.content.text;
      result.push({
        threadId: ti.id,
        groupId: gid,
        rootMid: ids[j],
        title: text.substring(0, 200),
        creatorId: msg.senderId,
        creatorName: senderName(msg.senderId),
        createTime: ti.createTime,
        messageCount: ti.totalReplyCount,
        lastMessageTime: ti.latestReplyTime,
        latestReplyMid: ti.latestReplyMid,
        unreadReplyCount: typeof ti.unreadReplyCount === 'number' ? ti.unreadReplyCount : null,
        unreadMentionCount: typeof ti.unreadMentionCount === 'number' ? ti.unreadMentionCount : null,
      });
    }
  }
  result.sort(function(a,b){ return b.lastMessageTime - a.lastMessageTime; });
  return result;
})(%s)
"""

THREAD_MESSAGES_JS = r"""
(function(threadId) {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var ms = store.getState().messages;
  var ui = store.getState().contact.userInfo;
  var gi = store.getState().contact.groupInfo;

  function senderName(id) {
    var u = ui[id]; return u ? (u.name || String(id)) : String(id);
  }
  function groupName(gid) {
    var g = gi[gid]; return g ? (g.name || String(gid)) : String(gid);
  }

  var lists = ms.lists;
  var messages = ms.messages;

  var threadMsgIds = lists[threadId];
  if (!threadMsgIds || threadMsgIds.length === 0) {
    return {__not_loaded__: true, threadId: threadId};
  }

  var parts = threadId.split('-');
  var gid = parseInt(parts[1], 10);
  var rootMid = parts.slice(2).join('-');

  var result = [];
  var rootMsg = messages['group-' + gid + '-' + rootMid];
  if (rootMsg) {
    var text = '';
    if (rootMsg.content && rootMsg.content.text) text = rootMsg.content.text;
    result.push({
      mid: rootMid,
      threadId: threadId,
      groupId: gid,
      groupName: groupName(gid),
      senderId: rootMsg.senderId,
      senderName: senderName(rootMsg.senderId),
      tag: rootMsg.tag || '',
      text: text,
      timestamp: rootMsg.timeStamp || 0,
      isRoot: true,
    });
  }

  for (var i = 0; i < threadMsgIds.length; i++) {
    var mid = threadMsgIds[i];
    var fullKey = threadId + '-' + mid;
    var msg = messages[fullKey];
    if (!msg) continue;
    var text = '';
    if (msg.content && msg.content.text) text = msg.content.text;
    result.push({
      mid: mid,
      threadId: threadId,
      groupId: gid,
      groupName: groupName(gid),
      senderId: msg.senderId,
      senderName: senderName(msg.senderId),
      tag: msg.tag || '',
      text: text,
      timestamp: msg.timeStamp || 0,
      isRoot: false,
    });
  }
  result.sort(function(a,b){ return a.timestamp - b.timestamp; });
  return result;
})(%s)
"""


# ── Unread / read-unread / list-groups / switch-chat / open-thread / close-thread ──

UNREAD_JS = r"""
(function(targetGroupIds) {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var ms = store.getState().messages;
  var uc = ms.unreadCounts;
  var ack = ms.ack;
  var gi = store.getState().contact.groupInfo;

  function groupName(gid) {
    var g = gi[gid]; return g ? (g.name || String(gid)) : String(gid);
  }

  var result = [];
  if (targetGroupIds && targetGroupIds.length > 0) {
    for (var i = 0; i < targetGroupIds.length; i++) {
      var gid = targetGroupIds[i];
      var sk = 'group-' + gid;
      result.push({groupId: gid, groupName: groupName(gid), unread: uc[sk] || 0, ack: ack[sk] || null});
    }
  } else {
    var keys = Object.keys(uc);
    for (var j = 0; j < keys.length; j++) {
      var k = keys[j];
      if (!k.startsWith('group-') || uc[k] <= 0) continue;
      var gid2 = parseInt(k.split('-')[1], 10);
      result.push({groupId: gid2, groupName: groupName(gid2), unread: uc[k], ack: ack[k] || null});
    }
    result.sort(function(a,b){ return b.unread - a.unread; });
  }
  return result;
})(%s)
"""

READ_UNREAD_JS = r"""
(function(groupId, maxCount) {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var ms = store.getState().messages;
  var gi = store.getState().contact.groupInfo;
  var ui = store.getState().contact.userInfo;

  function senderName(id) {
    var u = ui[id]; return u ? (u.name || String(id)) : String(id);
  }
  function groupName(gid) {
    var g = gi[gid]; return g ? (g.name || String(gid)) : String(gid);
  }

  var sk = 'group-' + groupId;
  var ackMid = ms.ack[sk];
  var ids = ms.lists[sk];
  if (!ids) return {__error__: 'no messages for group ' + groupId};

  var startIdx = 0;
  if (ackMid) {
    for (var a = 0; a < ids.length; a++) {
      if (ids[a] === ackMid) { startIdx = a + 1; break; }
    }
  }

  var result = [];
  var end = maxCount ? Math.min(startIdx + maxCount, ids.length) : ids.length;
  for (var i = startIdx; i < end; i++) {
    var msg = ms.messages[sk + '-' + ids[i]];
    if (!msg) continue;
    var text = '';
    if (msg.content && msg.content.text) text = msg.content.text;
    result.push({
      mid: ids[i],
      sessionKey: sk,
      groupId: groupId,
      groupName: groupName(groupId),
      senderId: msg.senderId,
      senderName: senderName(msg.senderId),
      tag: msg.tag || '',
      text: text,
      timestamp: msg.timeStamp || 0,
      threadInfo: msg.threadInfo ? {id: msg.threadInfo.id, replyCount: msg.threadInfo.totalReplyCount} : null,
    });
  }
  return result;
})(%s, %s)
"""

LIST_GROUPS_JS = r"""
(function(filterName) {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var gi = store.getState().contact.groupInfo;
  var uc = store.getState().messages.unreadCounts;

  var result = [];
  var gids = Object.keys(gi);
  for (var i = 0; i < gids.length; i++) {
    var gid = parseInt(gids[i], 10);
    var g = gi[gid];
    if (!g || !g.name) continue;
    if (filterName && g.name.toLowerCase().indexOf(filterName.toLowerCase()) < 0) continue;
    result.push({groupId: gid, groupName: g.name, unread: uc['group-' + gid] || 0});
  }
  result.sort(function(a,b){ return b.unread - a.unread; });
  return result;
})(%s)
"""

ALL_UNREAD_SESSIONS_JS = r"""
(function() {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var uc = store.getState().messages.unreadCounts;
  var groups = [], buddies = [];
  for (var k in uc) {
    if (!uc[k] || uc[k] <= 0) continue;
    if (k.indexOf('group-') === 0) {
      groups.push({id: parseInt(k.split('-')[1], 10), unread: uc[k]});
    } else if (k.indexOf('buddy-') === 0) {
      buddies.push({id: parseInt(k.split('-')[1], 10), unread: uc[k]});
    }
  }
  groups.sort(function(a,b){ return b.unread - a.unread; });
  buddies.sort(function(a,b){ return b.unread - a.unread; });
  return {groups: groups, buddies: buddies};
})()
"""

SWITCH_CHAT_JS = r"""
(function(groupId) {
  var store = window.store;
  if (!store) return {__error__: 'no store'};

  var targetId = String(groupId);
  var el = document.querySelector('.messages-chat-session-list-item[data-id="' + targetId + '"]');
  if (el) {
    var onclick = el.querySelector('.onclick-wrapper');
    (onclick || el).click();
    var gi = store.getState().contact.groupInfo[groupId];
    return {ok: true, groupId: groupId, groupName: gi ? gi.name : String(groupId)};
  }

  return {__need_search__: true, groupId: groupId};
})(%s)
"""

SWITCH_CHAT_SEARCH_JS = r"""
(function(searchText) {
  var input = document.querySelector('.global-search-input input');
  if (!input) return {__error__: 'no global search input'};
  input.focus();
  var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, searchText);
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: true};
})(%s)
"""

SWITCH_CHAT_CLICK_RESULT_JS = r"""
(function(groupId) {
  var popover = document.querySelector('.ant-popover:not(.ant-popover-hidden)');
  if (!popover) return {__error__: 'search popover not visible'};

  var items = popover.querySelectorAll('.contact-contact-list-item');
  if (!items.length) return {__error__: 'no search results'};

  items[0].click();

  var input = document.querySelector('.global-search-input input');
  if (input) {
    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, '');
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.blur();
  }

  var gi = window.store.getState().contact.groupInfo[groupId];
  return {ok: true, groupId: groupId, groupName: gi ? gi.name : String(groupId)};
})(%s)
"""

OPEN_THREAD_CLICK_JS = r"""
(function(targetMid) {
  var msgEl = document.querySelector('[data-mid="' + targetMid + '"]');
  if (!msgEl) return {__error__: 'message ' + targetMid + ' not visible in chat (scroll or switch to group first)'};

  var threadBox = msgEl.querySelector('.thread-status-box') ||
                  msgEl.querySelector('.thread-status-container');
  if (!threadBox) return {__error__: 'message has no thread indicator (no replies yet?)'};

  threadBox.click();
  return {ok: true};
})(%s)
"""

SCROLL_TO_MID_JS = r"""
(function(targetMid) {
  var msgEl = document.querySelector('[data-mid="' + targetMid + '"]');
  if (!msgEl) return {ok: false, reason: 'not_in_dom'};
  try {
    msgEl.scrollIntoView({block: 'center', behavior: 'instant'});
  } catch (e) {
    try { msgEl.scrollIntoView(true); } catch (e2) {}
  }
  return {ok: true};
})(%s)
"""

FIND_MESSAGE_SCROLL_CONTAINER_JS = r"""
(function() {
  function find() {
    var sample = document.querySelector('[data-mid]');
    if (sample) {
      var el = sample.parentElement;
      while (el && el !== document.body) {
        var st = window.getComputedStyle(el);
        if ((st.overflowY === 'auto' || st.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 40)
          return el;
        el = el.parentElement;
      }
    }
    var hints = ['.messages-main-content', '.im-session-main', '[class*="session-main"]', '.ant-layout-content'];
    for (var h = 0; h < hints.length; h++) {
      var nodes = document.querySelectorAll(hints[h]);
      for (var j = 0; j < nodes.length; j++) {
        var n = nodes[j];
        var st = window.getComputedStyle(n);
        if ((st.overflowY === 'auto' || st.overflowY === 'scroll') && n.scrollHeight > n.clientHeight + 40)
          return n;
        var inner = n.querySelector('.scrollbar__view, .ReactVirtualized__List, [class*="virtual"]');
        if (inner) {
          st = window.getComputedStyle(inner);
          if ((st.overflowY === 'auto' || st.overflowY === 'scroll') && inner.scrollHeight > inner.clientHeight + 40)
            return inner;
        }
      }
    }
    return null;
  }
  window.__ST_MSG_SCROLLER__ = find();
  if (!window.__ST_MSG_SCROLLER__) return {ok: false};
  return {
    ok: true,
    scrollHeight: window.__ST_MSG_SCROLLER__.scrollHeight,
    clientHeight: window.__ST_MSG_SCROLLER__.clientHeight
  };
})()
"""

SCROLL_MESSAGE_LIST_STEP_JS = r"""
(function(direction) {
  var sc = window.__ST_MSG_SCROLLER__;
  if (!sc) return {ok: false, reason: 'no_scroller'};
  var h = Math.max(120, Math.floor(sc.clientHeight * 0.78));
  var before = sc.scrollTop;
  var maxScroll = Math.max(0, sc.scrollHeight - sc.clientHeight);
  if (direction === 'older') {
    sc.scrollTop = Math.max(0, before - h);
  } else if (direction === 'newer') {
    sc.scrollTop = Math.min(maxScroll, before + h);
  } else if (direction === 'bottom') {
    sc.scrollTop = maxScroll;
  } else if (direction === 'top') {
    sc.scrollTop = 0;
  }
  var moved = Math.abs(sc.scrollTop - before) > 3;
  return {ok: true, scrollTop: sc.scrollTop, maxScroll: maxScroll, moved: moved};
})(%s)
"""

CLOSE_THREAD_CLICK_JS = r"""
(function() {
  var drawer = document.querySelector('.ant-drawer.thread-detail-panel');
  if (!drawer) return {__error__: 'no thread panel open'};

  var closeBtn = drawer.querySelector('.thread-detail-panel-title .common-icon');
  if (!closeBtn) return {__error__: 'close button not found'};

  closeBtn.click();
  return {ok: true};
})()
"""

INSTALL_PICK_SCROLLER_JS = r"""
(function() {
  if (window.__ST_pickScroller__) return 'already installed';
  window.__ST_pickScroller__ = function(root) {
    var hints = [
      '.scrollbar__view',
      '.ReactVirtualized__List',
      '[class*="thread-detail"] .scrollbar__view',
      '[class*="message-list"]',
      '.im-session-main',
      '.messages-main-content'
    ];
    var h, el, st, best = null, bestGrow = 0;
    for (h = 0; h < hints.length; h++) {
      var nodes = root.querySelectorAll(hints[h]);
      for (var j = 0; j < nodes.length; j++) {
        el = nodes[j];
        st = window.getComputedStyle(el);
        if ((st.overflowY === 'auto' || st.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 24) {
          var grow = el.scrollHeight - el.clientHeight;
          if (grow > bestGrow) { bestGrow = grow; best = el; }
        }
      }
    }
    if (best) return best;
    var divs = root.querySelectorAll('div');
    for (var i = 0; i < divs.length; i++) {
      el = divs[i];
      st = window.getComputedStyle(el);
      if ((st.overflowY === 'auto' || st.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 48) {
        var g = el.scrollHeight - el.clientHeight;
        if (g > bestGrow) { bestGrow = g; best = el; }
      }
    }
    return best;
  };
  return 'installed';
})()
"""

# Scroll thread detail drawer to bottom so SeaTalk loads latest replies and sends read receipts.
SCROLL_THREAD_DETAIL_PANEL_JS = r"""
(function() {
  var drawer = document.querySelector('.ant-drawer.thread-detail-panel');
  if (!drawer) return {ok: false, reason: 'no_drawer'};
  var pickScroller = window.__ST_pickScroller__ || function() { return null; };
  var sc = pickScroller(drawer);
  if (!sc) return {ok: true, scrolled: false};
  var lastTop = -999, same = 0, round = 0;
  while (round < 14) {
    var maxS = Math.max(0, sc.scrollHeight - sc.clientHeight);
    sc.scrollTop = maxS;
    round++;
    if (Math.abs(sc.scrollTop - lastTop) < 4) {
      same++;
      if (same >= 3) break;
    } else {
      same = 0;
    }
    lastTop = sc.scrollTop;
  }
  return {
    ok: true,
    scrolled: true,
    scrollTop: sc.scrollTop,
    maxScroll: Math.max(0, sc.scrollHeight - sc.clientHeight),
    scrollHeight: sc.scrollHeight
  };
})()
"""

# Focus thread composer, scroll visible thread bubbles, scroll to bottom, click message area — mimics a human "read".
THREAD_PANEL_SIMULATE_READ_JS = r"""
(function() {
  var drawer = document.querySelector('.ant-drawer.thread-detail-panel');
  if (!drawer) return {ok: false, reason: 'no_drawer'};
  var report = {midCount: 0, focused: false, clicked: false, scrolled: false};
  var pickScroller = window.__ST_pickScroller__ || function() { return null; };

  var ed = drawer.querySelector('.ProseMirror.seatalk-editor-content');
  if (ed && ed.editor && ed.editor.view) {
    try {
      ed.editor.view.focus();
      report.focused = true;
    } catch (e0) {}
  }

  var mids = drawer.querySelectorAll('[data-mid]');
  report.midCount = mids.length;
  for (var i = 0; i < mids.length; i++) {
    try { mids[i].scrollIntoView({block: 'nearest', behavior: 'instant'}); } catch (e1) {}
  }

  var sc = pickScroller(drawer);
  if (sc) {
    var lastTop = -999, same = 0, round = 0;
    while (round < 14) {
      var maxS = Math.max(0, sc.scrollHeight - sc.clientHeight);
      sc.scrollTop = maxS;
      round++;
      if (Math.abs(sc.scrollTop - lastTop) < 4) {
        same++;
        if (same >= 3) break;
      } else {
        same = 0;
      }
      lastTop = sc.scrollTop;
    }
    report.scrolled = true;
    var r = sc.getBoundingClientRect();
    var x = r.left + Math.min(Math.max(r.width * 0.52, 12), r.width - 6);
    var y = r.top + Math.min(Math.max(r.height * 0.4, 12), r.height - 6);
    var target = document.elementFromPoint(x, y);
    if (target && drawer.contains(target)) {
      try {
        target.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, clientX: x, clientY: y, view: window, buttons: 1}));
        target.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, clientX: x, clientY: y, view: window, buttons: 0}));
        target.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: x, clientY: y, view: window, buttons: 0}));
        report.clicked = true;
        report.clickTag = target.tagName;
      } catch (e2) {}
    }
  }

  return {ok: true, report: report};
})()
"""

THREAD_UNREAD_SNAPSHOT_JS = r"""
(function(gid, rootMid) {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var key = 'group-' + gid + '-' + rootMid;
  var m = store.getState().messages.messages[key];
  if (!m || !m.threadInfo) return {ok: false, reason: 'no_thread_on_root'};
  var ti = m.threadInfo;
  return {
    ok: true,
    unreadReplyCount: ti.unreadReplyCount,
    unreadMentionCount: ti.unreadMentionCount,
    consumedMid: ti.consumedMid,
    latestReplyMid: ti.latestReplyMid
  };
})(%s, %s)
"""

PROBE_THREAD_READ_JS = r"""
(function(targetGroupId) {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var ms = store.getState().messages;
  var ucKeys = Object.keys(ms.unreadCounts || {});
  var ackKeys = Object.keys(ms.ack || {});
  var longUc = ucKeys.filter(function(k) {
    return k.indexOf('group-') === 0 && k.split('-').length > 2;
  });
  var longAck = ackKeys.filter(function(k) {
    return k.indexOf('group-') === 0 && k.split('-').length > 2;
  });
  var weird = ucKeys.filter(function(k) {
    return k.indexOf('group-') !== 0 && k.indexOf('buddy-') !== 0;
  });
  var sampleTi = null;
  if (targetGroupId) {
    var sk = 'group-' + targetGroupId;
    var ids = (ms.lists && ms.lists[sk]) || [];
    for (var i = ids.length - 1; i >= 0 && !sampleTi; i--) {
      var msg = ms.messages[sk + '-' + ids[i]];
      if (msg && msg.threadInfo) {
        var o = msg.threadInfo;
        var slim = {};
        for (var k in o) {
          if (!Object.prototype.hasOwnProperty.call(o, k)) continue;
          var v = o[k];
          if (v === null || typeof v === 'number' || typeof v === 'boolean') slim[k] = v;
          else if (typeof v === 'string' && v.length < 96) slim[k] = v;
          else slim[k] = typeof v;
        }
        sampleTi = {rootMid: ids[i], threadKeys: Object.keys(o), threadInfoSlim: slim};
      }
    }
  }
  return {
    messagesTopKeys: Object.keys(ms),
    unreadCountsTotal: ucKeys.length,
    ackTotal: ackKeys.length,
    unreadNonSessionSamples: weird.slice(0, 50),
    unreadLongGroupForm: longUc.slice(0, 25),
    ackLongGroupForm: longAck.slice(0, 25),
    currentThreadId: ms.currentThreadId,
    sampleThreadRoot: sampleTi
  };
})(%s)
"""


# ── Open API fallback for thread messages ──────────────────────────

SEATALK_APP_ID = os.environ.get("SEATALK_APP_ID", "")
SEATALK_APP_SECRET = os.environ.get("SEATALK_APP_SECRET", "")
_token_cache: Dict[str, Any] = {"token": "", "expires_at": 0}


def _get_access_token() -> str:
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]
    if not SEATALK_APP_ID or not SEATALK_APP_SECRET:
        raise RuntimeError("SEATALK_APP_ID / SEATALK_APP_SECRET not configured")
    conn = http.client.HTTPSConnection("openapi.seatalk.io", timeout=10)
    body = json.dumps({"app_id": SEATALK_APP_ID, "app_secret": SEATALK_APP_SECRET})
    conn.request("POST", "/auth/app_access_token", body,
                 {"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    if data.get("code") != 0:
        raise RuntimeError(f"token error: {data}")
    _token_cache["token"] = data["app_access_token"]
    _token_cache["expires_at"] = now + data.get("expire_in", 7200)
    return _token_cache["token"]


def _api_get_thread_messages(group_id: str, thread_id: str) -> List[Dict]:
    """Fetch thread messages via SeaTalk Open API with cursor pagination."""
    token = _get_access_token()
    all_msgs: List[Dict] = []
    cursor = ""
    while True:
        params = f"group_id={group_id}&thread_id={thread_id}&page_size=100"
        if cursor:
            params += f"&cursor={cursor}"
        conn = http.client.HTTPSConnection("openapi.seatalk.io", timeout=15)
        conn.request("GET",
                     f"/messaging/v2/group_chat/get_thread_by_thread_id?{params}",
                     headers={"Authorization": f"Bearer {token}",
                              "Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        conn.close()
        if data.get("code") != 0:
            raise RuntimeError(f"API error: {data}")
        for m in data.get("thread_messages", []):
            sender = m.get("sender", {})
            text = ""
            if m.get("text") and m["text"].get("plain_text"):
                text = m["text"]["plain_text"]
            all_msgs.append({
                "mid": m.get("message_id", ""),
                "threadId": m.get("thread_id", thread_id),
                "groupId": group_id,
                "senderId": sender.get("seatalk_id", ""),
                "senderEmail": sender.get("email", ""),
                "senderCode": sender.get("employee_code", ""),
                "senderType": m.get("sender_type", 0),
                "tag": m.get("tag", ""),
                "text": text,
                "timestamp": m.get("message_sent_time", 0),
            })
        cursor = data.get("next_cursor", "")
        if not cursor:
            break
    return all_msgs


# ── Read cached messages ───────────────────────────────────────────

READ_MESSAGES_JS = r"""
(function(targetGroupId) {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var ms = store.getState().messages;
  var lists = ms.lists;
  var messages = ms.messages;
  var gi = store.getState().contact.groupInfo;
  var ui = store.getState().contact.userInfo;

  function senderName(id) {
    var u = ui[id]; return u ? (u.name || String(id)) : String(id);
  }
  function groupName(gid) {
    var g = gi[gid]; return g ? (g.name || String(gid)) : String(gid);
  }

  var result = [];
  var listKeys = Object.keys(lists);
  for (var i = 0; i < listKeys.length; i++) {
    var sk = listKeys[i];
    if (!sk.startsWith('group-')) continue;
    var parts = sk.split('-');
    var gid = parseInt(parts[1], 10);
    if (targetGroupId && gid !== targetGroupId) continue;

    var ids = lists[sk];
    for (var j = 0; j < ids.length; j++) {
      var msg = messages[sk + '-' + ids[j]];
      if (!msg) continue;
      var text = '';
      if (msg.content && msg.content.text) text = msg.content.text;
      result.push({
        mid: ids[j],
        sessionKey: sk,
        groupId: gid,
        groupName: groupName(gid),
        senderId: msg.senderId,
        senderName: senderName(msg.senderId),
        tag: msg.tag || '',
        text: text,
        timestamp: msg.timeStamp || 0,
      });
    }
  }
  result.sort(function(a,b){ return a.timestamp - b.timestamp; });
  return result;
})(%s)
"""


READ_BUDDY_MESSAGES_JS = r"""
(function(targetBuddyId) {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var ms = store.getState().messages;
  var lists = ms.lists;
  var messages = ms.messages;
  var ui = store.getState().contact.userInfo;

  function senderName(id) {
    var u = ui[id]; return u ? (u.name || u.nickname || String(id)) : String(id);
  }
  function buddyName(bid) {
    var u = ui[bid]; return u ? (u.name || u.nickname || String(bid)) : String(bid);
  }
  function extractText(msg) {
    var c = msg.content;
    if (!c) return '';
    if (typeof c === 'object' && c.text) return c.text;
    if (typeof c === 'string') {
      try { var p = JSON.parse(c); if (p && p.text) return p.text; } catch(e) {}
      return c;
    }
    return '';
  }
  function detectTag(msg) {
    if (msg.tag) return msg.tag;
    var c = msg.content;
    if (!c) return '';
    var obj = c;
    if (typeof c === 'string') { try { obj = JSON.parse(c); } catch(e) { return ''; } }
    if (typeof obj === 'object' && obj.fileId && (obj.width || obj.height)) return 'image';
    if (typeof obj === 'object' && obj.fileId && obj.fileName) return 'file';
    return '';
  }

  var result = [];
  var listKeys = Object.keys(lists);
  for (var i = 0; i < listKeys.length; i++) {
    var sk = listKeys[i];
    if (!sk.startsWith('buddy-')) continue;
    var parts = sk.split('-');
    var bid = parseInt(parts[1], 10);
    if (targetBuddyId && bid !== targetBuddyId) continue;

    var ids = lists[sk];
    for (var j = 0; j < ids.length; j++) {
      var msg = messages[sk + '-' + ids[j]];
      if (!msg) continue;
      var text = extractText(msg);
      var tag = detectTag(msg);
      var entry = {
        mid: ids[j],
        sessionKey: sk,
        buddyId: bid,
        buddyName: buddyName(bid),
        senderId: msg.senderId,
        senderName: senderName(msg.senderId),
        tag: tag,
        text: text,
        timestamp: msg.timeStamp || 0,
      };
      if (tag === 'image') {
        var c = msg.content;
        var obj = (typeof c === 'string') ? (function(){ try { return JSON.parse(c); } catch(e) { return {}; } })() : (c || {});
        entry.imageInfo = {fileId: obj.fileId || '', width: obj.width || 0, height: obj.height || 0, size: obj.size || 0};
      }
      result.push(entry);
    }
  }
  result.sort(function(a,b){ return a.timestamp - b.timestamp; });
  return result;
})(%s)
"""


# ── Commands ───────────────────────────────────────────────────────

def cmd_targets():
    for i, t in enumerate(get_targets()):
        print(f"[{i}] type={t.get('type')}  title={t.get('title','')[:60]}  url={t.get('url','')[:80]}")


def cmd_explore():
    s = connect()
    probes = [
        ("window.store (Redux)", "window.store && typeof window.store.getState === 'function'"),
        ("store.getState() keys", "window.store ? Object.keys(window.store.getState()) : null"),
        ("messages.lists count", "window.store ? Object.keys(window.store.getState().messages.lists).length : 0"),
        ("messages.messages count", "window.store ? Object.keys(window.store.getState().messages.messages).length : 0"),
        ("contact.groupInfo count", "window.store ? Object.keys(window.store.getState().contact.groupInfo).length : 0"),
        ("contact.userInfo count", "window.store ? Object.keys(window.store.getState().contact.userInfo).length : 0"),
    ]
    for label, expr in probes:
        val = s.evaluate(expr)
        print(f"  {label}: {json.dumps(val, ensure_ascii=False)[:200]}")
    s.close()


def cmd_eval(expression: str):
    s = connect()
    result = s.evaluate(expression)
    if isinstance(result, (dict, list)):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(result)
    s.close()


def cmd_read(group_id: Optional[int] = None):
    s = connect()
    js = READ_MESSAGES_JS % (str(group_id) if group_id else "null")
    result = s.evaluate(js)
    s.close()
    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_read_buddy(buddy_id: Optional[int] = None, output_file: Optional[str] = None, limit: Optional[int] = None):
    s = connect()
    js = READ_BUDDY_MESSAGES_JS % (str(buddy_id) if buddy_id else "null")
    result = s.evaluate(js)
    s.close()
    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        sys.exit(1)
    _output_messages(result, output_file, limit)


def cmd_threads(group_id: Optional[int] = None):
    """List threads in a group by scanning threadInfo on cached messages."""
    s = connect()
    js = THREAD_LIST_JS % (str(group_id) if group_id else "null")
    result = s.evaluate(js)
    s.close()
    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _output_messages(msgs: list, output_file: Optional[str] = None, limit: Optional[int] = None):
    """Write message list to stdout or file. Auto-redirect to file when exceeding threshold."""
    total = len(msgs)
    if limit and total > limit:
        msgs = msgs[-limit:]
        print(f"# Showing last {limit} of {total} messages", file=sys.stderr)

    auto_file = False
    if not output_file and len(msgs) > AUTO_FILE_THRESHOLD:
        output_file = "/tmp/seatalk-thread-messages.json"
        auto_file = True

    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(msgs, f, indent=2, ensure_ascii=False)
        print(f"# {total} messages written to {output_file}", file=sys.stderr)
        if auto_file:
            print(f"# (auto-redirected: {total} messages exceeds {AUTO_FILE_THRESHOLD} threshold)", file=sys.stderr)
    else:
        print(json.dumps(msgs, indent=2, ensure_ascii=False))


def cmd_thread_messages(thread_id: str, output_file: Optional[str] = None, limit: Optional[int] = None):
    """Read messages from a thread via Redux store (requires thread opened in UI)."""
    s = connect()
    js = THREAD_MESSAGES_JS % json.dumps(thread_id)
    result = s.evaluate(js)
    s.close()

    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        sys.exit(1)

    if isinstance(result, dict) and result.get("__not_loaded__"):
        print(
            f"Thread {thread_id} is not loaded in Redux store.\n"
            "Please open this thread in SeaTalk UI first, then retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    _output_messages(result, output_file, limit)


def cmd_unread(group_ids: Optional[List[int]] = None):
    """Query unread message counts for specified groups (or all unread groups)."""
    s = connect()
    gids_arg = json.dumps(group_ids) if group_ids else "null"
    result = s.evaluate(UNREAD_JS % gids_arg)
    s.close()
    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_read_unread(group_id: int, output_file: Optional[str] = None, limit: Optional[int] = None):
    """Read unread messages (after ack cursor) for a group."""
    s = connect()
    limit_arg = str(limit) if limit else "null"
    js = READ_UNREAD_JS % (str(group_id), limit_arg)
    result = s.evaluate(js)
    s.close()
    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        sys.exit(1)
    _output_messages(result, output_file, limit)


def cmd_list_groups(filter_name: Optional[str] = None):
    """List joined groups, optionally filtered by name."""
    s = connect()
    arg = json.dumps(filter_name) if filter_name else "null"
    result = s.evaluate(LIST_GROUPS_JS % arg)
    s.close()
    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _switch_via_search(s: CDPSession, group_id: int) -> dict:
    """Fallback: use global search to navigate to a group not visible in sidebar."""
    gi = s.evaluate(f"(function(){{ var g = window.store.getState().contact.groupInfo[{group_id}]; return g ? g.name : null; }})()")
    search_text = gi if gi else str(group_id)
    r = s.evaluate(SWITCH_CHAT_SEARCH_JS % json.dumps(search_text))
    if isinstance(r, dict) and "__error__" in r:
        return r
    time.sleep(1.5)
    r2 = s.evaluate(SWITCH_CHAT_CLICK_RESULT_JS % str(group_id))
    return r2


def _ensure_group(s: CDPSession, group_id: int) -> None:
    """Ensure the UI is switched to the given group, using search fallback if needed."""
    _try_ensure_group(s, group_id, on_error_exit=True)


def _try_ensure_group(s: CDPSession, group_id: int, on_error_exit: bool = False) -> bool:
    """Switch UI to group chat. Returns False on failure; may sys.exit if on_error_exit."""
    cur = s.evaluate("window.store.getState().messages.selectedSession")
    if isinstance(cur, dict) and cur.get("id") == group_id and cur.get("type") == "group":
        return True
    sw = s.evaluate(SWITCH_CHAT_JS % str(group_id))
    if isinstance(sw, dict) and sw.get("__need_search__"):
        sw = _switch_via_search(s, group_id)
    if isinstance(sw, dict) and "__error__" in sw:
        print(f"ERROR switching to group: {sw['__error__']}", file=sys.stderr)
        if on_error_exit:
            s.close()
            sys.exit(1)
        return False
    time.sleep(1.0 if on_error_exit else 0.6)
    return True


def _try_ensure_buddy(s: CDPSession, buddy_id: int) -> bool:
    """Switch UI to private chat with buddy. Returns False on failure."""
    cur = s.evaluate("window.store.getState().messages.selectedSession")
    if isinstance(cur, dict) and cur.get("id") == buddy_id and cur.get("type") == "buddy":
        return True
    result = s.evaluate(SWITCH_BUDDY_JS % str(buddy_id))
    if isinstance(result, dict) and result.get("__need_search__"):
        ui = s.evaluate(
            f"(function(){{ var u = window.store.getState().contact.userInfo[{buddy_id}]; "
            f"return u ? (u.name || u.nickname) : null; }})()"
        )
        search_text = ui if ui else str(buddy_id)
        r = s.evaluate(SWITCH_CHAT_SEARCH_JS % json.dumps(search_text))
        if not (isinstance(r, dict) and r.get("__error__")):
            time.sleep(1.5)
            result = s.evaluate(SWITCH_CHAT_CLICK_RESULT_JS % str(buddy_id))
    if isinstance(result, dict) and "__error__" in result:
        print(f"WARNING: buddy {buddy_id}: {result['__error__']}", file=sys.stderr)
        return False
    time.sleep(0.6)
    return True


def cmd_mark_all_read():
    """Open each session with unread > 0 so SeaTalk clears unread (same as manual click)."""
    s = connect()
    data = s.evaluate(ALL_UNREAD_SESSIONS_JS)
    if isinstance(data, dict) and "__error__" in data:
        print(f"ERROR: {data['__error__']}", file=sys.stderr)
        s.close()
        sys.exit(1)
    groups = data.get("groups") or []
    buddies = data.get("buddies") or []
    print(
        f"# sessions with unread: {len(groups)} groups, {len(buddies)} private chats",
        file=sys.stderr,
    )
    ok_g, ok_b, fail_g, fail_b = 0, 0, 0, 0
    for g in groups:
        gid = int(g["id"])
        n = int(g.get("unread") or 0)
        if _try_ensure_group(s, gid, on_error_exit=False):
            ok_g += 1
            print(f"# opened group {gid} (was ~{n} unread)", file=sys.stderr)
        else:
            fail_g += 1
        time.sleep(0.35)
    for b in buddies:
        bid = int(b["id"])
        n = int(b.get("unread") or 0)
        if _try_ensure_buddy(s, bid):
            ok_b += 1
            print(f"# opened buddy {bid} (was ~{n} unread)", file=sys.stderr)
        else:
            fail_b += 1
        time.sleep(0.35)
    s.close()
    print(
        json.dumps(
            {
                "ok": True,
                "groupsOpened": ok_g,
                "groupsFailed": fail_g,
                "buddiesOpened": ok_b,
                "buddiesFailed": fail_b,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def _js_mid_in_dom_expr(mid: str) -> str:
    return "(function(m){return !!document.querySelector('[data-mid=\"'+m+'\"]');})(" + json.dumps(mid) + ")"


def _ensure_mid_visible_with_scroll(s: CDPSession, mid: str, max_steps: int = 80) -> bool:
    """Scroll main chat until [data-mid] exists (virtual list / lazy load)."""
    if s.evaluate(_js_mid_in_dom_expr(mid)):
        return True
    ini = s.evaluate(FIND_MESSAGE_SCROLL_CONTAINER_JS)
    if not (isinstance(ini, dict) and ini.get("ok")):
        return False
    time.sleep(0.12)
    s.evaluate(SCROLL_MESSAGE_LIST_STEP_JS % json.dumps("bottom"))
    time.sleep(0.22)
    if s.evaluate(_js_mid_in_dom_expr(mid)):
        return True
    stuck = 0
    for _ in range(max_steps):
        if s.evaluate(_js_mid_in_dom_expr(mid)):
            return True
        r = s.evaluate(SCROLL_MESSAGE_LIST_STEP_JS % json.dumps("older"))
        time.sleep(0.22)
        if isinstance(r, dict) and not r.get("moved"):
            stuck += 1
            if stuck >= 4:
                break
        else:
            stuck = 0
    s.evaluate(SCROLL_MESSAGE_LIST_STEP_JS % json.dumps("top"))
    time.sleep(0.22)
    stuck = 0
    for _ in range(max_steps):
        if s.evaluate(_js_mid_in_dom_expr(mid)):
            return True
        r = s.evaluate(SCROLL_MESSAGE_LIST_STEP_JS % json.dumps("newer"))
        time.sleep(0.22)
        if isinstance(r, dict) and not r.get("moved"):
            stuck += 1
            if stuck >= 4:
                break
        else:
            stuck = 0
    return bool(s.evaluate(_js_mid_in_dom_expr(mid)))


def _ensure_pick_scroller(s: CDPSession) -> None:
    """Install the shared pickScroller helper into the page if not already present."""
    s.evaluate(INSTALL_PICK_SCROLLER_JS)


def _thread_panel_scroll_to_end(s: CDPSession) -> None:
    """Mimic reading inside the thread drawer (focus, mids, scroll, click) then force scroll to bottom."""
    _ensure_pick_scroller(s)
    s.evaluate(THREAD_PANEL_SIMULATE_READ_JS)
    time.sleep(0.12)
    s.evaluate(SCROLL_THREAD_DETAIL_PANEL_JS)
    time.sleep(0.15)


def _thread_unread_snapshot(s: CDPSession, group_id: int, root_mid: str) -> dict:
    js = THREAD_UNREAD_SNAPSHOT_JS % (str(int(group_id)), json.dumps(root_mid))
    r = s.evaluate(js)
    return r if isinstance(r, dict) else {}


def _sync_thread_unread_until_clear(
    s: CDPSession,
    group_id: int,
    root_mid: str,
    max_wait_sec: float,
    dwell_ms: int,
) -> bool:
    """Poll root message threadInfo until unreadReplyCount/unreadMentionCount are 0 (SeaTalk truth for thread read)."""
    dwell_sec = max(0, dwell_ms) / 1000.0
    deadline = time.time() + max(0.0, max_wait_sec)

    while time.time() < deadline:
        snap = _thread_unread_snapshot(s, group_id, root_mid)
        if snap.get("__error__"):
            if dwell_sec > 0:
                time.sleep(dwell_sec)
            return False
        if snap.get("ok"):
            ur = int(snap.get("unreadReplyCount") or 0)
            um = int(snap.get("unreadMentionCount") or 0)
            if ur == 0 and um == 0:
                if dwell_sec > 0:
                    time.sleep(dwell_sec)
                return True
        for _ in range(3):
            s.evaluate(THREAD_PANEL_SIMULATE_READ_JS)
            time.sleep(0.12)
        _thread_panel_scroll_to_end(s)
        time.sleep(0.22)
        s.evaluate(SCROLL_THREAD_DETAIL_PANEL_JS)
        time.sleep(0.28)

    snap = _thread_unread_snapshot(s, group_id, root_mid)
    if snap.get("ok"):
        ur = int(snap.get("unreadReplyCount") or 0)
        um = int(snap.get("unreadMentionCount") or 0)
        if ur == 0 and um == 0:
            if dwell_sec > 0:
                time.sleep(dwell_sec)
            return True
    return False


def _wait_thread_redux_loaded(s: CDPSession, thread_id: str, timeout_sec: float = 8.0) -> bool:
    """Wait until Redux has lists[threadId] with at least the root message row."""
    deadline = time.time() + timeout_sec
    js = THREAD_MESSAGES_JS % json.dumps(thread_id)
    while time.time() < deadline:
        r = s.evaluate(js)
        if isinstance(r, list) and len(r) > 0:
            return True
        if isinstance(r, dict):
            if r.get("__error__"):
                return False
            if r.get("__not_loaded__"):
                time.sleep(0.2)
                continue
        time.sleep(0.2)
    return False


def cmd_probe_thread_read(group_id: Optional[int] = None):
    """Print Redux shapes related to thread/unread (for debugging mark-threads-read)."""
    s = connect()
    gid_js = "null" if group_id is None else str(int(group_id))
    r = s.evaluate(PROBE_THREAD_READ_JS % gid_js)
    s.close()
    if isinstance(r, dict) and r.get("__error__"):
        print(f"ERROR: {r['__error__']}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(r, indent=2, ensure_ascii=False))


def cmd_mark_threads_read(
    group_id: int,
    max_threads: Optional[int] = None,
    scroll_steps: int = 80,
    dwell_ms: int = 400,
    sync_timeout_sec: float = 25.0,
    only_unread: bool = False,
):
    """Open thread; sync until Redux threadInfo unread counts hit 0, then close."""
    s = connect()
    _ensure_pick_scroller(s)
    _ensure_group(s, group_id)
    time.sleep(0.4)
    raw = s.evaluate(THREAD_LIST_JS % str(group_id))
    if isinstance(raw, dict) and "__error__" in raw:
        print(f"ERROR: {raw['__error__']}", file=sys.stderr)
        s.close()
        sys.exit(1)
    if not isinstance(raw, list):
        raw = []
    seen: set[str] = set()
    threads: List[dict] = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        mid = t.get("rootMid")
        if not mid or mid in seen:
            continue
        seen.add(str(mid))
        threads.append(t)
    threads.sort(
        key=lambda x: (x.get("lastMessageTime") or x.get("createTime") or 0),
        reverse=True,
    )
    if only_unread:
        before = len(threads)
        threads = [
            x
            for x in threads
            if int(x.get("unreadReplyCount") or 0) > 0
            or int(x.get("unreadMentionCount") or 0) > 0
        ]
        print(
            f"# --only-unread: {len(threads)} with unread (of {before} thread roots)",
            file=sys.stderr,
        )
    if max_threads is not None and max_threads > 0:
        threads = threads[:max_threads]
    print(f"# {len(threads)} thread roots to open (group {group_id})", file=sys.stderr)
    ok, fail = 0, 0
    for t in threads:
        mid = str(t.get("rootMid", ""))
        s.evaluate(FIND_MESSAGE_SCROLL_CONTAINER_JS)
        if not _ensure_mid_visible_with_scroll(s, mid, max_steps=scroll_steps):
            print(f"WARNING: {mid}: not in DOM after scrolling (try larger --scroll-steps)", file=sys.stderr)
            fail += 1
            continue
        s.evaluate(SCROLL_TO_MID_JS % json.dumps(mid))
        time.sleep(0.12)
        r = s.evaluate(OPEN_THREAD_CLICK_JS % json.dumps(mid))
        if isinstance(r, dict) and "__error__" in r:
            print(f"WARNING: {mid}: {r['__error__']}", file=sys.stderr)
            fail += 1
            continue
        tid = f"group-{group_id}-{mid}"
        time.sleep(0.12)
        if not _wait_thread_redux_loaded(s, tid, timeout_sec=8.0):
            print(
                f"WARNING: {mid}: thread messages not in Redux after wait — read ack may not run",
                file=sys.stderr,
            )
        time.sleep(0.1)
        for attempt in range(5):
            sim = s.evaluate(THREAD_PANEL_SIMULATE_READ_JS)
            mc = 0
            if isinstance(sim, dict) and sim.get("ok") and isinstance(sim.get("report"), dict):
                mc = int(sim.get("report", {}).get("midCount") or 0)
            if mc > 0:
                break
            time.sleep(0.35)
        cleared = _sync_thread_unread_until_clear(
            s,
            int(group_id),
            mid,
            max_wait_sec=sync_timeout_sec,
            dwell_ms=dwell_ms,
        )
        if not cleared:
            print(
                f"WARNING: {mid}: threadInfo still shows unread after {sync_timeout_sec}s — closing anyway",
                file=sys.stderr,
            )
        r2 = s.evaluate(CLOSE_THREAD_CLICK_JS)
        if isinstance(r2, dict) and "__error__" in r2:
            print(f"WARNING: close {mid}: {r2['__error__']}", file=sys.stderr)
        time.sleep(0.3)
        ok += 1
    s.close()
    print(json.dumps({"ok": True, "openedOk": ok, "openFailed": fail, "total": len(threads)}, indent=2, ensure_ascii=False))


CACHED_SESSIONS_JS = r"""
(function() {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var lists = store.getState().messages.lists;
  var groups = [];
  var buddies = [];
  var keys = Object.keys(lists);
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    var ids = lists[k];
    var count = ids ? ids.length : 0;
    if (k.indexOf('group-') === 0) {
      var parts = k.split('-');
      if (parts.length === 2) groups.push({id: parseInt(parts[1], 10), messages: count});
    } else if (k.indexOf('buddy-') === 0) {
      var parts = k.split('-');
      if (parts.length === 2) buddies.push({id: parseInt(parts[1], 10), messages: count});
    }
  }
  return {groups: groups, buddies: buddies};
})()
"""


SAVE_SESSION_JS = r"""
(function() {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var ms = store.getState().messages;
  var sel = ms.selectedSession;
  var tid = ms.currentThreadId || null;
  if (!sel) return {__error__: 'no selectedSession'};
  return {type: sel.type || null, id: sel.id || null, threadId: tid};
})()
"""


def cmd_cached_sessions():
    """List session keys with cached message data in Redux (no UI interaction)."""
    s = connect()
    result = getattr(s, 'evaluate')(CACHED_SESSIONS_JS)
    s.close()
    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_save_session():
    """Print the currently selected session as JSON (no UI interaction)."""
    s = connect()
    result = getattr(s, 'evaluate')(SAVE_SESSION_JS)
    s.close()
    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_restore_session(session_type: str, session_id: int):
    """Switch back to a previously saved session (UI interaction: clicks sidebar)."""
    if session_type == "group":
        cmd_switch_chat(session_id)
    elif session_type == "buddy":
        cmd_switch_buddy(session_id)
    else:
        print(f"ERROR: unknown session type '{session_type}'", file=sys.stderr)
        sys.exit(1)


def cmd_switch_chat(group_id: int):
    """Switch SeaTalk UI to the specified group chat."""
    s = connect()
    result = s.evaluate(SWITCH_CHAT_JS % str(group_id))
    if isinstance(result, dict) and result.get("__need_search__"):
        result = _switch_via_search(s, group_id)
    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        s.close()
        sys.exit(1)
    time.sleep(0.5)
    session = s.evaluate("window.store.getState().messages.selectedSession")
    s.close()
    actual_id = session.get("id") if isinstance(session, dict) else None
    if actual_id != group_id:
        print(f"WARNING: selectedSession is {actual_id}, expected {group_id}", file=sys.stderr)
    group_name = result.get("groupName", str(group_id))
    print(f"# Switched to: {group_name} ({group_id})", file=sys.stderr)
    print(json.dumps({"ok": True, "groupId": group_id, "groupName": group_name}))


SWITCH_BUDDY_JS = r"""
(function(buddyId) {
  var store = window.store;
  if (!store) return {__error__: 'no store'};
  var el = document.querySelector('.messages-chat-session-list-item[data-id="' + buddyId + '"]');
  if (el) {
    var onclick = el.querySelector('.onclick-wrapper');
    (onclick || el).click();
    var u = store.getState().contact.userInfo[buddyId];
    return {ok: true, buddyId: buddyId, buddyName: u ? (u.name || u.nickname || String(buddyId)) : String(buddyId)};
  }
  return {__need_search__: true, buddyId: buddyId};
})(%s)
"""


def cmd_switch_buddy(buddy_id: int):
    """Switch SeaTalk UI to a private chat (buddy)."""
    s = connect()
    result = s.evaluate(SWITCH_BUDDY_JS % str(buddy_id))
    if isinstance(result, dict) and result.get("__need_search__"):
        ui = s.evaluate(f"(function(){{ var u = window.store.getState().contact.userInfo[{buddy_id}]; return u ? (u.name || u.nickname) : null; }})()")
        search_text = ui if ui else str(buddy_id)
        r = s.evaluate(SWITCH_CHAT_SEARCH_JS % json.dumps(search_text))
        if not (isinstance(r, dict) and r.get("__error__")):
            time.sleep(1.5)
            result = s.evaluate(SWITCH_CHAT_CLICK_RESULT_JS % str(buddy_id))
    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        s.close()
        sys.exit(1)
    time.sleep(0.5)
    session = s.evaluate("window.store.getState().messages.selectedSession")
    s.close()
    actual = session if isinstance(session, dict) else {}
    buddy_name = result.get("buddyName", str(buddy_id))
    print(f"# Switched to buddy: {buddy_name} ({buddy_id})", file=sys.stderr)
    print(json.dumps({"ok": True, "buddyId": buddy_id, "buddyName": buddy_name}))


def _cdp_click(s: CDPSession, x: float, y: float):
    """Click at absolute page coordinates using CDP Input.dispatchMouseEvent."""
    s.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
    s.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})


def cmd_open_thread(group_id: int, message_id: str):
    """Open a thread panel for the given message (switches group first)."""
    s = connect()
    _ensure_group(s, group_id)

    result = s.evaluate(OPEN_THREAD_CLICK_JS % json.dumps(message_id))
    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        s.close()
        sys.exit(1)

    expected = f"group-{group_id}-{message_id}"
    tid = None
    for _ in range(6):
        time.sleep(0.5)
        tid = s.evaluate("window.store.getState().messages.currentThreadId")
        if tid == expected:
            break
    s.close()
    if tid != expected:
        print(f"WARNING: currentThreadId is {tid}, expected {expected}", file=sys.stderr)
    print(f"# Thread opened: {tid or expected}", file=sys.stderr)
    print(json.dumps({"ok": True, "threadId": tid or expected}))


def cmd_close_thread():
    """Close the currently open thread panel."""
    s = connect()
    tid = s.evaluate("window.store.getState().messages.currentThreadId")
    if not tid:
        print("No thread panel is currently open.", file=sys.stderr)
        s.close()
        sys.exit(0)

    result = s.evaluate(CLOSE_THREAD_CLICK_JS)
    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        s.close()
        sys.exit(1)

    time.sleep(0.5)
    after = s.evaluate("window.store.getState().messages.currentThreadId")
    s.close()
    if after:
        print(f"WARNING: thread still open after close: {after}", file=sys.stderr)
    else:
        print(f"# Closed thread: {tid}", file=sys.stderr)
    print(json.dumps({"ok": True, "closedThreadId": tid}))


def cmd_reply_thread(group_id: int, message_id: str, text: str):
    """Switch to group, open thread, send a message. Controlled by ALLOW_SEND."""
    if not ALLOW_SEND:
        print(
            "ERROR: send is disabled. Set SEATALK_ALLOW_SEND=true to enable.",
            file=sys.stderr,
        )
        sys.exit(1)

    s = connect()

    _ensure_group(s, group_id)

    r = s.evaluate(OPEN_THREAD_CLICK_JS % json.dumps(message_id))
    if isinstance(r, dict) and "__error__" in r:
        print(f"ERROR opening thread: {r['__error__']}", file=sys.stderr)
        s.close()
        sys.exit(1)
    print(f"# Opened thread for message {message_id}", file=sys.stderr)

    expected = f"group-{group_id}-{message_id}"
    for _ in range(6):
        time.sleep(0.5)
        tid = s.evaluate("window.store.getState().messages.currentThreadId")
        if tid == expected:
            break

    segments = _parse_send_segments(text)
    target = ".ant-drawer.thread-detail-panel"
    js = SEND_MESSAGE_JS % (json.dumps(segments) + ", " + json.dumps(target))
    result = s.evaluate(js)
    s.close()

    if isinstance(result, dict) and result.get("error"):
        print(f"ERROR sending: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"# Replied in thread: {expected}", file=sys.stderr)
    print(json.dumps({"ok": True, "threadId": expected}))


def cmd_current_thread(output_file: Optional[str] = None, limit: Optional[int] = None):
    """Read messages from the currently opened thread in SeaTalk UI."""
    s = connect()
    tid = s.evaluate("window.store && window.store.getState().messages.currentThreadId")
    if not tid:
        print("No thread is currently open in SeaTalk UI.", file=sys.stderr)
        s.close()
        sys.exit(1)

    print(f"# Current thread: {tid}", file=sys.stderr)
    js = THREAD_MESSAGES_JS % json.dumps(tid)
    result = s.evaluate(js)
    s.close()

    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        sys.exit(1)

    if isinstance(result, dict) and result.get("__not_loaded__"):
        print(f"Thread {tid} messages not yet loaded. Try scrolling in the thread.", file=sys.stderr)
        sys.exit(1)

    _output_messages(result, output_file, limit)


def cmd_current_group(output_file: Optional[str] = None, limit: Optional[int] = None):
    """Read messages from the currently selected group in SeaTalk UI."""
    s = connect()
    session = s.evaluate("window.store && window.store.getState().messages.selectedSession")
    if not session or not isinstance(session, dict) or session.get("type") != "group":
        print("No group chat is currently selected in SeaTalk UI.", file=sys.stderr)
        s.close()
        sys.exit(1)

    gid = session["id"]
    gi = s.evaluate(f"(function(){{ var g = window.store.getState().contact.groupInfo[{gid}]; return g ? g.name : null; }})()")
    label = f"{gi} ({gid})" if gi else str(gid)
    print(f"# Current group: {label}", file=sys.stderr)

    js = READ_MESSAGES_JS % str(gid)
    result = s.evaluate(js)
    s.close()

    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        sys.exit(1)

    _output_messages(result, output_file, limit)


def cmd_current_chat(output_file: Optional[str] = None, limit: Optional[int] = None):
    """Read messages from the currently selected chat (group or private) in SeaTalk UI."""
    s = connect()
    session = s.evaluate("window.store && window.store.getState().messages.selectedSession")
    if not session or not isinstance(session, dict):
        print("No chat is currently selected in SeaTalk UI.", file=sys.stderr)
        s.close()
        sys.exit(1)

    stype = session.get("type", "")
    sid = session["id"]

    if stype == "group":
        gi = s.evaluate(f"(function(){{ var g = window.store.getState().contact.groupInfo[{sid}]; return g ? g.name : null; }})()")
        label = f"{gi} ({sid})" if gi else str(sid)
        print(f"# Current group: {label}", file=sys.stderr)
        js = READ_MESSAGES_JS % str(sid)
    elif stype == "buddy":
        ui = s.evaluate(f"(function(){{ var u = window.store.getState().contact.userInfo[{sid}]; return u ? (u.name || u.nickname || String({sid})) : String({sid}); }})()")
        label = f"{ui} ({sid})" if ui else str(sid)
        print(f"# Current private chat: {label}", file=sys.stderr)
        js = READ_BUDDY_MESSAGES_JS % str(sid)
    else:
        print(f"Unsupported session type: {stype}", file=sys.stderr)
        s.close()
        sys.exit(1)

    result = s.evaluate(js)
    s.close()

    if isinstance(result, dict) and "__error__" in result:
        print(f"ERROR: {result['__error__']}", file=sys.stderr)
        sys.exit(1)

    _output_messages(result, output_file, limit)


SEND_MESSAGE_JS = r"""
(function(segments, targetSelector) {
  var container = targetSelector ? document.querySelector(targetSelector) : document;
  if (targetSelector && !container) return {error: 'target container not found: ' + targetSelector};

  var el = (container || document).querySelector('.ProseMirror.seatalk-editor-content');
  if (!el) return {error: 'editor not found' + (targetSelector ? ' in ' + targetSelector : '')};
  var editor = el.editor;
  if (!editor || !editor.view) return {error: 'no editor view'};
  var pmView = editor.view;
  var schema = pmView.state.schema;

  var store = window.store;
  var state = store.getState();
  var ui = state.contact.userInfo;
  var sel = state.messages.selectedSession;
  var members = (sel && sel.type === 'group') ? (state.contact.groupMembers[sel.id] || []) : [];
  var nameToId = {};
  for (var i = 0; i < members.length; i++) {
    var u = ui[members[i].id];
    if (u && u.name) nameToId[u.name] = members[i].id;
  }

  var nodes = [];
  for (var s = 0; s < segments.length; s++) {
    var seg = segments[s];
    if (seg.type === 'mention') {
      var uid = seg.id || nameToId[seg.label];
      if (!uid) return {error: 'cannot resolve mention: ' + seg.label};
      nodes.push(schema.nodes.mention.create({key: String(uid), label: seg.label, type: 'user'}));
      nodes.push(schema.text(' '));
    } else {
      nodes.push(schema.text(seg.text));
    }
  }

  if (nodes.length === 0) return {error: 'empty message'};

  var tr = pmView.state.tr;
  for (var n = 0; n < nodes.length; n++) {
    tr = tr.replaceSelectionWith(nodes[n], false);
  }
  pmView.dispatch(tr);
  pmView.focus();

  var ev = new KeyboardEvent('keydown', {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true});
  el.dispatchEvent(ev);

  return {ok: true};
})(%s)
"""


def _parse_send_segments(text: str) -> list:
    """Parse text with @Name mentions into segments for the JS side.

    Supports: @Name (space-delimited) and @{Name With Spaces}.
    """
    segments: list = []
    pattern = re.compile(r'@\{([^}]+)\}|@(\S+)')
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            segments.append({"type": "text", "text": text[last:m.start()]})
        label = m.group(1) or m.group(2)
        segments.append({"type": "mention", "label": label})
        last = m.end()
    if last < len(text):
        segments.append({"type": "text", "text": text[last:]})
    return segments


def cmd_send(text: str):
    """Compose and send a message in the active SeaTalk editor, with @mention support."""
    if not ALLOW_SEND:
        print(
            "ERROR: send is disabled. Set SEATALK_ALLOW_SEND=true to enable.\n"
            "  In seatalk-listener.conf or environment:\n"
            "    export SEATALK_ALLOW_SEND=true",
            file=sys.stderr,
        )
        sys.exit(1)

    s = connect()

    context = s.evaluate(r"""
    (() => {
        var store = window.store;
        if (!store) return {error: 'no store'};
        var ms = store.getState().messages;
        var sel = ms.selectedSession;
        var tid = ms.currentThreadId;
        var gi = store.getState().contact.groupInfo;
        var groupName = '';
        if (sel && sel.type === 'group') {
            var g = gi[sel.id];
            groupName = g ? g.name : String(sel.id);
        }
        return {session: sel, threadId: tid || null, groupName: groupName};
    })()
    """)
    if isinstance(context, dict) and context.get("error"):
        print(f"ERROR: {context['error']}", file=sys.stderr)
        s.close()
        sys.exit(1)

    session = context.get("session")
    thread_id = context.get("threadId")
    group_name = context.get("groupName", "")
    if thread_id:
        print(f"# Sending to thread: {thread_id} in {group_name}", file=sys.stderr)
    elif session:
        print(f"# Sending to group: {group_name} ({session.get('id', '?')})", file=sys.stderr)
    else:
        print("ERROR: no active chat session in SeaTalk UI.", file=sys.stderr)
        s.close()
        sys.exit(1)

    segments = _parse_send_segments(text)
    target = ".ant-drawer.thread-detail-panel" if thread_id else None
    js = SEND_MESSAGE_JS % (json.dumps(segments) + ", " + json.dumps(target))
    result = s.evaluate(js)
    s.close()

    if isinstance(result, dict) and result.get("error"):
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print("Sent.", file=sys.stderr)


EXTRACT_IMAGE_URL_JS = r"""
(function(mid, sessionKey) {
  // Try finding the element by data-mid
  var el = document.querySelector('[data-mid="' + mid + '"]');

  // If not found, try to resolve the server-assigned mid from Redux
  if (!el && sessionKey) {
    var ms = window.store.getState().messages;
    var msg = ms.messages[sessionKey + '-' + mid];
    if (msg) {
      // The message might have been replaced with a server-assigned ID
      var serverMid = msg.receiverMsgId || msg.id;
      if (serverMid && serverMid !== mid) {
        el = document.querySelector('[data-mid="' + serverMid + '"]');
        if (el) mid = serverMid;
      }
    }
    // Also scan the list for the real mid if the original was a client-side cid
    if (!el) {
      var list = ms.lists[sessionKey] || [];
      for (var i = list.length - 1; i >= Math.max(0, list.length - 10); i--) {
        var candidate = ms.messages[sessionKey + '-' + list[i]];
        if (candidate && (candidate.cid === mid || candidate.senderMsgId === mid)) {
          el = document.querySelector('[data-mid="' + list[i] + '"]');
          if (el) { mid = list[i]; break; }
        }
      }
    }
  }

  if (!el) return {found: false, reason: 'message not in DOM', mid: mid};

  // Find the actual image, skipping avatars and badges
  var allImgs = el.querySelectorAll('img[src*="f.haiserve.com"]');
  var bestImg = null;
  var bestArea = 0;
  for (var k = 0; k < allImgs.length; k++) {
    var candidate = allImgs[k];
    // Skip images inside avatar containers
    if (candidate.closest('.avatar-box')) continue;
    if (candidate.classList.contains('avatar-badge')) continue;
    var area = (candidate.naturalWidth || 1) * (candidate.naturalHeight || 1);
    if (area > bestArea) { bestArea = area; bestImg = candidate; }
  }
  if (!bestImg && allImgs.length > 0) {
    // Fallback: pick the largest image even if in avatar-box
    for (var k2 = 0; k2 < allImgs.length; k2++) {
      var a2 = (allImgs[k2].naturalWidth || 1) * (allImgs[k2].naturalHeight || 1);
      if (a2 > bestArea) { bestArea = a2; bestImg = allImgs[k2]; }
    }
  }
  if (!bestImg) {
    var blobImg = el.querySelector('img[src^="blob:"]');
    if (blobImg) return {found: false, reason: 'image still uploading (blob URL)', mid: mid};
    return {found: false, reason: 'no image element in message', mid: mid};
  }
  return {found: true, url: bestImg.src, mid: mid};
})(%s)
"""

SEATALK_IMAGE_DIR = os.environ.get("SEATALK_IMAGE_DIR", "/tmp/seatalk-images")


def _download_image(url: str, mid: str) -> Optional[str]:
    """Download an image from f.haiserve.com to local disk. Returns local path or None.

    Strips the thumbnail size suffix (e.g. _80) from the URL to fetch the
    original resolution image.
    """
    os.makedirs(SEATALK_IMAGE_DIR, exist_ok=True)

    orig_url = re.sub(r'_\d+\?', '?', url)

    try:
        req = urllib.request.Request(orig_url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "")

        if len(data) < 100:
            body = data.decode("utf-8", errors="ignore")
            if "File Not Exist" in body or "ErrorCode" in body:
                return None

        if data[:4] == b'\x89PNG':
            ext = ".png"
        elif data[:2] == b'\xff\xd8':
            ext = ".jpg"
        elif data[:4] == b'GIF8':
            ext = ".gif"
        elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            ext = ".webp"
        elif "jpeg" in content_type or "jpg" in content_type:
            ext = ".jpg"
        elif "png" in content_type:
            ext = ".png"
        else:
            ext = ".png"

        local_path = os.path.join(SEATALK_IMAGE_DIR, f"{mid}{ext}")
        with open(local_path, "wb") as f:
            f.write(data)
        os.chmod(local_path, 0o600)
        return local_path
    except (urllib.error.URLError, OSError) as e:
        print(f"# Image download failed for {mid}: {e}", file=sys.stderr)
        return None


def _download_image_via_fileid(s: CDPSession, msg: dict) -> Optional[str]:
    """Fallback: download image using fileId from Redux + auth token from any visible img."""
    image_info = msg.get("imageInfo", {})
    file_id = image_info.get("fileId", "")
    if not file_id or file_id.startswith("blob:"):
        return None
    mid = msg.get("mid", "")
    session_key = msg.get("sessionKey", "")

    token_info = s.evaluate(r"""
(function() {
  var img = document.querySelector('img[src*="f.haiserve.com"]');
  if (!img) return null;
  var url = new URL(img.src);
  return {token: url.searchParams.get('token'), userid: url.searchParams.get('userid')};
})()
""")
    if not isinstance(token_info, dict) or not token_info.get("token"):
        return None

    url = (f"https://f.haiserve.com/download/{file_id}"
           f"?userid={token_info['userid']}&token={token_info['token']}")
    return _download_image(url, mid)


def _try_extract_image(s: CDPSession, msg: dict, retries: int = 6, delay: float = 2.0) -> Optional[str]:
    """Try to extract image URL from DOM for an image message, with retries for render/upload lag.
    Falls back to direct fileId download if DOM extraction fails."""
    mid = msg.get("mid", "")
    session_key = msg.get("sessionKey", "")
    js_args = json.dumps(mid) + ", " + json.dumps(session_key)

    for attempt in range(retries):
        result = s.evaluate(EXTRACT_IMAGE_URL_JS % js_args)
        if isinstance(result, dict) and result.get("found"):
            url = result["url"]
            resolved_mid = result.get("mid", mid)
            local_path = _download_image(url, resolved_mid)
            if local_path:
                return local_path
            return url
        reason = result.get("reason", "") if isinstance(result, dict) else ""
        if "blob URL" in reason:
            print(f"# Image {mid}: still uploading, retry {attempt+1}/{retries}", file=sys.stderr)
        if attempt < retries - 1:
            time.sleep(delay)

    # Re-read imageInfo from Redux (fileId may have been updated after upload)
    refreshed = s.evaluate(r"""
(function(sk, mid) {
  var ms = window.store.getState().messages;
  var msg = ms.messages[sk + '-' + mid];
  if (!msg) {
    var list = ms.lists[sk] || [];
    for (var i = list.length - 1; i >= Math.max(0, list.length - 10); i--) {
      var c = ms.messages[sk + '-' + list[i]];
      if (c && (c.cid === mid || c.senderMsgId === mid)) { msg = c; mid = list[i]; break; }
    }
  }
  if (!msg || msg.tag !== 'image' || !msg.content) return null;
  return {mid: mid, fileId: msg.content.fileId || '', width: msg.content.width, height: msg.content.height, size: msg.content.size};
})(%s, %s)
""" % (json.dumps(session_key), json.dumps(mid)))
    if isinstance(refreshed, dict) and refreshed.get("fileId"):
        msg = dict(msg)
        msg["mid"] = refreshed.get("mid", mid)
        msg["imageInfo"] = refreshed

    print(f"# Image {mid}: DOM extraction failed, trying fileId fallback", file=sys.stderr)
    fallback = _download_image_via_fileid(s, msg)
    if fallback:
        print(f"# Image {mid}: fileId fallback succeeded", file=sys.stderr)
    return fallback


LISTEN_HEALTH_INTERVAL = int(os.environ.get("SEATALK_LISTEN_HEALTH_INTERVAL", "30"))
LISTEN_RETRY_INITIAL = float(os.environ.get("SEATALK_LISTEN_RETRY_INITIAL", "2"))
LISTEN_RETRY_MAX = float(os.environ.get("SEATALK_LISTEN_RETRY_MAX", "120"))
LISTEN_RETRY_MULT = float(os.environ.get("SEATALK_LISTEN_RETRY_MULT", "2"))


def _listen_setup(s: CDPSession, group_ids: Optional[List[int]]) -> bool:
    """Inject JS listener, set watch/admin filters. Returns True on success."""
    _ensure_pick_scroller(s)
    res = s.evaluate(INJECT_LISTENER_JS)
    if isinstance(res, dict) and "__error__" in res:
        print(f"# ERROR installing listener: {res['__error__']}", file=sys.stderr)
        return False
    print(f"# Listener: {res}", file=sys.stderr)

    if group_ids:
        res2 = s.evaluate(SET_WATCH_JS % json.dumps(group_ids))
        print(f"# Watch: {res2}", file=sys.stderr)
    else:
        print("# Watch: all groups", file=sys.stderr)

    res3 = s.evaluate(SET_ADMINS_JS % json.dumps(ADMIN_IDS if ADMIN_IDS else []))
    print(f"# {res3}", file=sys.stderr)
    return True


def cmd_listen(group_ids: Optional[List[int]] = None, poll_sec: float = DEFAULT_POLL_SEC,
             since_ts: Optional[float] = None):
    """Subscribe to Redux store changes and stream new messages as JSON-lines to stdout.

    Reconnects automatically on any error with exponential backoff.
    Periodically health-checks the JS listener and re-injects if stale.
    Filters out messages with timestamp < since_ts (strict) to avoid replaying old messages.
    After CDP reconnect or listener re-inject, also applies replay guards (see SKILL.md).
    """
    admin_set = set(ADMIN_IDS) if ADMIN_IDS else None
    current_since_ts = since_ts
    backoff = LISTEN_RETRY_INITIAL
    reconnect_count = 0
    s: Optional[CDPSession] = None

    try:
        had_watermark = since_ts is not None and float(since_ts) > 0
    except (TypeError, ValueError):
        had_watermark = False
    skip_first_large_batch = True
    initial_batch_skip_threshold = int(os.environ.get("SEATALK_INITIAL_BATCH_SKIP_THRESHOLD", "12"))
    replay_fresh_sec = float(os.environ.get("SEATALK_REPLAY_FRESH_SEC", "180"))

    print(f"# Polling every {poll_sec}s, Ctrl-C to stop", file=sys.stderr)
    print(f"# Health check every {LISTEN_HEALTH_INTERVAL} polls", file=sys.stderr)
    print(f"# Image download dir: {SEATALK_IMAGE_DIR}", file=sys.stderr)

    def _close_quiet(session: Optional[CDPSession]):
        if session:
            try:
                session.close()
            except Exception:
                pass

    def _connect_and_setup() -> CDPSession:
        nonlocal backoff, reconnect_count, skip_first_large_batch
        while True:
            try:
                sess = connect()
                if not _listen_setup(sess, group_ids):
                    _close_quiet(sess)
                    raise RuntimeError("listener setup failed")
                backoff = LISTEN_RETRY_INITIAL
                skip_first_large_batch = True
                if reconnect_count > 0:
                    print(f"# Reconnected (attempt #{reconnect_count})", file=sys.stderr)
                return sess
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                reconnect_count += 1
                jitter = random.uniform(0, backoff * 0.3)
                wait = backoff + jitter
                print(f"# Connect failed: {exc}  (retry #{reconnect_count} in {wait:.1f}s)", file=sys.stderr)
                time.sleep(wait)
                backoff = min(backoff * LISTEN_RETRY_MULT, LISTEN_RETRY_MAX)

    try:
        s = _connect_and_setup()
        poll_count = 0

        while True:
            try:
                time.sleep(poll_sec)
            except KeyboardInterrupt:
                raise
            poll_count += 1

            try:
                if poll_count % LISTEN_HEALTH_INTERVAL == 0:
                    alive = s.evaluate("window.__ST_CDP_LISTENER__ === true")
                    if alive is not True:
                        print("# Health: listener stale, re-injecting...", file=sys.stderr)
                        if not _listen_setup(s, group_ids):
                            raise RuntimeError("re-inject failed")
                        # Re-inject clears __ST_CDP_SEEN__ — same replay risk as TCP reconnect.
                        skip_first_large_batch = True

                batch = s.evaluate(DRAIN_QUEUE_JS)

            except KeyboardInterrupt:
                raise
            except Exception as exc:
                reconnect_count += 1
                print(f"# Poll error: {exc}  (reconnecting...)", file=sys.stderr)
                _close_quiet(s)
                s = _connect_and_setup()
                poll_count = 0
                continue

            if not batch:
                continue
            if isinstance(batch, dict) and "__error__" in batch:
                print(f"# Drain error: {batch['__error__']}, reconnecting...", file=sys.stderr)
                _close_quiet(s)
                reconnect_count += 1
                s = _connect_and_setup()
                poll_count = 0
                continue

            if not isinstance(batch, list):
                print(f"# Drain: unexpected batch type {type(batch)}, skipping", file=sys.stderr)
                continue

            batch_is_replay_storm = skip_first_large_batch and len(batch) > initial_batch_skip_threshold
            if batch_is_replay_storm:
                print(
                    f"# Replay guard: large first batch ({len(batch)} msgs > {initial_batch_skip_threshold}); "
                    f"only forwarding messages newer than ~{replay_fresh_sec:.0f}s (SEATALK_REPLAY_FRESH_SEC)",
                    file=sys.stderr,
                )

            now_wall = time.time()
            skip_first_large_batch = False

            for msg in batch:
                if not _sender_allowed(msg.get("senderId"), admin_set):
                    continue

                # Filter out messages already forwarded (timestamp-based dedup).
                # Use strict < only: same-second messages share one timestamp; <= would drop bursts.
                msg_ts = msg.get("timestamp") or 0
                try:
                    msg_ts = float(msg_ts)
                except (TypeError, ValueError):
                    msg_ts = 0.0
                msg_ts_sec = _timestamp_to_unix_seconds(msg_ts)
                # After we have a last_forwarded_ts watermark, drop msgs with no server time — they used
                # to resolve to Date.now() in JS and bypassed dedup (historical replay after reconnect).
                if msg_ts <= 0 and had_watermark:
                    continue
                if batch_is_replay_storm and msg_ts_sec > 0 and (now_wall - msg_ts_sec) > replay_fresh_sec:
                    continue
                if msg_ts > 0 and current_since_ts is not None and msg_ts < current_since_ts:
                    continue
                if msg_ts > (current_since_ts or 0):
                    current_since_ts = msg_ts

                if msg.get("tag") == "image" and msg.get("imageInfo"):
                    try:
                        local_path = _try_extract_image(s, msg)
                    except Exception as img_err:
                        local_path = None
                        print(f"# Image extract error: {img_err}", file=sys.stderr)
                    if local_path:
                        msg["imagePath"] = local_path
                        print(f"# Image saved: {local_path}", file=sys.stderr)
                    else:
                        print(f"# Image extraction failed for mid={msg.get('mid')}", file=sys.stderr)

                line = json.dumps(msg, ensure_ascii=False)
                print(line, flush=True)

    except KeyboardInterrupt:
        print("\n# Stopped", file=sys.stderr)
    finally:
        _close_quiet(s)


# ── Main ───────────────────────────────────────────────────────────

def _parse_since_ts(args: List[str]) -> Optional[float]:
    """Extract --since-ts EPOCH from args. Also checks SEATALK_LAST_FORWARDED_TS env."""
    for i, a in enumerate(args):
        if a == '--since-ts' and i + 1 < len(args):
            try:
                return float(args[i + 1])
            except ValueError:
                return None
    raw = os.environ.get('SEATALK_LAST_FORWARDED_TS', '').strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return None


def _parse_group_ids(args: List[str]) -> Optional[List[int]]:
    """Extract --group IDs from args like ['--group', '188215,4172747'] or ['--group', '4172747']."""
    result = []
    i = 0
    while i < len(args):
        if args[i] in ("--group", "--groups") and i + 1 < len(args):
            for part in args[i + 1].split(","):
                part = part.strip()
                if part.isdigit():
                    result.append(int(part))
            i += 2
        else:
            if args[i].isdigit():
                result.append(int(args[i]))
            i += 1
    return result if result else None


def _parse_thread_id(args: List[str]) -> Optional[str]:
    """Extract --thread THREAD_ID from args."""
    for i, a in enumerate(args):
        if a == "--thread" and i + 1 < len(args):
            return args[i + 1]
    return args[0] if args else None


def _parse_output_opts(args: List[str]) -> tuple:
    """Extract --output FILE and --limit N from args. Returns (output_file, limit)."""
    output_file = None
    limit = None
    i = 0
    while i < len(args):
        if args[i] == "--output" and i + 1 < len(args):
            output_file = args[i + 1]
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        else:
            i += 1
    return output_file, limit


def _parse_groups_ids(args: List[str]) -> Optional[List[int]]:
    """Extract --groups IDs from args like ['--groups', '499098,1743938']."""
    for i, a in enumerate(args):
        if a == "--groups" and i + 1 < len(args):
            result = []
            for part in args[i + 1].split(","):
                part = part.strip()
                if part.isdigit():
                    result.append(int(part))
            return result if result else None
    return _parse_group_ids(args)


def _parse_message_id(args: List[str]) -> Optional[str]:
    """Extract --message MID from args."""
    for i, a in enumerate(args):
        if a == "--message" and i + 1 < len(args):
            return args[i + 1]
    return None


def _parse_buddy_id(args: List[str]) -> Optional[int]:
    """Extract --buddy BUDDY_ID from args."""
    for i, a in enumerate(args):
        if a == "--buddy" and i + 1 < len(args):
            v = args[i + 1].strip()
            if v.isdigit():
                return int(v)
    return None


def _parse_filter(args: List[str]) -> Optional[str]:
    """Extract --filter VALUE from args."""
    for i, a in enumerate(args):
        if a == "--filter" and i + 1 < len(args):
            return args[i + 1]
    return None


def _collect_positional(args: List[str]) -> List[str]:
    """Collect positional args (not --flag or their values)."""
    skip_next = False
    result = []
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a.startswith("--"):
            skip_next = True
            continue
        result.append(a)
    return result


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    rest = sys.argv[2:]
    out_file, limit = _parse_output_opts(rest)
    try:
        if cmd == "targets":
            cmd_targets()
        elif cmd == "explore":
            cmd_explore()
        elif cmd == "probe-thread-read":
            gids = _parse_group_ids(rest)
            cmd_probe_thread_read(gids[0] if gids else None)
        elif cmd == "eval":
            if len(sys.argv) < 3:
                print("Usage: cdp-reader.py eval \"expression\"", file=sys.stderr)
                sys.exit(1)
            cmd_eval(sys.argv[2])
        elif cmd == "read":
            gids = _parse_group_ids(rest)
            cmd_read(gids[0] if gids else None)
        elif cmd == "listen":
            def _graceful_exit(signum, frame):
                print(f"\n# Received signal {signum}, exiting gracefully...", file=sys.stderr)
                sys.exit(0)
            signal.signal(signal.SIGTERM, _graceful_exit)
            signal.signal(signal.SIGHUP, _graceful_exit)

            gids = _parse_group_ids(rest)
            since_ts = _parse_since_ts(rest)
            while True:
                try:
                    cmd_listen(gids, since_ts=since_ts)
                    break
                except (KeyboardInterrupt, SystemExit):
                    break
                except Exception as e:
                    print(f"# listen crashed: {e}, restarting in 5s...", file=sys.stderr)
                    try:
                        time.sleep(5)
                    except (KeyboardInterrupt, SystemExit):
                        break
        elif cmd == "threads":
            gids = _parse_group_ids(rest)
            cmd_threads(gids[0] if gids else None)
        elif cmd == "thread-messages":
            tid = _parse_thread_id(rest)
            if not tid:
                print("Usage: cdp-reader.py thread-messages --thread THREAD_ID", file=sys.stderr)
                sys.exit(1)
            cmd_thread_messages(tid, out_file, limit)
        elif cmd == "current-thread":
            cmd_current_thread(out_file, limit)
        elif cmd == "current-group":
            cmd_current_group(out_file, limit)
        elif cmd == "current-chat":
            cmd_current_chat(out_file, limit)
        elif cmd == "send":
            msg_parts = [a for a in rest if not a.startswith("--")]
            if not msg_parts:
                print("Usage: cdp-reader.py send \"message text\"", file=sys.stderr)
                sys.exit(1)
            cmd_send(" ".join(msg_parts))
        elif cmd == "unread":
            gids = _parse_group_ids(rest)
            cmd_unread(gids)
        elif cmd == "mark-all-read":
            cmd_mark_all_read()
        elif cmd == "mark-threads-read":
            gids = _parse_group_ids(rest)
            if not gids:
                print(
                    "Usage: cdp-reader.py mark-threads-read --group GROUP_ID [--max N] [--scroll-steps 80] "
                    "[--dwell-ms 400] [--sync-timeout-sec 25] [--only-unread]",
                    file=sys.stderr,
                )
                sys.exit(1)
            max_t: Optional[int] = None
            scroll_steps = 80
            dwell_ms = 400
            sync_timeout_sec = 25.0
            only_unread = "--only-unread" in rest
            for i, a in enumerate(rest):
                if a == "--max" and i + 1 < len(rest) and rest[i + 1].strip().isdigit():
                    max_t = int(rest[i + 1].strip())
                if a == "--scroll-steps" and i + 1 < len(rest) and rest[i + 1].strip().isdigit():
                    scroll_steps = max(5, int(rest[i + 1].strip()))
                if a == "--dwell-ms" and i + 1 < len(rest) and rest[i + 1].strip().isdigit():
                    dwell_ms = max(0, int(rest[i + 1].strip()))
                if a == "--sync-timeout-sec" and i + 1 < len(rest):
                    try:
                        sync_timeout_sec = max(3.0, float(rest[i + 1].strip()))
                    except ValueError:
                        pass
            cmd_mark_threads_read(
                gids[0], max_t, scroll_steps, dwell_ms, sync_timeout_sec, only_unread
            )
        elif cmd == "read-unread":
            gids = _parse_group_ids(rest)
            if not gids:
                print("Usage: cdp-reader.py read-unread --group GROUP_ID", file=sys.stderr)
                sys.exit(1)
            cmd_read_unread(gids[0], out_file, limit)
        elif cmd == "list-groups":
            f = _parse_filter(rest)
            cmd_list_groups(f)
        elif cmd == "read-buddy":
            bid = _parse_buddy_id(rest)
            if not bid:
                gids = _parse_group_ids(rest)
                bid = gids[0] if gids else None
            if not bid:
                print("Usage: cdp-reader.py read-buddy --buddy BUDDY_ID", file=sys.stderr)
                sys.exit(1)
            cmd_read_buddy(bid, out_file, limit)
        elif cmd == "cached-sessions":
            cmd_cached_sessions()
        elif cmd == "save-session":
            cmd_save_session()
        elif cmd == "restore-session":
            stype = None
            sid = None
            for i, a in enumerate(rest):
                if a == "--group" and i + 1 < len(rest) and rest[i + 1].strip().isdigit():
                    stype = "group"
                    sid = int(rest[i + 1].strip())
                elif a == "--buddy" and i + 1 < len(rest) and rest[i + 1].strip().isdigit():
                    stype = "buddy"
                    sid = int(rest[i + 1].strip())
            if not stype or not sid:
                print("Usage: cdp-reader.py restore-session --group ID | --buddy ID", file=sys.stderr)
                sys.exit(1)
            cmd_restore_session(stype, sid)
        elif cmd == "switch-chat":
            gids = _parse_group_ids(rest)
            if not gids:
                print("Usage: cdp-reader.py switch-chat --group GROUP_ID", file=sys.stderr)
                sys.exit(1)
            cmd_switch_chat(gids[0])
        elif cmd == "switch-buddy":
            bid = _parse_buddy_id(rest)
            if not bid:
                gids = _parse_group_ids(rest)
                bid = gids[0] if gids else None
            if not bid:
                print("Usage: cdp-reader.py switch-buddy --buddy BUDDY_ID", file=sys.stderr)
                sys.exit(1)
            cmd_switch_buddy(bid)
        elif cmd == "open-thread":
            gids = _parse_group_ids(rest)
            mid = _parse_message_id(rest)
            if not gids or not mid:
                print("Usage: cdp-reader.py open-thread --group GID --message MID", file=sys.stderr)
                sys.exit(1)
            cmd_open_thread(gids[0], mid)
        elif cmd == "close-thread":
            cmd_close_thread()
        elif cmd == "reply-thread":
            gids = _parse_group_ids(rest)
            mid = _parse_message_id(rest)
            positional = _collect_positional(rest)
            if not gids or not mid or not positional:
                print("Usage: cdp-reader.py reply-thread --group GID --message MID \"text\"", file=sys.stderr)
                sys.exit(1)
            cmd_reply_thread(gids[0], mid, " ".join(positional))
        else:
            print(f"Unknown command: {cmd}", file=sys.stderr)
            sys.exit(1)
    except ConnectionRefusedError:
        print(
            f"ERROR: Cannot connect to CDP at {CDP_HOST}:{CDP_PORT}\n"
            "  Start SeaTalk with (MUST use 'open -a' to avoid input focus issues):\n"
            f"    open -a SeaTalk --args --remote-debugging-port={CDP_PORT} --remote-allow-origins=*",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
