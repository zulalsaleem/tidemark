"""Read-only Telegram alert interface.

Sends informational alerts only. This module never places orders, never
holds private keys, and never has exchange trading permissions — it is a
one-way notifier from Tidemark to a human. Bot token and chat ID come from
`tidemark.config.settings.Settings` and are never logged.
"""

from __future__ import annotations

from tidemark.data.models import ContextRecord


class TelegramNotifier:
    """Sends read-only alerts to a configured Telegram chat.

    Not implemented in Phase 0.
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id

    def send_alert(self, record: ContextRecord) -> None:
        """Send a read-only alert describing a rulebook evaluation result."""
        raise NotImplementedError
