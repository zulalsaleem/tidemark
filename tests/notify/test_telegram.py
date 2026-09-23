"""Telegram notifier: send-only, bounded retry, no real network anywhere."""

import datetime as dt

from tidemark.data.models import ContextRecord
from tidemark.notify.telegram import DISCLAIMER, TelegramNotifier, build_message

EVALUATED_AT = dt.datetime(2026, 9, 23, 4, tzinfo=dt.UTC)

_FORBIDDEN_WORDS = ["entry", "stop", "sl", "tp", "target", "r:r", "buy", "sell"]


def _record(**overrides) -> ContextRecord:
    defaults = dict(
        asset="BTC/USDT:USDT",
        evaluated_at=EVALUATED_AT,
        rule_version="section-01-v1.1",
        state="BULLISH",
        watch="LONG_WATCH",
        grade="A",
        reason_code="MAJOR_SUPPORT_FIB",
        active_levels=[
            {
                "role": "support",
                "price": 79918.90,
                "zone_low": 79800.0,
                "zone_high": 80000.0,
                "touches": 7,
                "is_major": True,
                "held": True,
                "source": "swing_high_cluster",
                "formed_at": EVALUATED_AT.isoformat(),
            }
        ],
        fib={"nearest_level": "0.618"},
        swings_used=[],
    )
    defaults.update(overrides)
    return ContextRecord(**defaults)


# --- message format ----------------------------------------------------------


def test_message_matches_the_required_shape_exactly() -> None:
    message = build_message(_record(), "WATCH_OPENED", "BTC/USDT:USDT")

    expected = (
        "\U0001f7e2 LONG WATCH — BTC/USDT (Grade A)\n"
        "\n"
        "4H: BULLISH\n"
        "Major support: 79,918.90 (7 touches)\n"
        "Fib zone: 0.618\n"
        "Reason: BULLISH + holds major support + Fib zone\n"
        "\n"
        "Rulebook: section-01-v1.1\n"
        "\n"
        f"{DISCLAIMER}"
    )
    assert message == expected


def test_message_contains_disclaimer_and_no_trading_instructions() -> None:
    message = build_message(_record(), "WATCH_OPENED", "BTC/USDT:USDT")

    assert DISCLAIMER in message

    # The disclaimer's own negation sentence ("No entry, stop, or target
    # defined.") necessarily contains some of these words - that's the
    # point of the disclaimer. What must never happen is any of these
    # words appearing *outside* it, i.e. Tidemark never emits actual
    # trading instructions.
    remainder = message.replace(DISCLAIMER, "").lower()
    for word in _FORBIDDEN_WORDS:
        assert word not in remainder, f"unexpected {word!r} outside the disclaimer"


def test_short_watch_uses_red_circle() -> None:
    record = _record(state="BEARISH", watch="SHORT_WATCH", reason_code="MAJOR_RESISTANCE")
    record.active_levels = [
        {
            "role": "resistance",
            "price": 82000.0,
            "zone_low": 81900.0,
            "zone_high": 82100.0,
            "touches": 3,
            "is_major": True,
            "held": True,
            "source": "prev_week_high",
            "formed_at": EVALUATED_AT.isoformat(),
        }
    ]
    record.fib = {}

    message = build_message(record, "WATCH_OPENED", "BTC/USDT:USDT")

    assert message.startswith("\U0001f534 SHORT WATCH")
    assert "Major resistance: 82,000.00 (3 touches)" in message
    assert "Fib zone" not in message  # no valid leg -> line omitted


def test_watch_closed_uses_white_circle_and_no_grade() -> None:
    record = _record(state="NEUTRAL", watch="WAIT", grade=None, reason_code="NEUTRAL_STRUCTURE")
    record.active_levels = []
    record.fib = {}

    message = build_message(record, "WATCH_CLOSED", "BTC/USDT:USDT")

    assert message.startswith("⚪ WATCH CLOSED — BTC/USDT")
    assert "Grade" not in message.split("\n")[0]
    assert "Reason: NEUTRAL" in message


