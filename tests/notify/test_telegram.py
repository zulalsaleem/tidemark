"""Telegram notifier is read-only, and unimplemented in Phase 0."""

import pytest

from tidemark.notify.telegram import TelegramNotifier


def test_send_alert_not_implemented() -> None:
    notifier = TelegramNotifier(bot_token="dummy", chat_id="123")
    with pytest.raises(NotImplementedError):
        notifier.send_alert(record=None)


def test_notifier_has_no_order_placement_methods() -> None:
    forbidden = {"place_order", "create_order", "cancel_order", "submit_order"}
    assert forbidden.isdisjoint(dir(TelegramNotifier))
