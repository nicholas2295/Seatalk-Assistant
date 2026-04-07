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

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                _TOKEN_URL,
                json={"app_id": config.app_id, "app_secret": config.app_secret},
            )
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error fetching Seatalk access token: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to get Seatalk access token: {response.status_code} {response.text}"
        )

    data = response.json()
    if "access_token" not in data:
        raise RuntimeError(
            f"Seatalk token response missing 'access_token' field: {response.text}"
        )
    token = data["access_token"]
    expire_in = int(data.get("expire_in", 7200))
    _cache[id(config)] = _TokenEntry(token=token, expires_at=time.monotonic() + expire_in)

    return token
