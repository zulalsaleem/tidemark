"""Minimal Telegram Bot API client for the /coin long-polling bot.

Deliberately independent from `notify.telegram` (the research engine's
one-way alert sender): that module imports `tidemark.context.htf`,
`tidemark.data.models`, and `tidemark.journal.changes` to build its
alert text, so reusing it here would transitively pull the research
engine into `market_intel` even though the import-boundary test only
checks *direct* imports per file. This client is a separate,
self-contained implementation of exactly the two Bot API methods this
bot needs - `getUpdates` and `sendMessage` - with none of
`notify.telegram`'s alert-formatting or Section 1/2 knowledge.

The bot token is held as `SecretStr` (never a plain `str` after
construction) and is passed only in the request URL - the shape
Telegram's Bot API itself requires (`/bot<TOKEN>/<method>`, no
header-based auth option) - and it is never included in a log message
or a raised exception's text; errors are reported by HTTP status and
method name only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from pydantic import SecretStr

from tidemark.market_intel.errors import (
    MissingBotTokenError,
    TelegramConnectionError,
    TelegramHttpError,
)

TELEGRAM_API_BASE = "https://api.telegram.org"
POLL_TIMEOUT_PADDING_SECONDS = 10.0
SEND_TIMEOUT_SECONDS = 10.0


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read())
        description = payload.get("description")
        if description:
            return str(description)
    except Exception:
        pass
    return exc.reason or str(exc)


def _do_request(request: urllib.request.Request, timeout: float) -> str:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise TelegramHttpError(exc.code, _error_detail(exc)) from exc
    except Exception as exc:
        raise TelegramConnectionError(str(exc)) from exc


def _http_get_updates(token: str, offset: int | None, timeout: int) -> str:
    params: dict[str, str] = {"timeout": str(timeout), "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        params["offset"] = str(offset)
    url = f"{TELEGRAM_API_BASE}/bot{token}/getUpdates?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, method="GET")
    return _do_request(request, timeout + POLL_TIMEOUT_PADDING_SECONDS)


def _http_send_message(token: str, chat_id: int, text: str) -> str:
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    return _do_request(request, SEND_TIMEOUT_SECONDS)


class TelegramBotClient:
    """`getUpdates`/`sendMessage` only - no webhook, no other Bot API method."""

    def __init__(
        self,
        bot_token: str | SecretStr | None,
        get_updates_transport: Callable[[str, int | None, int], str] = _http_get_updates,
        send_message_transport: Callable[[str, int, str], str] = _http_send_message,
    ) -> None:
        if bot_token is None or (isinstance(bot_token, str) and not bot_token):
            raise MissingBotTokenError()
        if isinstance(bot_token, SecretStr) and not bot_token.get_secret_value():
            raise MissingBotTokenError()

        self._bot_token = bot_token if isinstance(bot_token, SecretStr) else SecretStr(bot_token)
        self._get_updates_transport = get_updates_transport
        self._send_message_transport = send_message_transport

    def __repr__(self) -> str:
        return "TelegramBotClient(bot_token=SecretStr('**********'))"

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        """Long-poll for pending updates. `timeout=0` returns immediately
        with whatever is already pending - used by `--once`.
        """
        token = self._bot_token.get_secret_value()
        body = self._get_updates_transport(token, offset, timeout)
        payload = json.loads(body)
        return payload.get("result", [])

    def send_message(self, chat_id: int, text: str) -> None:
        token = self._bot_token.get_secret_value()
        self._send_message_transport(token, chat_id, text)
