"""TelegramBotClient: no real network anywhere - fake get_updates/
send_message transports play the role `urllib` normally would.
"""

from __future__ import annotations

import json

import pytest

from tidemark.market_intel.errors import (
    MissingBotTokenError,
    TelegramConnectionError,
    TelegramHttpError,
)
from tidemark.market_intel.telegram_client import TelegramBotClient

# -- missing token: clean, eager failure -------------------------------------


def test_missing_bot_token_raises_immediately() -> None:
    with pytest.raises(MissingBotTokenError):
        TelegramBotClient(bot_token=None)


def test_empty_string_bot_token_raises_immediately() -> None:
    with pytest.raises(MissingBotTokenError):
        TelegramBotClient(bot_token="")


# -- get_updates ---------------------------------------------------------------


def test_get_updates_parses_result_list() -> None:
    def fake_get_updates(token: str, offset, timeout: int) -> str:
        assert token == "secret-token"
        assert offset == 5
        assert timeout == 30
        return json.dumps({"ok": True, "result": [{"update_id": 5, "message": {}}]})

    client = TelegramBotClient(bot_token="secret-token", get_updates_transport=fake_get_updates)
    updates = client.get_updates(offset=5, timeout=30)

    assert updates == [{"update_id": 5, "message": {}}]


def test_get_updates_with_no_offset_passes_none_through() -> None:
    def fake_get_updates(token: str, offset, timeout: int) -> str:
        assert offset is None
        return json.dumps({"ok": True, "result": []})

    client = TelegramBotClient(bot_token="tok", get_updates_transport=fake_get_updates)
    assert client.get_updates(offset=None, timeout=0) == []


def test_get_updates_propagates_http_error() -> None:
    def unauthorized(token: str, offset, timeout: int) -> str:
        raise TelegramHttpError(401, "Unauthorized")

    client = TelegramBotClient(bot_token="bad-token", get_updates_transport=unauthorized)

    with pytest.raises(TelegramHttpError) as exc_info:
        client.get_updates(offset=None, timeout=0)
    assert exc_info.value.status_code == 401


def test_get_updates_propagates_connection_error() -> None:
    def times_out(token: str, offset, timeout: int) -> str:
        raise TelegramConnectionError("simulated timeout")

    client = TelegramBotClient(bot_token="tok", get_updates_transport=times_out)

    with pytest.raises(TelegramConnectionError):
        client.get_updates(offset=None, timeout=0)


# -- send_message ---------------------------------------------------------------


def test_send_message_passes_chat_id_and_text() -> None:
    calls = []

    def fake_send(token: str, chat_id: int, text: str) -> str:
        calls.append((token, chat_id, text))
        return json.dumps({"ok": True, "result": {}})

    client = TelegramBotClient(bot_token="tok", send_message_transport=fake_send)
    client.send_message(123, "hello")

    assert calls == [("tok", 123, "hello")]


def test_send_message_propagates_http_error() -> None:
    def bad_chat(token: str, chat_id: int, text: str) -> str:
        raise TelegramHttpError(400, "Bad Request: chat not found")

    client = TelegramBotClient(bot_token="tok", send_message_transport=bad_chat)

    with pytest.raises(TelegramHttpError) as exc_info:
        client.send_message(999, "hello")
    assert exc_info.value.status_code == 400


# -- secret hygiene: the token never leaks ------------------------------------


def test_repr_and_str_never_leak_the_token() -> None:
    client = TelegramBotClient(bot_token="super-secret-bot-token")
    assert "super-secret-bot-token" not in repr(client)
    assert "super-secret-bot-token" not in str(client)


def test_token_never_appears_in_a_raised_error_message() -> None:
    def unauthorized(token: str, offset, timeout: int) -> str:
        raise TelegramHttpError(401, "Unauthorized")

    client = TelegramBotClient(
        bot_token="super-secret-bot-token", get_updates_transport=unauthorized
    )

    with pytest.raises(TelegramHttpError) as exc_info:
        client.get_updates(offset=None, timeout=0)
    assert "super-secret-bot-token" not in str(exc_info.value)
