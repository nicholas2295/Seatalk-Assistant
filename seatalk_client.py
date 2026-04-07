import httpx
import auth
from config import Config

_TIMEOUT = 3.0

# Confirm all URLs against https://open.seatalk.io/docs before live testing
_SEND_GROUP_URL = "https://openapi.seatalk.io/messaging/v2/group_chat"
_FETCH_URL      = "https://openapi.seatalk.io/messaging/v2/get_message_by_message_id"
_HISTORY_URL    = "https://openapi.seatalk.io/messaging/v2/group_chat/history"
_GROUP_INFO_URL = "https://openapi.seatalk.io/messaging/v2/group_chat/info"


async def _resolve_group(
    config: Config, group: str
) -> "tuple[str, dict] | str":
    """Return (group_id, auth_headers) or an error string if group not found."""
    if group not in config.groups:
        available = ", ".join(config.groups.keys())
        return f"Group '{group}' not found. Configured groups: {available}"
    group_id = config.groups[group].group_id
    token = await auth.get_token(config)
    return group_id, {"Authorization": f"Bearer {token}"}


async def send_message(config: Config, group: str, message: str) -> str:
    resolved = await _resolve_group(config, group)
    if isinstance(resolved, str):
        return resolved
    group_id, headers = resolved
    payload = {"group_id": group_id, "message": {"tag": "text", "text": {"content": message}}}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(2):
            try:
                response = await client.post(_SEND_GROUP_URL, json=payload, headers=headers)
                try:
                    data = response.json()
                except Exception:
                    data = {}
                if response.status_code == 200 and data.get("code") == 0:
                    return "Message sent successfully"
                return f"Seatalk error {data.get('code', response.status_code)}: {data.get('message', response.text)}"
            except httpx.TimeoutException:
                if attempt == 0:
                    continue
                return "Request timed out after retry"
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
    """Fetch recent messages from a group. Requires 'Get Chat History' API permission
    (org admin approval needed — apply via Seatalk Developer Portal → Scopes & Permissions)."""
    resolved = await _resolve_group(config, group)
    if isinstance(resolved, str):
        return resolved
    group_id, headers = resolved

    if limit < 1:
        return "limit must be at least 1"

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(2):
            try:
                response = await client.get(
                    _HISTORY_URL, headers=headers,
                    params={"group_id": group_id, "page_size": min(limit, 100)}
                )
                data = response.json()
                if data.get("code") == 103:
                    return (
                        "fetch_messages requires 'Get Chat History' API permission. "
                        "Apply via Seatalk Developer Portal → your app → Scopes & Permissions."
                    )
                if response.status_code == 200 and data.get("code") == 0:
                    history = data.get("chat_history", [])
                    if not history:
                        return "No messages found"
                    lines = []
                    for msg in history[:limit]:
                        sender = (msg.get("sender") or {}).get("email", "Unknown")
                        tag = msg.get("tag", "")
                        content = ""
                        if tag == "text":
                            content = (msg.get("text") or {}).get("plain_text", "")
                        elif tag in ("image", "video", "file"):
                            content = f"[{tag}]"
                        else:
                            content = f"[{tag}]"
                        lines.append(f"[{sender}] {content}")
                    return "\n".join(lines)
                return f"Seatalk error {data.get('code', response.status_code)}: {data.get('message', response.text)}"
            except httpx.TimeoutException:
                if attempt == 0:
                    continue
                return "Request timed out after retry"
    return "Request timed out after retry"


async def fetch_message_by_id(config: Config, message_id: str) -> str:
    """Fetch a single message by its message_id."""
    token = await auth.get_token(config)
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(2):
            try:
                response = await client.get(
                    _FETCH_URL, headers=headers, params={"message_id": message_id}
                )
                data = response.json()
                if response.status_code == 200 and data.get("code") == 0:
                    sender = (data.get("sender") or {}).get("email", "Unknown")
                    tag = data.get("tag", "")
                    content = ""
                    if tag == "text":
                        content = (data.get("text") or {}).get("plain_text", "")
                    return f"[{sender}] {content}" if content else f"Message type: {tag}"
                return f"Seatalk error {data.get('code', response.status_code)}: {data.get('message', response.text)}"
            except httpx.TimeoutException:
                if attempt == 0:
                    continue
                return "Request timed out after retry"
    return "Request timed out after retry"


async def get_group_info(config: Config, group: str) -> str:
    resolved = await _resolve_group(config, group)
    if isinstance(resolved, str):
        return resolved
    group_id, headers = resolved
    params = {"group_id": group_id}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(2):
            try:
                response = await client.get(_GROUP_INFO_URL, headers=headers, params=params)
                if response.status_code == 200:
                    data = response.json()
                    group = data.get("group", {})
                    name = group.get("group_name", "Unknown")
                    users = group.get("group_user_total", "?")
                    bots = group.get("group_bot_total", 0)
                    return f"Group: {name}\nMembers: {users} users, {bots} bot(s)"
                return f"Seatalk error {response.status_code}: {response.text}"
            except httpx.TimeoutException:
                if attempt == 0:
                    continue
                return "Request timed out after retry"
    return "Request timed out after retry"
