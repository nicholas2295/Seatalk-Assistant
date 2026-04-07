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


@respx.mock
async def test_send_message_retries_on_timeout():
    respx.post(WEBHOOK_URL).mock(side_effect=[
        httpx.TimeoutException("timeout"),
        httpx.Response(200),
    ])

    result = await seatalk_client.send_message(make_config(), "my-team", "Hello")

    assert result == "Message sent successfully"


@respx.mock
async def test_send_message_timeout_after_retry():
    respx.post(WEBHOOK_URL).mock(side_effect=httpx.TimeoutException("timeout"))

    result = await seatalk_client.send_message(make_config(), "my-team", "Hello")

    assert "timed out" in result.lower() or "retry" in result.lower()


# --- list_groups ---

async def test_list_groups():
    result = await seatalk_client.list_groups(make_config())

    assert "my-team" in result


async def test_list_groups_empty():
    config = Config(groups={}, bot_token=None, signing_secret=None)

    result = await seatalk_client.list_groups(config)

    assert result == "No groups configured"


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
    respx.get(seatalk_client._FETCH_URL).mock(
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
    respx.get(seatalk_client._FETCH_URL).mock(
        return_value=httpx.Response(401)
    )

    result = await seatalk_client.fetch_messages(make_config(bot_token="expired"), "my-team")

    assert "401" in result
    assert any(word in result.lower() for word in ["expired", "invalid"])
