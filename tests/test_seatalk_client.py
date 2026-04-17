import pytest
import respx
import httpx
from unittest.mock import AsyncMock, patch
from config import Config, GroupConfig
import auth
import seatalk_client


@pytest.fixture(autouse=True)
def _reset_shared_client():
    """Reset the shared httpx client before each test so respx can intercept."""
    auth._shared_client = None
    yield
    auth._shared_client = None

GROUP_ID    = "grp-abc123"
SEND_URL    = seatalk_client._SEND_GROUP_URL
FETCH_URL   = seatalk_client._FETCH_URL
INFO_URL    = seatalk_client._GROUP_INFO_URL
LIST_URL    = seatalk_client._JOINED_GROUPS_URL
EMP_URL     = seatalk_client._EMPLOYEE_CODE_URL
SEND_DM_URL = seatalk_client._SEND_DM_URL


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
async def test_fetch_messages_permission_denied():
    respx.get(seatalk_client._HISTORY_URL).mock(
        return_value=httpx.Response(200, json={"code": 103, "message": "app permission denied"})
    )

    with mock_token():
        result = await seatalk_client.fetch_messages(make_config(), "my-team")

    assert "permission" in result.lower()
    assert "Scopes & Permissions" in result


@respx.mock
async def test_fetch_messages_success():
    respx.get(seatalk_client._HISTORY_URL).mock(
        return_value=httpx.Response(200, json={
            "code": 0,
            "next_cursor": "",
            "chat_history": [
                {"sender": {"email": "alice@example.com"}, "tag": "text",
                 "text": {"plain_text": "Hello"}, "message_sent_time": 1000},
                {"sender": {"email": "bob@example.com"}, "tag": "text",
                 "text": {"plain_text": "Hi"}, "message_sent_time": 999},
            ]
        })
    )

    with mock_token():
        result = await seatalk_client.fetch_messages(make_config(), "my-team", limit=10)

    assert "alice@example.com" in result
    assert "Hello" in result
    assert "bob@example.com" in result


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


# --- list_joined_groups ---

@respx.mock
async def test_list_joined_groups_success():
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, json={
        "code": 0,
        "next_cursor": "",
        "joined_group_chats": {"group_id": ["grp-1", "grp-2"]},
    }))

    with mock_token():
        result = await seatalk_client.list_joined_groups(make_config())

    assert "grp-1" in result
    assert "grp-2" in result


@respx.mock
async def test_list_joined_groups_empty():
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, json={
        "code": 0, "joined_group_chats": {"group_id": []}
    }))

    with mock_token():
        result = await seatalk_client.list_joined_groups(make_config())

    assert "not in any groups" in result.lower()


@respx.mock
async def test_list_joined_groups_api_error():
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, json={"code": 403, "message": "forbidden"}))

    with mock_token():
        result = await seatalk_client.list_joined_groups(make_config())

    assert "403" in result


# --- get_employee_code ---

@respx.mock
async def test_get_employee_code_success():
    respx.post(EMP_URL).mock(return_value=httpx.Response(200, json={
        "code": 0,
        "employees": [{"employee_code": "EMP001", "display_name": "Alice"}],
    }))

    with mock_token():
        result = await seatalk_client.get_employee_code(make_config(), "alice@example.com")

    assert result == ("EMP001", "Alice")


@respx.mock
async def test_get_employee_code_not_found():
    respx.post(EMP_URL).mock(return_value=httpx.Response(200, json={"code": 0, "employees": []}))

    with mock_token():
        result = await seatalk_client.get_employee_code(make_config(), "nobody@example.com")

    assert isinstance(result, str)
    assert "nobody@example.com" in result


@respx.mock
async def test_get_employee_code_api_error():
    respx.post(EMP_URL).mock(return_value=httpx.Response(200, json={"code": 500, "message": "internal error"}))

    with mock_token():
        result = await seatalk_client.get_employee_code(make_config(), "alice@example.com")

    assert "500" in result


# --- send_dm ---

@respx.mock
async def test_send_dm_success():
    respx.post(EMP_URL).mock(return_value=httpx.Response(200, json={
        "code": 0,
        "employees": [{"employee_code": "EMP001", "display_name": "Alice"}],
    }))
    respx.post(SEND_DM_URL).mock(return_value=httpx.Response(200, json={"code": 0}))

    with mock_token():
        result = await seatalk_client.send_dm(make_config(), "alice@example.com", "Hey!")

    assert "Alice" in result
    assert "alice@example.com" in result


@respx.mock
async def test_send_dm_employee_not_found():
    respx.post(EMP_URL).mock(return_value=httpx.Response(200, json={"code": 0, "employees": []}))

    with mock_token():
        result = await seatalk_client.send_dm(make_config(), "ghost@example.com", "Hi")

    assert "ghost@example.com" in result


@respx.mock
async def test_send_dm_api_error():
    respx.post(EMP_URL).mock(return_value=httpx.Response(200, json={
        "code": 0,
        "employees": [{"employee_code": "EMP001", "display_name": "Alice"}],
    }))
    respx.post(SEND_DM_URL).mock(return_value=httpx.Response(200, json={"code": 400, "message": "bad request"}))

    with mock_token():
        result = await seatalk_client.send_dm(make_config(), "alice@example.com", "Hey!")

    assert "400" in result
