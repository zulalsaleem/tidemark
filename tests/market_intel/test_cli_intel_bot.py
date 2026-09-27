"""`tidemark intel bot --once`: missing settings exit cleanly, and a
fully faked run prints a summary. No live network: `_telegram_bot_client`
and `_coinalyze_client` are monkeypatched, exactly like `_notifier` is
swapped out elsewhere in this suite.
"""

from __future__ import annotations

from typer.testing import CliRunner

from tidemark import cli as cli_module
from tidemark.cli import app
from tidemark.market_intel.errors import TelegramConnectionError, TelegramHttpError

runner = CliRunner()


class _FakeTelegram:
    def __init__(self, updates=None) -> None:
        self._updates = updates or []
        self.sent = []

    def get_updates(self, offset, timeout):
        return self._updates

    def send_message(self, chat_id, text) -> None:
        self.sent.append((chat_id, text))


class _UnreachableTelegram:
    """Simulates the real failure mode seen live in this environment:
    api.telegram.org unreachable (network filtering/firewall)."""

    def get_updates(self, offset, timeout):
        raise TelegramConnectionError("<urlopen error timed out>")


class _RejectingTelegram:
    def get_updates(self, offset, timeout):
        raise TelegramHttpError(401, "Unauthorized")


class _FakeCoinalyze:
    def future_markets(self):
        return []


# -- missing settings exit cleanly --------------------------------------------


def test_missing_allowed_chat_id_exits_cleanly(monkeypatch) -> None:
    monkeypatch.delenv("TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID", raising=False)

    result = runner.invoke(app, ["intel", "bot", "--once"])

    assert result.exit_code == 1
    assert "TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID is not set" in result.output


def test_missing_bot_token_exits_cleanly(monkeypatch) -> None:
    monkeypatch.setenv("TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID", "111")
    monkeypatch.setenv("TIDEMARK_TELEGRAM_BOT_TOKEN", "")

    result = runner.invoke(app, ["intel", "bot", "--once"])

    assert result.exit_code == 1
    assert "TIDEMARK_TELEGRAM_BOT_TOKEN is not set" in result.output


def test_missing_coinalyze_key_exits_cleanly(monkeypatch) -> None:
    monkeypatch.setenv("TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID", "111")
    monkeypatch.setattr(cli_module, "_telegram_bot_client", lambda settings: _FakeTelegram())
    monkeypatch.setenv("TIDEMARK_COINALYZE_API_KEY", "")

    result = runner.invoke(app, ["intel", "bot", "--once"])

    assert result.exit_code == 1
    assert "TIDEMARK_COINALYZE_API_KEY is not set" in result.output


# -- --once processes pending updates and exits --------------------------------


def test_once_prints_a_summary_and_exits_cleanly(monkeypatch) -> None:
    monkeypatch.setenv("TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID", "111")
    monkeypatch.setattr(cli_module, "_telegram_bot_client", lambda settings: _FakeTelegram([]))
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze())

    result = runner.invoke(app, ["intel", "bot", "--once"])

    assert result.exit_code == 0
    assert "updates_seen=0" in result.output
    assert "processed=0" in result.output


# -- a Telegram connection failure exits cleanly, never a raw traceback -------


def test_once_reports_a_telegram_connection_failure_cleanly(monkeypatch) -> None:
    monkeypatch.setenv("TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID", "111")
    monkeypatch.setattr(cli_module, "_telegram_bot_client", lambda settings: _UnreachableTelegram())
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze())

    result = runner.invoke(app, ["intel", "bot", "--once"])

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Could not reach Telegram" in result.output


def test_once_reports_a_telegram_http_error_cleanly(monkeypatch) -> None:
    monkeypatch.setenv("TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID", "111")
    monkeypatch.setattr(cli_module, "_telegram_bot_client", lambda settings: _RejectingTelegram())
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze())

    result = runner.invoke(app, ["intel", "bot", "--once"])

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "HTTP 401" in result.output
