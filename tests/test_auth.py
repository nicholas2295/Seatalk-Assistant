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
async def test_get_token_refreshes_when_expired():
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
