# Seatalk Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local MCP server that lets Claude Code send messages to and fetch messages from Seatalk groups via a named group registry.

**Architecture:** A Python FastMCP server exposes three tools (`send_message`, `fetch_messages`, `list_groups`) to Claude Code over stdio. Outbound messages POST to Seatalk webhook URLs; inbound messages call the Seatalk OpenAPI with a bot token. All group credentials live in a gitignored `config.json`.

**Tech Stack:** Python 3.11+, `mcp` (FastMCP), `httpx`, `pytest`, `pytest-asyncio`, `respx`

---

## File Map

| File | Purpose |
|------|---------|
| `requirements.txt` | Python dependencies |
| `pytest.ini` | pytest asyncio configuration |
| `.gitignore` | Exclude config.json and venv |
| `config.example.json` | Committed config template |
| `config.py` | Load and validate config.json |
| `seatalk_client.py` | HTTP calls to Seatalk (send, fetch, list) |
| `seatalk_mcp_server.py` | FastMCP server, tool definitions |
| `tests/__init__.py` | Empty, marks tests as a package |
| `tests/test_config.py` | Tests for config loading/validation |
| `tests/test_seatalk_client.py` | Tests for Seatalk HTTP client functions |

All paths are relative to the project root:
`/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge/`

---

## Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `pytest.ini`
- Create: `.gitignore`
- Create: `config.example.json`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create `requirements.txt`**

```
mcp>=1.0.0
httpx>=0.27.0
respx>=0.21.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 2: Create `pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 3: Create `.gitignore`**

```
config.json
.venv/
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 4: Create `config.example.json`**

```json
{
  "groups": {
    "example-group": {
      "webhook_url": "https://openapi.seatalk.io/webhook/group/<your-group-id>"
    }
  },
  "bot_token": "<obtain from Seatalk Developer Portal>",
  "signing_secret": "<obtain from Seatalk Developer Portal>"
}
```

- [ ] **Step 5: Create empty `tests/__init__.py`**

File content: (empty)

- [ ] **Step 6: Install dependencies**

```bash
cd "/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge"
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Expected: All packages install without errors. `mcp`, `httpx`, `respx`, `pytest`, `pytest-asyncio` present.

- [ ] **Step 7: Commit**

```bash
git init
git add requirements.txt pytest.ini .gitignore config.example.json tests/__init__.py
git commit -m "chore: project scaffolding"
```

---

## Task 2: Config Loader

**Files:**
- Create: `config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
import json
import pytest
from pathlib import Path
from config import load_config


def test_load_config_success(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "groups": {
            "my-team": {"webhook_url": "https://openapi.seatalk.io/webhook/group/abc123"}
        },
        "bot_token": "test-token",
        "signing_secret": "test-secret"
    }))

    config = load_config(str(config_file))

    assert "my-team" in config.groups
    assert config.groups["my-team"].webhook_url == "https://openapi.seatalk.io/webhook/group/abc123"
    assert config.bot_token == "test-token"
    assert config.signing_secret == "test-secret"


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError, match="config.json"):
        load_config("/nonexistent/config.json")


def test_load_config_missing_groups_key(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"bot_token": "tok"}))

    with pytest.raises(ValueError, match="groups"):
        load_config(str(config_file))


def test_load_config_empty_groups(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"groups": {}}))

    with pytest.raises(ValueError, match="at least one group"):
        load_config(str(config_file))


def test_load_config_group_missing_webhook(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "groups": {"my-team": {}}
    }))

    with pytest.raises(ValueError, match="webhook_url"):
        load_config(str(config_file))


def test_load_config_optional_fields_absent(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "groups": {"my-team": {"webhook_url": "https://example.com/wh"}}
    }))

    config = load_config(str(config_file))

    assert config.bot_token is None
    assert config.signing_secret is None
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: Implement `config.py`**

```python
import json
from pathlib import Path
from dataclasses import dataclass


@dataclass
class GroupConfig:
    webhook_url: str


@dataclass
class Config:
    groups: dict[str, GroupConfig]
    bot_token: str | None
    signing_secret: str | None


def load_config(path: str | None = None) -> Config:
    if path is None:
        path = str(Path(__file__).parent / "config.json")

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. "
            "Copy config.example.json to config.json and fill in your values."
        )

    with open(config_path) as f:
        data = json.load(f)

    if "groups" not in data or not isinstance(data["groups"], dict):
        raise ValueError("config.json must contain a 'groups' object")

    if not data["groups"]:
        raise ValueError("config.json must contain at least one group")

    groups = {}
    for name, group_data in data["groups"].items():
        if "webhook_url" not in group_data:
            raise ValueError(f"Group '{name}' is missing 'webhook_url'")
        groups[name] = GroupConfig(webhook_url=group_data["webhook_url"])

    return Config(
        groups=groups,
        bot_token=data.get("bot_token"),
        signing_secret=data.get("signing_secret"),
    )
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/test_config.py -v
```

Expected: 6 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_config.py
git commit -m "feat: config loader with validation"
```

