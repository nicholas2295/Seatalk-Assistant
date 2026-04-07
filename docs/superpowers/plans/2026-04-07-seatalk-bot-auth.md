# Seatalk Bot Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate from System Account webhooks + static bot token to Bot API with App ID + Secret OAuth, and add a `get_group_info` MCP tool.

**Architecture:** A new `auth.py` module fetches and caches an OAuth access token using App ID + Secret. All `seatalk_client.py` functions call `auth.get_token()` before hitting the bot API. Config drops `webhook_url`/`bot_token` in favour of `app_id`, `app_secret`, and per-group `group_id`.

**Tech Stack:** Python 3.11, httpx, mcp (FastMCP), respx (test mocking), pytest-asyncio

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `auth.py` | Create | Fetch + cache access token from Seatalk |
| `config.py` | Modify | New fields: `app_id`, `app_secret`, `group_id`; remove `webhook_url`, `bot_token` |
| `config.json` | Modify | Update credentials + group entries |
| `config.example.json` | Modify | Update schema documentation |
| `seatalk_client.py` | Modify | Replace webhook send, update fetch, add `get_group_info` |
| `seatalk_mcp_server.py` | Modify | Add `get_group_info` tool |
| `tests/test_config.py` | Modify | Update for new fields |
| `tests/test_seatalk_client.py` | Modify | Update for bot API; add auth + group_info tests |

---

## Task 1: Verify Seatalk Bot API Endpoints

Before writing any code, confirm the exact URLs for the four bot API calls. Log them in a comment at the top of `seatalk_client.py`.

- [ ] **Step 1: Open the Seatalk Developer Portal API reference**

  Navigate to `https://open.seatalk.io/docs` and find these four endpoints. Record the full URL and HTTP method for each:

  | Capability | Expected URL pattern | Method |
  |------------|---------------------|--------|
  | Get App Access Token | `/auth/app_access_token` | POST |
  | Send Message to Group Chat | `/v1/chat/group/send_message` or similar | POST |
  | Get Group Info | `/v1/chat/group/info` | GET |
  | Get Chat History | `/v1/chat/group/messages` | GET |

- [ ] **Step 2: Record confirmed URLs**

  Open `seatalk_client.py` and add/update the URL constants at the top once confirmed:

  ```python
  # Confirm all URLs against https://open.seatalk.io/docs before use
  _SEND_GROUP_URL = "https://openapi.seatalk.io/v1/chat/group/send_message"
  _FETCH_URL      = "https://openapi.seatalk.io/v1/chat/group/messages"
  _GROUP_INFO_URL = "https://openapi.seatalk.io/v1/chat/group/info"
  ```

  And record the token URL in `auth.py` (created in Task 2):

  ```python
  _TOKEN_URL = "https://openapi.seatalk.io/auth/app_access_token"
  ```

---

## Task 2: Update Config Schema

Remove `webhook_url` and `bot_token`. Add `app_id`, `app_secret` (top-level) and `group_id` (per group).

