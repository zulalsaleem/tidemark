"""bot.py: symbol normalization, command parsing, authorization,
startup-backlog discard, offset persistence, rate-limit handling, and
outage survival via backoff. No live network anywhere.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tidemark.market_intel.bot import (
    COIN_USAGE_TEXT,
    HELP_TEXT,
    RATE_LIMITED_TEXT,
    UNKNOWN_COMMAND_TEXT,
    _parse_command,
    normalize_coin_input,
    run_forever,
    run_once,
)
from tidemark.market_intel.bot_state import BotStateStore
from tidemark.market_intel.client import RATE_LIMIT_PER_MINUTE
from tidemark.market_intel.errors import (
    RateLimitedError,
    TelegramConnectionError,
    TelegramHttpError,
)
from tidemark.market_intel.future_markets import FutureMarketsCache

VENUE = "binanceusdm"
ALLOWED_CHAT_ID = 111
OTHER_CHAT_ID = 222
NOW = dt.datetime(2026, 9, 27, 10, 0, tzinfo=dt.UTC)


# -- normalize_coin_input ------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["SOL", "$SOL", "sol", "sol/usdt:usdt", "SOL/USDT:USDT", " SOL ", "$sol"],
)
def test_normalize_coin_input_forms(raw: str) -> None:
    assert normalize_coin_input(raw) == "SOL/USDT:USDT"


# -- _parse_command --------------------------------------------------------


def test_parse_command_splits_command_and_argument() -> None:
    assert _parse_command("/coin SOL") == ("/coin", "SOL")


def test_parse_command_strips_bot_username_suffix() -> None:
    assert _parse_command("/coin@MyBot SOL") == ("/coin", "SOL")


def test_parse_command_lowercases_the_command_only() -> None:
    assert _parse_command("/COIN SOL") == ("/coin", "SOL")


def test_parse_command_handles_no_argument() -> None:
    assert _parse_command("/coin") == ("/coin", "")


def test_parse_command_returns_none_for_plain_text() -> None:
    assert _parse_command("hello there") is None


# -- fakes ----------------------------------------------------------------


class _FakeTelegram:
    def __init__(self, updates: list[dict] | None = None) -> None:
        self._updates = updates or []
        self.sent: list[tuple[int, str]] = []
        self.get_updates_calls: list[tuple] = []

    def get_updates(self, offset, timeout):
        self.get_updates_calls.append((offset, timeout))
        return self._updates

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class _FailingTelegram:
    """Raises a fixed sequence of errors, then behaves like `_FakeTelegram`."""

    def __init__(self, errors: list[Exception]) -> None:
        self._errors = list(errors)
        self.sent: list[tuple[int, str]] = []

    def get_updates(self, offset, timeout):
        if self._errors:
            raise self._errors.pop(0)
        return []

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def _market_row(**overrides) -> dict:
    base = {
        "symbol": "SOLUSDT_PERP.A",
        "exchange": "A",
        "base_asset": "SOL",
        "quote_asset": "USDT",
        "is_perpetual": True,
        "has_long_short_ratio_data": True,
        "has_ohlcv_data": True,
        "has_buy_sell_data": True,
        "oi_lq_vol_denominated_in": "BASE_ASSET",
    }
    base.update(overrides)
    return base


class _FakeCoinalyze:
    """Enough of CoinalyzeClient's surface for run_once/_handle_coin."""

    def __init__(
        self, future_markets_rows: list[dict] | None = None, calls_in_last_minute: int = 0
    ):
        self._rows = future_markets_rows if future_markets_rows is not None else [_market_row()]
        self.calls_in_last_minute = calls_in_last_minute
        self.raise_rate_limited = False

    def future_markets(self):
        return self._rows

    def open_interest(self, symbols, convert_to_usd=True):
        if self.raise_rate_limited:
            raise RateLimitedError(3, 1.0)
        return [{"symbol": symbols[0], "value": 1.0, "update": 1_700_000_000_000}]

    def funding_rate(self, symbols):
        return [{"symbol": symbols[0], "value": 0.001, "update": 1_700_000_000_000}]

    def predicted_funding_rate(self, symbols):
        return [{"symbol": symbols[0], "value": 0.002, "update": 1_700_000_000_000}]

    def open_interest_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        return [
            {"symbol": symbol, "history": [{"t": from_ts, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0}]}
        ]

    def long_short_ratio_history(self, symbol, interval, from_ts, to_ts):
        return [{"symbol": symbol, "history": [{"t": from_ts, "r": 1.0, "l": 50.0, "s": 50.0}]}]

    def liquidation_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        return [{"symbol": symbol, "history": [{"t": from_ts, "l": 1.0, "s": 1.0}]}]

    def ohlcv_history(self, symbol, interval, from_ts, to_ts):
        return [
            {
                "symbol": symbol,
                "history": [
                    {
                        "t": from_ts,
                        "o": 1.0,
                        "h": 1.0,
                        "l": 1.0,
                        "c": 1.0,
                        "v": 10.0,
                        "bv": 6.0,
                        "tx": 1,
                        "btx": 1,
                    }
                ],
            }
        ]