def test_structure_broken_uses_warning_emoji() -> None:
    record = _record(
        state="STRUCTURE_BROKEN_BULL", watch="WAIT", grade=None, reason_code="STRUCTURE_BROKEN"
    )
    record.active_levels = []
    record.fib = {}

    message = build_message(record, "STRUCTURE_BROKEN", "BTC/USDT:USDT")

    assert message.startswith("⚠️ STRUCTURE BROKEN — BTC/USDT")
    assert "Reason: STRUCTURE_BROKEN_*" in message


def test_structure_resolved_uses_warning_emoji() -> None:
    record = _record(state="NEUTRAL", watch="WAIT", grade=None, reason_code="NEUTRAL_STRUCTURE")
    record.active_levels = []
    record.fib = {}

    message = build_message(record, "STRUCTURE_RESOLVED", "BTC/USDT:USDT")

    assert message.startswith("⚠️ STRUCTURE RESOLVED — BTC/USDT")


def test_display_symbol_strips_perpetual_suffix() -> None:
    message = build_message(_record(), "WATCH_OPENED", "BTC/USDT:USDT")
    assert "BTC/USDT " in message or "BTC/USDT\n" in message
    assert "BTC/USDT:USDT" not in message


# --- sending: no real network, bounded retry, missing credentials -----------


def test_send_text_calls_send_fn_and_returns_true_on_success() -> None:
    calls = []
    notifier = TelegramNotifier(
        bot_token="tok",
        chat_id="123",
        send_fn=lambda token, chat_id, text: calls.append((token, chat_id, text)),
        sleep_fn=lambda seconds: None,
    )

    assert notifier.send_text("hello") is True
    assert calls == [("tok", "123", "hello")]


def test_send_text_retries_then_succeeds() -> None:
    attempts = {"n": 0}

    def flaky_send(token, chat_id, text):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("simulated transient failure")

    slept = []
    notifier = TelegramNotifier(
        bot_token="tok",
        chat_id="123",
        max_retries=5,
        send_fn=flaky_send,
        sleep_fn=lambda seconds: slept.append(seconds),
    )

    assert notifier.send_text("hello") is True
    assert attempts["n"] == 3
    assert len(slept) == 2  # two retries before the third attempt succeeded


def test_send_text_gives_up_after_max_retries_and_returns_false() -> None:
    def always_fails(token, chat_id, text):
        raise ConnectionError("simulated permanent failure")

    notifier = TelegramNotifier(
        bot_token="tok",
        chat_id="123",
        max_retries=2,
        send_fn=always_fails,
        sleep_fn=lambda seconds: None,
    )

    assert notifier.send_text("hello") is False


def test_missing_bot_token_skips_send_without_crashing() -> None:
    calls = []
    notifier = TelegramNotifier(
        bot_token=None,
        chat_id="123",
        send_fn=lambda *a: calls.append(a),
    )

    assert notifier.send_text("hello") is False
    assert calls == []


def test_missing_chat_id_skips_send_without_crashing() -> None:
    calls = []
    notifier = TelegramNotifier(
        bot_token="tok",
        chat_id=None,
        send_fn=lambda *a: calls.append(a),
    )

    assert notifier.send_text("hello") is False
    assert calls == []


def test_empty_string_credentials_are_treated_as_missing() -> None:
    notifier = TelegramNotifier(bot_token="", chat_id="")
    assert notifier.is_configured is False


def test_send_alert_builds_and_sends_the_message() -> None:
    calls = []
    notifier = TelegramNotifier(
        bot_token="tok",
        chat_id="123",
        send_fn=lambda token, chat_id, text: calls.append(text),
    )

    sent = notifier.send_alert(_record(), "WATCH_OPENED", "BTC/USDT:USDT")

    assert sent is True
    assert len(calls) == 1
    assert calls[0] == build_message(_record(), "WATCH_OPENED", "BTC/USDT:USDT")


# --- no order placement, no token leakage ------------------------------------


def test_notifier_has_no_order_placement_methods() -> None:
    forbidden = {"place_order", "create_order", "cancel_order", "submit_order"}
    assert forbidden.isdisjoint(dir(TelegramNotifier))


def test_repr_and_str_do_not_leak_token() -> None:
    notifier = TelegramNotifier(bot_token="super-secret-token", chat_id="123")
    assert "super-secret-token" not in repr(notifier)
    assert "super-secret-token" not in str(notifier)
