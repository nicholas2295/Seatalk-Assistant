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


async def test_fetch_messages_returns_unavailable():
    result = await seatalk_client.fetch_messages(make_config(), "my-team")

    assert "not available" in result.lower()
    assert "management approval" in result.lower()


# --- fetch_message_by_id ---

@respx.mock
async def test_fetch_message_by_id_success():
    respx.get(FETCH_URL).mock(return_value=httpx.Response(200, json={
        "code": 0,
        "tag": "text",
        "sender": {"email": "alice@example.com"},
        "text": {"plain_text": "Hello world"},
    }))

    with mock_token():
        result = await seatalk_client.fetch_message_by_id(make_config(), "msg-123")

    assert "alice@example.com" in result
    assert "Hello world" in result


@respx.mock
async def test_fetch_message_by_id_error():
    respx.get(FETCH_URL).mock(return_value=httpx.Response(200, json={
        "code": 404, "message": "message not found"
    }))

    with mock_token():
        result = await seatalk_client.fetch_message_by_id(make_config(), "bad-id")

    assert "404" in result


# --- get_group_info ---

async def test_get_group_info_unknown_group():
    with mock_token():
        result = await seatalk_client.get_group_info(make_config(), "no-such-group")

    assert "no-such-group" in result


@respx.mock
async def test_get_group_info_success():
    respx.get(INFO_URL).mock(return_value=httpx.Response(200, json={
        "code": 0,
        "group": {"group_name": "JNT Test Group", "group_user_total": 5, "group_bot_total": 1},
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