def _cache(coinalyze: _FakeCoinalyze) -> FutureMarketsCache:
    return FutureMarketsCache(coinalyze)


def _message_update(update_id: int, chat_id: int, text: str, date: dt.datetime = NOW) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": int(date.timestamp()),
            "chat": {"id": chat_id},
            "text": text,
        },
    }


# -- authorization: authorized gets a reply, unauthorized gets nothing --------


def test_authorized_chat_gets_a_reply(tmp_path) -> None:
    telegram = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, "/help")])
    coinalyze = _FakeCoinalyze()
    state = BotStateStore(tmp_path / "offset.json")

    outcome = run_once(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=False,
    )

    assert outcome.processed == 1
    assert telegram.sent == [(ALLOWED_CHAT_ID, HELP_TEXT)]


def test_unauthorized_chat_gets_nothing_and_the_attempt_is_logged(tmp_path, caplog) -> None:
    telegram = _FakeTelegram([_message_update(1, OTHER_CHAT_ID, "/coin SOL")])
    coinalyze = _FakeCoinalyze()
    state = BotStateStore(tmp_path / "offset.json")

    with caplog.at_level("WARNING"):
        outcome = run_once(
            telegram,
            coinalyze,
            _cache(coinalyze),
            state,
            ALLOWED_CHAT_ID,
            VENUE,
            NOW,
            discard_backlog=False,
        )

    assert telegram.sent == []  # absolutely nothing sent
    assert outcome.unauthorized == 1
    assert outcome.processed == 0
    assert any(str(OTHER_CHAT_ID) in record.message for record in caplog.records)
    # Never logs message content.
    assert not any("/coin" in record.message for record in caplog.records)


# -- /coin symbol resolution ---------------------------------------------------


def test_coin_command_with_each_input_form_resolves_to_the_same_symbol(tmp_path) -> None:
    for i, raw in enumerate(("SOL", "$SOL", "sol", "SOL/USDT:USDT")):
        telegram = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, f"/coin {raw}")])
        coinalyze = _FakeCoinalyze()
        state = BotStateStore(tmp_path / f"offset-{i}.json")

        run_once(
            telegram,
            coinalyze,
            _cache(coinalyze),
            state,
            ALLOWED_CHAT_ID,
            VENUE,
            NOW,
            discard_backlog=False,
        )

        assert len(telegram.sent) == 1
        chat_id, text = telegram.sent[0]
        assert chat_id == ALLOWED_CHAT_ID
        assert "SOL/USDT:USDT" in text


def test_coin_with_no_argument_gets_usage_text(tmp_path) -> None:
    telegram = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, "/coin")])
    coinalyze = _FakeCoinalyze()
    state = BotStateStore(tmp_path / "offset.json")

    run_once(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=False,
    )

    assert telegram.sent == [(ALLOWED_CHAT_ID, COIN_USAGE_TEXT)]


def test_unknown_symbol_gives_market_not_found(tmp_path) -> None:
    telegram = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, "/coin NOPE")])
    coinalyze = _FakeCoinalyze(future_markets_rows=[])  # nothing listed
    state = BotStateStore(tmp_path / "offset.json")

    run_once(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=False,
    )

    assert len(telegram.sent) == 1
    _, text = telegram.sent[0]
    assert "MARKET_NOT_FOUND" in text


def test_symbol_with_no_data_gives_unavailable_never_zero(tmp_path) -> None:
    class _NoDataCoinalyze(_FakeCoinalyze):
        def open_interest(self, symbols, convert_to_usd=True):
            return []

        def funding_rate(self, symbols):
            return []

        def predicted_funding_rate(self, symbols):
            return []

    telegram = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, "/coin SOL")])
    coinalyze = _NoDataCoinalyze()
    state = BotStateStore(tmp_path / "offset.json")

    run_once(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=False,
    )

    _, text = telegram.sent[0]
    assert "Open interest: UNAVAILABLE (NO_DATA:" in text
    assert "Open interest: 0" not in text


def test_unrecognized_command_gets_unknown_command_reply(tmp_path) -> None:
    telegram = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, "/frobnicate")])
    coinalyze = _FakeCoinalyze()
    state = BotStateStore(tmp_path / "offset.json")

    run_once(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=False,
    )

    assert telegram.sent == [(ALLOWED_CHAT_ID, UNKNOWN_COMMAND_TEXT)]


def test_start_command_gets_the_same_reply_as_help(tmp_path) -> None:
    telegram = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, "/start")])
    coinalyze = _FakeCoinalyze()
    state = BotStateStore(tmp_path / "offset.json")

    run_once(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=False,
    )

    assert telegram.sent == [(ALLOWED_CHAT_ID, HELP_TEXT)]


# -- offset persistence: never reprocessed ------------------------------------