- [ ] **Step 1: Write failing tests for new config schema**

  Replace the entire contents of `tests/test_config.py`:

  ```python
  import json
  import pytest
  from config import load_config


  def _write(tmp_path, data):
      f = tmp_path / "config.json"
      f.write_text(json.dumps(data))
      return str(f)


  def _valid(tmp_path, **overrides):
      data = {
          "app_id": "my-app-id",
          "app_secret": "my-app-secret",
          "signing_secret": "my-signing-secret",
          "groups": {
              "my-team": {"group_id": "grp-abc123", "name": "My Team"}
          },
      }
      data.update(overrides)
      return _write(tmp_path, data)


  def test_load_config_success(tmp_path):
      config = load_config(_valid(tmp_path))

      assert config.app_id == "my-app-id"
      assert config.app_secret == "my-app-secret"
      assert config.signing_secret == "my-signing-secret"
      assert "my-team" in config.groups
      assert config.groups["my-team"].group_id == "grp-abc123"
      assert config.groups["my-team"].name == "My Team"


  def test_load_config_name_optional(tmp_path):
      data = {
          "app_id": "a", "app_secret": "b",
          "groups": {"my-team": {"group_id": "grp-abc123"}},
      }
      config = load_config(_write(tmp_path, data))

      assert config.groups["my-team"].name is None


  def test_load_config_missing_file():
      with pytest.raises(FileNotFoundError, match="config.json"):
          load_config("/nonexistent/config.json")


  def test_load_config_missing_app_id(tmp_path):
      data = {
          "app_secret": "b",
          "groups": {"my-team": {"group_id": "grp-abc123"}},
      }
      with pytest.raises(ValueError, match="app_id"):
          load_config(_write(tmp_path, data))


  def test_load_config_missing_app_secret(tmp_path):
      data = {
          "app_id": "a",
          "groups": {"my-team": {"group_id": "grp-abc123"}},
      }
      with pytest.raises(ValueError, match="app_secret"):
          load_config(_write(tmp_path, data))


  def test_load_config_missing_groups_key(tmp_path):
      data = {"app_id": "a", "app_secret": "b"}
      with pytest.raises(ValueError, match="groups"):
          load_config(_write(tmp_path, data))


  def test_load_config_empty_groups(tmp_path):
      data = {"app_id": "a", "app_secret": "b", "groups": {}}
      with pytest.raises(ValueError, match="at least one group"):
          load_config(_write(tmp_path, data))


  def test_load_config_group_missing_group_id(tmp_path):
      data = {"app_id": "a", "app_secret": "b", "groups": {"my-team": {}}}
      with pytest.raises(ValueError, match="group_id"):
          load_config(_write(tmp_path, data))


  def test_load_config_malformed_json(tmp_path):
      f = tmp_path / "config.json"
      f.write_text("{bad json")
      with pytest.raises(ValueError, match="invalid JSON"):
          load_config(str(f))
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  cd "/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge"
  python3.11 -m pytest tests/test_config.py -v
  ```

  Expected: multiple FAILED (attributes don't exist yet).

- [ ] **Step 3: Update `config.py`**

  Replace entire file:

  ```python
  import json
  from pathlib import Path
  from dataclasses import dataclass


  @dataclass
  class GroupConfig:
      group_id: str
      name: str | None = None


  @dataclass
  class Config:
      app_id: str
      app_secret: str
      groups: dict[str, GroupConfig]
      signing_secret: str | None = None


  def load_config(path: str | None = None) -> "Config":
      if path is None:
          path = str(Path(__file__).parent / "config.json")

      config_path = Path(path)
      if not config_path.exists():
          raise FileNotFoundError(
              f"Config file not found: {path}. "
              "Copy config.example.json to config.json and fill in your values."
          )

      with open(config_path) as f:
          try:
              data = json.load(f)
          except json.JSONDecodeError as exc:
              raise ValueError(f"config.json contains invalid JSON: {exc}") from exc

      if "app_id" not in data or not isinstance(data["app_id"], str) or not data["app_id"].strip():
          raise ValueError("config.json must contain a non-empty 'app_id'")

      if "app_secret" not in data or not isinstance(data["app_secret"], str) or not data["app_secret"].strip():
          raise ValueError("config.json must contain a non-empty 'app_secret'")

      if "groups" not in data or not isinstance(data["groups"], dict):
          raise ValueError("config.json must contain a 'groups' object")

      if not data["groups"]:
          raise ValueError("config.json must contain at least one group")

      groups = {}
      for key, group_data in data["groups"].items():
          if "group_id" not in group_data or not str(group_data["group_id"]).strip():
              raise ValueError(f"Group '{key}' is missing 'group_id'")
          groups[key] = GroupConfig(
              group_id=str(group_data["group_id"]),
              name=group_data.get("name"),
          )

      return Config(
          app_id=data["app_id"],
          app_secret=data["app_secret"],
          signing_secret=data.get("signing_secret"),
          groups=groups,
      )
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```bash
  python3.11 -m pytest tests/test_config.py -v
  ```

  Expected: all PASSED.

- [ ] **Step 5: Update `config.json` and `config.example.json`**

  `config.json` — replace contents (fill in your real values):

  ```json
  {
    "app_id": "<your-app-id>",
    "app_secret": "<your-app-secret>",
    "signing_secret": "C9lwQ2uJbiUO0LCZBhx95SAMbRBn-6O9",
    "groups": {
      "example-group": {
        "name": "JNT Test Group",
        "group_id": "<your-group-id>"
      }
    }
  }
  ```

  `config.example.json` — replace contents:

  ```json
  {
    "app_id": "<obtain from Seatalk Developer Portal — Basic Info & Credentials>",
    "app_secret": "<obtain from Seatalk Developer Portal — Basic Info & Credentials>",
    "signing_secret": "<obtain from Seatalk Developer Portal — optional, for future webhook verification>",
    "groups": {
      "example-group": {
        "name": "Human-readable group name (optional)",
        "group_id": "<Seatalk group ID — find via Get Group Info API or Developer Portal>"
      }
    }
  }
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add config.py tests/test_config.py config.example.json
  git commit -m "feat: update config schema for bot API (app_id, app_secret, group_id)"
  ```

---

## Task 3: Add `auth.py` — Token Fetch and Cache

- [ ] **Step 1: Write failing tests**

  Create `tests/test_auth.py`:

  ```python
  import time
  import pytest
  import respx
  import httpx
  from config import Config, GroupConfig
  import auth


  def make_config():
      return Config(
          app_id="test-app-id",
          app_secret="test-app-secret",
          groups={"g": GroupConfig(group_id="grp-1")},
      )


  TOKEN_URL = auth._TOKEN_URL


  @respx.mock
  async def test_get_token_success():
      respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
          "access_token": "tok-abc",
          "expire_in": 7200,
      }))

      token = await auth.get_token(make_config())

      assert token == "tok-abc"


  @respx.mock
  async def test_get_token_cached():
      respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
          "access_token": "tok-abc",
          "expire_in": 7200,
      }))

      config = make_config()
      await auth.get_token(config)
      await auth.get_token(config)

      assert respx.calls.call_count == 1  # only one HTTP call


  @respx.mock
  async def test_get_token_refreshes_when_expired(monkeypatch):
      respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
          "access_token": "tok-new",
          "expire_in": 7200,
      }))

      config = make_config()
      # Inject an expired cache entry
      auth._cache[id(config)] = auth._TokenEntry(token="tok-old", expires_at=time.monotonic() - 1)

      token = await auth.get_token(config)

      assert token == "tok-new"


  @respx.mock
  async def test_get_token_api_error():
      respx.post(TOKEN_URL).mock(return_value=httpx.Response(401, json={"message": "invalid credentials"}))

      with pytest.raises(RuntimeError, match="401"):
          await auth.get_token(make_config())
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  python3.11 -m pytest tests/test_auth.py -v
  ```

  Expected: ERROR — `auth` module not found.

- [ ] **Step 3: Create `auth.py`**

  ```python
  import time
  import httpx
  from dataclasses import dataclass
  from config import Config

  # Confirm URL against https://open.seatalk.io/docs
  _TOKEN_URL = "https://openapi.seatalk.io/auth/app_access_token"
  _TIMEOUT = 5.0
  _REFRESH_BUFFER = 60  # refresh this many seconds before actual expiry


  @dataclass
  class _TokenEntry:
      token: str
      expires_at: float  # monotonic time


  _cache: dict[int, _TokenEntry] = {}


  async def get_token(config: Config) -> str:
      """Return a valid access token, fetching or refreshing as needed."""
      entry = _cache.get(id(config))
      if entry and time.monotonic() < entry.expires_at - _REFRESH_BUFFER:
          return entry.token

      async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
          response = await client.post(
              _TOKEN_URL,
              json={"app_id": config.app_id, "app_secret": config.app_secret},
          )

      if response.status_code != 200:
          raise RuntimeError(
              f"Failed to get Seatalk access token: {response.status_code} {response.text}"
          )

      data = response.json()
      token = data["access_token"]
      expire_in = int(data.get("expire_in", 7200))
      _cache[id(config)] = _TokenEntry(token=token, expires_at=time.monotonic() + expire_in)

      return token
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```bash
  python3.11 -m pytest tests/test_auth.py -v
  ```

  Expected: all PASSED.

