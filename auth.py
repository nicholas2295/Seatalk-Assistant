import time
import httpx
from dataclasses import dataclass
from config import Config

# Confirm URL against https://open.seatalk.io/docs
_TOKEN_URL = "https://openapi.seatalk.io/auth/app_access_token"
_TIMEOUT = 5.0
_REFRESH_BUFFER = 60  # refresh this many seconds before actual expiry

_shared_client: httpx.AsyncClient | None = None


def get_shared_client() -> httpx.AsyncClient:
    """Return a shared httpx.AsyncClient with connection pooling."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _shared_client


@dataclass
class _TokenEntry:
    token: str
    expires_at: float  # monotonic time


_cache: dict[tuple[str, str], _TokenEntry] = {}


def _cache_key(config: Config) -> tuple[str, str]:
    return (config.app_id, config.app_secret)


async def get_token(config: Config) -> str:
    """Return a valid access token, fetching or refreshing as needed."""
    key = _cache_key(config)
    entry = _cache.get(key)
    if entry and time.monotonic() < entry.expires_at - _REFRESH_BUFFER:
        return entry.token

    client = get_shared_client()
    try:
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
    if "app_access_token" not in data:
        raise RuntimeError(
            f"Seatalk token response missing 'app_access_token' field: {response.text}"
        )
    token = data["app_access_token"]
    # 'expire' is an absolute Unix timestamp
    expire_unix = int(data.get("expire", time.time() + 7200))
    expires_at = time.monotonic() + (expire_unix - time.time())
    _cache[key] = _TokenEntry(token=token, expires_at=expires_at)

    return token