def test_offset_is_persisted_so_a_second_run_does_not_reprocess(tmp_path) -> None:
    state_path = tmp_path / "offset.json"

    telegram1 = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, "/help")])
    run_once(
        telegram1,
        _FakeCoinalyze(),
        _cache(_FakeCoinalyze()),
        BotStateStore(state_path),
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=False,
    )
    assert len(telegram1.sent) == 1

    # A fresh telegram fake simulates Telegram never returning update_id=1
    # again once we've acked it with offset=2 - exactly what a real
    # getUpdates(offset=2) would do.
    persisted_offset = BotStateStore(state_path).load_offset()
    assert persisted_offset == 2

    telegram2 = _FakeTelegram([])  # Telegram has nothing new
    outcome2 = run_once(
        telegram2,
        _FakeCoinalyze(),
        _cache(_FakeCoinalyze()),
        BotStateStore(state_path),
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=False,
    )
    assert telegram2.get_updates_calls == [(2, 0)]
    assert outcome2.processed == 0
    assert telegram2.sent == []


# -- startup backlog is discarded ---------------------------------------------


def test_stale_backlog_is_discarded_on_startup_without_a_reply(tmp_path) -> None:
    stale_time = NOW - dt.timedelta(minutes=30)
    telegram = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, "/coin SOL", date=stale_time)])
    coinalyze = _FakeCoinalyze()
    state = BotStateStore(tmp_path / "offset.json")

    outcome = run_once(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=True,
    )

    assert outcome.discarded_stale == 1
    assert outcome.processed == 0
    assert telegram.sent == []
    # Still advances the offset - a discarded update is never replayed either.
    assert state.load_offset() == 2


def test_recent_message_is_not_discarded_even_with_discard_backlog_true(tmp_path) -> None:
    telegram = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, "/help", date=NOW)])
    coinalyze = _FakeCoinalyze()
    state = BotStateStore(tmp_path / "offset.json")

    outcome = run_once(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=True,
    )

    assert outcome.discarded_stale == 0
    assert outcome.processed == 1


# -- rate limiting: replies rather than crashing -------------------------------


def test_proactive_throttle_replies_when_budget_is_nearly_spent(tmp_path) -> None:
    telegram = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, "/coin SOL")])
    coinalyze = _FakeCoinalyze(calls_in_last_minute=RATE_LIMIT_PER_MINUTE)  # already at the cap
    state = BotStateStore(tmp_path / "offset.json")

    run_once(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=False,
    )

    assert telegram.sent == [(ALLOWED_CHAT_ID, RATE_LIMITED_TEXT)]


def test_rate_limited_error_from_the_client_replies_instead_of_crashing(tmp_path) -> None:
    telegram = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, "/coin SOL")])
    coinalyze = _FakeCoinalyze()
    coinalyze.raise_rate_limited = True
    state = BotStateStore(tmp_path / "offset.json")

    run_once(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=False,
    )

    assert telegram.sent == [(ALLOWED_CHAT_ID, RATE_LIMITED_TEXT)]


# -- outage survival: run_forever backs off and does not crash ----------------


def test_run_forever_survives_connection_errors_with_backoff(tmp_path) -> None:
    telegram = _FailingTelegram(
        [TelegramConnectionError("timeout"), TelegramConnectionError("timeout")]
    )
    coinalyze = _FakeCoinalyze()
    state = BotStateStore(tmp_path / "offset.json")
    slept: list[float] = []

    run_forever(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        sleep_fn=slept.append,
        clock=lambda: NOW,
        max_iterations=3,
    )

    assert slept == [1.0, 2.0]  # exponential backoff, never crashed


def test_run_forever_raises_on_unrecoverable_auth_error() -> None:
    telegram = _FailingTelegram([TelegramHttpError(401, "Unauthorized")])
    coinalyze = _FakeCoinalyze()

    with pytest.raises(TelegramHttpError) as exc_info:
        run_forever(
            telegram,
            coinalyze,
            _cache(coinalyze),
            BotStateStore("unused.json"),
            ALLOWED_CHAT_ID,
            VENUE,
            sleep_fn=lambda s: None,
            clock=lambda: NOW,
            max_iterations=3,
        )
    assert exc_info.value.status_code == 401


def test_run_forever_backs_off_but_keeps_retrying_on_5xx(tmp_path) -> None:
    telegram = _FailingTelegram([TelegramHttpError(502, "Bad Gateway")])
    coinalyze = _FakeCoinalyze()
    state = BotStateStore(tmp_path / "offset.json")
    slept: list[float] = []

    run_forever(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        sleep_fn=slept.append,
        clock=lambda: NOW,
        max_iterations=2,
    )

    assert slept == [1.0]


# -- secret hygiene ---------------------------------------------------------


def test_no_secret_appears_in_any_sent_message(tmp_path) -> None:
    telegram = _FakeTelegram([_message_update(1, ALLOWED_CHAT_ID, "/coin SOL")])
    coinalyze = _FakeCoinalyze()
    state = BotStateStore(tmp_path / "offset.json")

    run_once(
        telegram,
        coinalyze,
        _cache(coinalyze),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=False,
    )

    for _, text in telegram.sent:
        assert "TIDEMARK_" not in text
        assert "token" not in text.lower()
        assert "api_key" not in text.lower()