- [ ] **Step 5: Commit**

  ```bash
  git add auth.py tests/test_auth.py
  git commit -m "feat: add auth module with token fetch and in-memory cache"
  ```

---

## Task 4: Update `seatalk_client.py` — Bot API Calls

- [ ] **Step 1: Write failing tests**

  Replace entire `tests/test_seatalk_client.py`:

  ```python
  import pytest
  import respx
  import httpx
  from unittest.mock import AsyncMock, patch
  from config import Config, GroupConfig
  import seatalk_client

  GROUP_ID = "grp-abc123"
  SEND_URL = seatalk_client._SEND_GROUP_URL
  FETCH_URL = seatalk_client._FETCH_URL
  INFO_URL  = seatalk_client._GROUP_INFO_URL


  def make_config():
      return Config(
          app_id="app-id",
          app_secret="app-secret",
          groups={"my-team": GroupConfig(group_id=GROUP_ID, name="My Team")},
      )


  def mock_token():
      return patch("seatalk_client.auth.get_token", new=AsyncMock(return_value="test-token"))


  # --- send_message ---

  @respx.mock
  async def test_send_message_success():
      respx.post(SEND_URL).mock(return_value=httpx.Response(200, json={"code": 0}))

      with mock_token():
          result = await seatalk_client.send_message(make_config(), "my-team", "Hello")

      assert result == "Message sent successfully"


  async def test_send_message_unknown_group():
      with mock_token():
          result = await seatalk_client.send_message(make_config(), "no-such-group", "Hi")

      assert "no-such-group" in result
      assert "my-team" in result


  @respx.mock
  async def test_send_message_api_error():
      respx.post(SEND_URL).mock(return_value=httpx.Response(400, text="Bad Request"))

      with mock_token():
          result = await seatalk_client.send_message(make_config(), "my-team", "Hello")

      assert "400" in result


  @respx.mock
  async def test_send_message_retries_on_timeout():
      respx.post(SEND_URL).mock(side_effect=[
          httpx.TimeoutException("timeout"),
          httpx.Response(200, json={"code": 0}),
      ])

      with mock_token():
          result = await seatalk_client.send_message(make_config(), "my-team", "Hello")

      assert result == "Message sent successfully"


  @respx.mock
  async def test_send_message_timeout_after_retry():
      respx.post(SEND_URL).mock(side_effect=httpx.TimeoutException("timeout"))

      with mock_token():
          result = await seatalk_client.send_message(make_config(), "my-team", "Hello")

      assert "timed out" in result.lower() or "retry" in result.lower()


  # --- list_groups ---

  async def test_list_groups():
      result = await seatalk_client.list_groups(make_config())

      assert "my-team" in result
      assert "My Team" in result


  async def test_list_groups_empty():
      config = Config(app_id="a", app_secret="b", groups={})

      result = await seatalk_client.list_groups(config)

      assert result == "No groups configured"


  # --- fetch_messages ---

  async def test_fetch_messages_unknown_group():
      with mock_token():
          result = await seatalk_client.fetch_messages(make_config(), "no-such-group")

      assert "no-such-group" in result


  @respx.mock
  async def test_fetch_messages_success():
      respx.get(FETCH_URL).mock(return_value=httpx.Response(200, json={
          "messages": [
              {"sender_name": "Alice", "content": "Hello", "timestamp": "2026-04-07T10:00:00Z"},
              {"sender_name": "Bob",   "content": "Hi",    "timestamp": "2026-04-07T10:01:00Z"},
          ]
      }))

      with mock_token():
          result = await seatalk_client.fetch_messages(make_config(), "my-team", limit=10)

      assert "Alice" in result
      assert "Hello" in result
      assert "Bob" in result


  @respx.mock
  async def test_fetch_messages_expired_token():
      respx.get(FETCH_URL).mock(return_value=httpx.Response(401))

      with mock_token():
          result = await seatalk_client.fetch_messages(make_config(), "my-team")

      assert "401" in result
      assert any(word in result.lower() for word in ["expired", "invalid"])


  # --- get_group_info ---

  async def test_get_group_info_unknown_group():
      with mock_token():
          result = await seatalk_client.get_group_info(make_config(), "no-such-group")

      assert "no-such-group" in result


  @respx.mock
  async def test_get_group_info_success():
      respx.get(INFO_URL).mock(return_value=httpx.Response(200, json={
          "group_name": "JNT Test Group",
          "member_count": 5,
      }))

      with mock_token():
          result = await seatalk_client.get_group_info(make_config(), "my-team")

      assert "JNT Test Group" in result
      assert "5" in result


  @respx.mock
  async def test_get_group_info_api_error():
      respx.get(INFO_URL).mock(return_value=httpx.Response(403, text="Forbidden"))

      with mock_token():
          result = await seatalk_client.get_group_info(make_config(), "my-team")

      assert "403" in result
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  python3.11 -m pytest tests/test_seatalk_client.py -v
  ```

  Expected: FAILED / ERROR — old attributes missing, new functions missing.

