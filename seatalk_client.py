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
