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


def test_repr_and_str_do_not_leak_token() -> None:
    notifier = TelegramNotifier(bot_token="super-secret-token", chat_id="123")
    assert "super-secret-token" not in repr(notifier)
    assert "super-secret-token" not in str(notifier)
