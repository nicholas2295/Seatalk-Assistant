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