---

## Task 3: send_message Client Function

**Files:**
- Create: `seatalk_client.py`
- Create: `tests/test_seatalk_client.py`

- [ ] **Step 1: Write the failing tests for send_message**

Create `tests/test_seatalk_client.py`:

```python
import pytest
import respx
import httpx
from config import Config, GroupConfig
import seatalk_client

WEBHOOK_URL = "https://openapi.seatalk.io/webhook/group/abc123"


def make_config(bot_token: str | None = None) -> Config:
    return Config(
        groups={"my-team": GroupConfig(webhook_url=WEBHOOK_URL)},
        bot_token=bot_token,
        signing_secret="test-secret",
    )


# --- send_message ---

@respx.mock
async def test_send_message_success():
    respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))

    result = await seatalk_client.send_message(make_config(), "my-team", "Hello")

    assert result == "Message sent successfully"


@respx.mock
async def test_send_message_unknown_group():
    result = await seatalk_client.send_message(make_config(), "no-such-group", "Hi")

    assert "no-such-group" in result
    assert "my-team" in result


@respx.mock
async def test_send_message_webhook_error():
    respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(400, text="Bad Request"))

    result = await seatalk_client.send_message(make_config(), "my-team", "Hello")

    assert "400" in result
    assert "Bad Request" in result


# --- list_groups ---

async def test_list_groups():
    result = await seatalk_client.list_groups(make_config())

    assert "my-team" in result


# --- fetch_messages ---

async def test_fetch_messages_no_bot_token():
    result = await seatalk_client.fetch_messages(make_config(bot_token=None), "my-team")

    assert "bot_token" in result
    assert "config.json" in result


async def test_fetch_messages_unknown_group():
    result = await seatalk_client.fetch_messages(make_config(bot_token="tok"), "no-such-group")

    assert "no-such-group" in result


@respx.mock
async def test_fetch_messages_success():
    respx.get("https://openapi.seatalk.io/v1/chat/group/messages").mock(
        return_value=httpx.Response(200, json={
            "messages": [
                {"sender_name": "Alice", "content": "Hello", "timestamp": "2026-04-07T10:00:00Z"},
                {"sender_name": "Bob", "content": "Hi there", "timestamp": "2026-04-07T10:01:00Z"},
            ]
        })
    )

    result = await seatalk_client.fetch_messages(make_config(bot_token="test-token"), "my-team", limit=10)

    assert "Alice" in result
    assert "Hello" in result
    assert "Bob" in result


@respx.mock
async def test_fetch_messages_expired_token():
    respx.get("https://openapi.seatalk.io/v1/chat/group/messages").mock(
        return_value=httpx.Response(401)
    )

    result = await seatalk_client.fetch_messages(make_config(bot_token="expired"), "my-team")

    assert "401" in result
    assert any(word in result.lower() for word in ["expired", "invalid"])
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_seatalk_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'seatalk_client'`

- [ ] **Step 3: Implement `seatalk_client.py`**

```python
import httpx
from config import Config

_TIMEOUT = 3.0
_FETCH_URL = "https://openapi.seatalk.io/v1/chat/group/messages"
# NOTE: Verify _FETCH_URL against your Seatalk OpenAPI docs before live testing.
# The endpoint above is a best-guess. Update it if the real endpoint differs.


async def send_message(config: Config, group: str, message: str) -> str:
    if group not in config.groups:
        available = ", ".join(config.groups.keys())
        return f"Group '{group}' not found. Configured groups: {available}"

    webhook_url = config.groups[group].webhook_url
    payload = {"tag": "text", "text": {"content": message}}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(2):
            try:
                response = await client.post(webhook_url, json=payload)
                if response.status_code == 200:
                    return "Message sent successfully"
                return f"Seatalk error {response.status_code}: {response.text}"
            except httpx.TimeoutException:
                if attempt == 0:
                    continue
                return "Request timed out after retry"

    return "Request failed"


async def list_groups(config: Config) -> str:
    if not config.groups:
        return "No groups configured"
    return "Configured groups: " + ", ".join(config.groups.keys())


async def fetch_messages(config: Config, group: str, limit: int = 10) -> str:
    if group not in config.groups:
        available = ", ".join(config.groups.keys())
        return f"Group '{group}' not found. Configured groups: {available}"

    if not config.bot_token:
        return (
            "fetch_messages requires bot_token in config.json — "
            "see config.example.json for instructions"
        )

    headers = {"Authorization": f"Bearer {config.bot_token}"}
    params = {"group_name": group, "limit": limit}

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
                    return "Bot token expired or invalid (401). Refresh your token at the Seatalk Developer Portal."
                return f"Seatalk error {response.status_code}: {response.text}"
            except httpx.TimeoutException:
                if attempt == 0:
                    continue
                return "Request timed out after retry"

    return "Request failed"
```

