"""Read-only Telegram alert interface.

Sends informational alerts only. This module never places orders, never
holds private keys, and never has exchange trading permissions — it is a
one-way notifier from Tidemark to a human. Bot token and chat ID come from
`tidemark.config.settings.Settings` and are never logged.
"""

from __future__ import annotations

from pydantic import SecretStr

from tidemark.data.models import ContextRecord


class TelegramNotifier:
    """Sends read-only alerts to a configured Telegram chat.

    The bot token is held as `SecretStr`, never a plain `str`, so it can
    never leak through a `repr()`, `str()`, or accidental log call.

    Not implemented in Phase 0.
    """

    def __init__(self, bot_token: str | SecretStr, chat_id: str) -> None:
        self._bot_token = bot_token if isinstance(bot_token, SecretStr) else SecretStr(bot_token)
        self._chat_id = chat_id

    def __repr__(self) -> str:
        return f"TelegramNotifier(chat_id={self._chat_id!r})"

    def send_alert(self, record: ContextRecord) -> None:
        """Send a read-only alert describing a rulebook evaluation result."""
        raise NotImplementedError