- [ ] **Step 3: Replace `seatalk_client.py`**

  ```python
  import httpx
  import auth
  from config import Config

  _TIMEOUT = 3.0

  # Confirm all URLs against https://open.seatalk.io/docs before live testing
  _SEND_GROUP_URL = "https://openapi.seatalk.io/v1/chat/group/send_message"
  _FETCH_URL      = "https://openapi.seatalk.io/v1/chat/group/messages"
  _GROUP_INFO_URL = "https://openapi.seatalk.io/v1/chat/group/info"


  async def send_message(config: Config, group: str, message: str) -> str:
      if group not in config.groups:
          available = ", ".join(config.groups.keys())
          return f"Group '{group}' not found. Configured groups: {available}"

      group_id = config.groups[group].group_id
      token = await auth.get_token(config)
      headers = {"Authorization": f"Bearer {token}"}
      payload = {"group_id": group_id, "tag": "text", "text": {"content": message}}

      async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
          for attempt in range(2):
              try:
                  response = await client.post(_SEND_GROUP_URL, json=payload, headers=headers)
                  if response.status_code == 200:
                      return "Message sent successfully"
                  return f"Seatalk error {response.status_code}: {response.text}"
              except httpx.TimeoutException:
                  if attempt == 0:
                      continue
                  return "Request timed out after retry"


  async def list_groups(config: Config) -> str:
      if not config.groups:
          return "No groups configured"
      entries = [
          f"{key} ({g.name})" if g.name else key
          for key, g in config.groups.items()
      ]
      return "Configured groups: " + ", ".join(entries)


  async def fetch_messages(config: Config, group: str, limit: int = 10) -> str:
      if group not in config.groups:
          available = ", ".join(config.groups.keys())
          return f"Group '{group}' not found. Configured groups: {available}"

      if limit < 1:
          return "limit must be at least 1"

      group_id = config.groups[group].group_id
      token = await auth.get_token(config)
      headers = {"Authorization": f"Bearer {token}"}
      params = {"group_id": group_id, "limit": limit}

      async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
          for attempt in range(2):
              try:
                  response = await client.get(_FETCH_URL, headers=headers, params=params)
                  if response.status_code == 200:
                      data = response.json()
                      messages = data.get("messages", [])
                      if not messages:
                          return "No messages found"
                      lines = [
                          f"[{msg.get('timestamp', '')}] {msg.get('sender_name', 'Unknown')}: {msg.get('content', '')}"
                          for msg in messages[:limit]
                      ]
                      return "\n".join(lines)
                  if response.status_code == 401:
                      return "Bot token expired or invalid (401). The access token will refresh automatically on the next call."
                  return f"Seatalk error {response.status_code}: {response.text}"
              except httpx.TimeoutException:
                  if attempt == 0:
                      continue
                  return "Request timed out after retry"


  async def get_group_info(config: Config, group: str) -> str:
      if group not in config.groups:
          available = ", ".join(config.groups.keys())
          return f"Group '{group}' not found. Configured groups: {available}"

      group_id = config.groups[group].group_id
      token = await auth.get_token(config)
      headers = {"Authorization": f"Bearer {token}"}
      params = {"group_id": group_id}

      async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
          for attempt in range(2):
              try:
                  response = await client.get(_GROUP_INFO_URL, headers=headers, params=params)
                  if response.status_code == 200:
                      data = response.json()
                      name = data.get("group_name", "Unknown")
                      members = data.get("member_count", "Unknown")
                      return f"Group: {name}\nMembers: {members}"
                  return f"Seatalk error {response.status_code}: {response.text}"
              except httpx.TimeoutException:
                  if attempt == 0:
                      continue
                  return "Request timed out after retry"
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```bash
  python3.11 -m pytest tests/test_seatalk_client.py -v
  ```

  Expected: all PASSED.

- [ ] **Step 5: Commit**

  ```bash
  git add seatalk_client.py tests/test_seatalk_client.py
  git commit -m "feat: migrate seatalk_client to bot API with OAuth token"
  ```

---

## Task 5: Add `get_group_info` MCP Tool

- [ ] **Step 1: Update `seatalk_mcp_server.py`**

  Replace entire file:

  ```python
  from mcp.server.fastmcp import FastMCP
  import seatalk_client
  from config import load_config

  config = load_config()
  mcp = FastMCP("seatalk")


  @mcp.tool()
  async def send_message(group: str, message: str) -> str:
      """Send a text message to a named Seatalk group.

      Args:
          group: Name of the group as defined in config.json (e.g. "my-team")
          message: Text content to send
      """
      return await seatalk_client.send_message(config, group, message)


  @mcp.tool()
  async def list_groups() -> str:
      """List all configured Seatalk groups."""
      return await seatalk_client.list_groups(config)


  @mcp.tool()
  async def fetch_messages(group: str, limit: int = 10) -> str:
      """Fetch recent messages from a named Seatalk group (on-demand only).

      Args:
          group: Name of the group as defined in config.json
          limit: Number of recent messages to retrieve (default 10)
      """
      return await seatalk_client.fetch_messages(config, group, limit)


  @mcp.tool()
  async def get_group_info(group: str) -> str:
      """Fetch live info for a named Seatalk group (name, member count).

      Args:
          group: Name of the group as defined in config.json
      """
      return await seatalk_client.get_group_info(config, group)


  if __name__ == "__main__":
      mcp.run()
  ```

- [ ] **Step 2: Smoke-test the MCP server starts cleanly**

  ```bash
  cd "/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge"
  python3.11 -c "from seatalk_mcp_server import mcp; print('OK')"
  ```

  Expected: `OK` with no errors.

- [ ] **Step 3: Run full test suite**

  ```bash
  python3.11 -m pytest tests/ -v
  ```

  Expected: all PASSED.

- [ ] **Step 4: Commit**

  ```bash
  git add seatalk_mcp_server.py
  git commit -m "feat: add get_group_info MCP tool"
  ```

---

## Task 6: Update `config.json` With Real Credentials

- [ ] **Step 1: Fill in your App ID and App Secret**

  In `config.json`, replace the placeholder values:
  - `app_id` — from Seatalk Developer Portal → your app → General Setting → Basic Info & Credentials
  - `app_secret` — same location
  - `group_id` per group — find via the Seatalk Developer Portal or by calling `get_group_info` once the bot is in the group

- [ ] **Step 2: Verify `config.json` is gitignored**

  ```bash
  git check-ignore -v config.json
  ```

  Expected: line showing `config.json` is ignored. If not, add it:

  ```bash
  echo "config.json" >> .gitignore
  git add .gitignore
  git commit -m "chore: ensure config.json is gitignored"
  ```

- [ ] **Step 3: Smoke-test live token fetch**

  ```bash
  python3.11 -c "
  import asyncio
  from config import load_config
  import auth

  async def main():
      config = load_config()
      token = await auth.get_token(config)
      print('Token obtained:', token[:10], '...')

  asyncio.run(main())
  "
  ```

  Expected: `Token obtained: <first 10 chars> ...`

- [ ] **Step 4: Commit config.example.json if not already committed**

  ```bash
  git add config.example.json
  git status
  git commit -m "chore: update config.example.json for bot API schema" --allow-empty
  ```