- [ ] **Step 4: Run all tests — verify they pass**

```bash
pytest tests/ -v
```

Expected: All tests PASSED (6 config tests + 8 client tests = 14 total)

- [ ] **Step 5: Commit**

```bash
git add seatalk_client.py tests/test_seatalk_client.py
git commit -m "feat: seatalk client — send, list, fetch"
```

---

## Task 4: MCP Server

**Files:**
- Create: `seatalk_mcp_server.py`

No unit tests for the server itself — Claude Code integration is the test (Task 5).

- [ ] **Step 1: Create `seatalk_mcp_server.py`**

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


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 2: Smoke-test the server starts**

First, copy config.example.json to config.json and fill in your real webhook URL and signing secret (bot_token can be left as placeholder for now):

```bash
cp config.example.json config.json
# Edit config.json: set the real webhook_url and signing_secret
```

Then run:

```bash
python seatalk_mcp_server.py
```

Expected: Server starts without errors, no output (it waits for MCP protocol input via stdin). Press Ctrl+C to stop.

- [ ] **Step 3: Commit**

```bash
git add seatalk_mcp_server.py
git commit -m "feat: MCP server with send_message, list_groups, fetch_messages tools"
```

---

## Task 5: Register with Claude Code and Live Test

**Files:**
- Modify: `~/.claude.json` (via `claude mcp add` command)

- [ ] **Step 1: Register the MCP server with Claude Code**

```bash
claude mcp add seatalk python "/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge/seatalk_mcp_server.py"
```

Expected output: `Added MCP server seatalk`

If the command fails due to the path having spaces, run instead:

```bash
claude mcp add seatalk -- python "/Users/nicholas.lim/Library/CloudStorage/GoogleDrive-nicholas.lim@shopee.com/My Drive/Shopee/Claude/Seatalk Bridge/seatalk_mcp_server.py"
```

- [ ] **Step 2: Verify registration**

```bash
claude mcp list
```

Expected: `seatalk` appears in the list.

- [ ] **Step 3: Live test — list groups**

Start a new Claude Code conversation and ask:

> "Use the list_groups tool"

Expected: Claude calls `list_groups` and returns the group names from your config.json (e.g. `Configured groups: my-team`).

- [ ] **Step 4: Live test — send a message**

Ask Claude:

> "Use the send_message tool to send 'Hello from Claude' to the my-team group"

Expected: Claude calls `send_message("my-team", "Hello from Claude")` and returns `"Message sent successfully"`. Verify the message appears in your Seatalk group.

- [ ] **Step 5: Live test — fetch messages (once bot_token is set)**

Once you have your bot token from the Seatalk Developer Portal, add it to `config.json` under `"bot_token"`. Then ask Claude:

> "Use the fetch_messages tool to get the last 5 messages from my-team"

Expected: Claude returns a list of recent messages with sender names and content.

**If fetch_messages returns a Seatalk error (wrong endpoint):** Check your Seatalk Developer Portal API docs for the correct group message history endpoint. Update `_FETCH_URL` in `seatalk_client.py` (the constant near the top of the file) to match.

- [ ] **Step 6: Final commit**

```bash
git add -p  # review any config.json changes you want to track (do NOT commit config.json)
git commit -m "docs: add live test results and endpoint notes"
```

---

## Spec Coverage Check

| Spec requirement | Task |
|-----------------|------|
| send_message tool | Task 3, Task 4 |
| fetch_messages tool (on-demand only) | Task 3, Task 4 |
| list_groups tool | Task 3, Task 4 |
| Multi-group config registry | Task 2 |
| Error: unknown group | Task 3 (tests + impl) |
| Error: webhook failure | Task 3 (tests + impl) |
| Error: missing bot_token | Task 3 (tests + impl) |
| Error: expired bot_token | Task 3 (tests + impl) |
| Error: network timeout + retry | Task 3 (impl) |
| Error: malformed config | Task 2 (tests + impl) |
| Signing secret stored (not active) | Task 2 (Config dataclass) |
| Register with Claude Code | Task 5 |
| config.json gitignored | Task 1 |
