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

    if limit < 1:
        return "limit must be at least 1"

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
