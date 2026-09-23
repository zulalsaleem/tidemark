"""Read-only, send-only Telegram alert interface.

Sends informational alerts only. This module never places orders, never
holds private keys, and never has exchange trading permissions — it is a
one-way notifier from Tidemark to a human. Sending is all it does: no
polling, no webhook, no command handling, no inline keyboards — those
arrive in a later phase, if ever. Bot token and chat ID come from
`tidemark.config.settings.Settings` and are never logged; the token is
always held as `SecretStr`.

A Telegram failure — missing credentials or a network error — must never
crash the run. `send_text`/`send_alert` catch everything, log, and
return whether the message actually went out; the journal write that
precedes a send is already committed and is never rolled back by a
failure here (see `journal/records.py` and PART D wiring in `cli.py`).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import SecretStr

from tidemark.context.htf import BEARISH, BULLISH, LONG_WATCH, MATRIX_ROW_TEXT
from tidemark.data.models import ContextRecord
from tidemark.journal.changes import (
    GRADE_DOWNGRADED,
    GRADE_UPGRADED,
    STRUCTURE_BROKEN,
    STRUCTURE_RESOLVED,
    WATCH_CLOSED,
    WATCH_FLIPPED,
    WATCH_OPENED,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_BACKOFF_SECONDS = 1.0
TELEGRAM_API_BASE = "https://api.telegram.org"

DISCLAIMER = "⚠️ Context only.\nNo entry, stop, or target defined.\n1H/15M/5M rules not yet built."

_GRADE_REASONS = (WATCH_OPENED, GRADE_UPGRADED, GRADE_DOWNGRADED, WATCH_FLIPPED)


def _display_symbol(asset: str) -> str:
    """Cosmetic only: "BTC/USDT:USDT" -> "BTC/USDT". Never used for lookups."""
    return asset.split(":")[0]


def _header(alert_reason: str, record: ContextRecord, symbol: str) -> str:
    if alert_reason in _GRADE_REASONS:
        emoji = "\U0001f7e2" if record.watch == LONG_WATCH else "\U0001f534"
        direction = "LONG WATCH" if record.watch == LONG_WATCH else "SHORT WATCH"
        grade_suffix = f" (Grade {record.grade})" if record.grade else ""
        return f"{emoji} {direction} — {symbol}{grade_suffix}"
    if alert_reason == WATCH_CLOSED:
        return f"⚪ WATCH CLOSED — {symbol}"
    if alert_reason in (STRUCTURE_BROKEN, STRUCTURE_RESOLVED):
        label = "STRUCTURE BROKEN" if alert_reason == STRUCTURE_BROKEN else "STRUCTURE RESOLVED"
        return f"⚠️ {label} — {symbol}"
    raise ValueError(f"unknown alert reason: {alert_reason!r}")


def _body_lines(record: ContextRecord) -> list[str]:
    lines = [f"4H: {record.state}"]

    if record.state == BULLISH:
        held = [lvl for lvl in record.active_levels if lvl.get("held") and lvl["role"] == "support"]
        if held:
            top = max(held, key=lambda lvl: lvl["touches"])
            lines.append(f"Major support: {top['price']:,.2f} ({top['touches']} touches)")
    elif record.state == BEARISH:
        held = [
            lvl for lvl in record.active_levels if lvl.get("held") and lvl["role"] == "resistance"
        ]
        if held:
            top = max(held, key=lambda lvl: lvl["touches"])
            lines.append(f"Major resistance: {top['price']:,.2f} ({top['touches']} touches)")

    if record.fib:
        lines.append(f"Fib zone: {record.fib['nearest_level']}")

    # PART C: the reason line is the matrix row's own condition text,
    # never free-form — quoted verbatim from section-01-htf-context-v1.1.md.
    lines.append(f"Reason: {MATRIX_ROW_TEXT[record.reason_code]}")
    return lines


def build_message(record: ContextRecord, alert_reason: str, symbol: str) -> str:
    """Render the fixed alert shape for one (record, alert_reason) pair."""
    display = _display_symbol(symbol)
    header = _header(alert_reason, record, display)
    body = "\n".join(_body_lines(record))
    return f"{header}\n\n{body}\n\nRulebook: {record.rule_version}\n\n{DISCLAIMER}"


def _http_send(token: str, chat_id: str, text: str) -> None:
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=10):
        pass


@dataclass(frozen=True)
class TelegramSendError:
    """Classifies why the most recent `send_text` attempt failed.

    `kind="connection"` means the request never reached Telegram at all
    (timeout, DNS failure, SSL handshake failure, connection refused,
    ...) — `detail` is the raw error, and nothing about it says anything
    about whether the credentials are valid. `kind="http_error"` means
    Telegram's API answered with a non-2xx status — `status_code` and
    `detail` (Telegram's own "description" field where available) then
    say something real about *why* it was rejected, e.g. 401 for a bad
    bot token, or 400 with "chat not found" for a bad chat ID.
    """

    kind: str  # "connection" or "http_error"
    detail: str
    status_code: int | None = None


def _classify_error(exc: Exception) -> TelegramSendError:
    if isinstance(exc, urllib.error.HTTPError):
        return TelegramSendError(
            kind="http_error", detail=_telegram_error_description(exc), status_code=exc.code
        )
    return TelegramSendError(kind="connection", detail=str(exc))


def _telegram_error_description(exc: urllib.error.HTTPError) -> str:
    """Best-effort extraction of Telegram's own error text from the
    response body (`{"ok": false, "description": "..."}`). Falls back to
    the HTTP reason phrase if the body isn't readable or isn't JSON —
    this must never raise, it's only ever called while already handling
    a failure.
    """
    try:
        payload = json.loads(exc.read())
        description = payload.get("description")
        if description:
            return str(description)
    except Exception:
        pass
    return exc.reason or str(exc)


class TelegramNotifier:
    """Sends read-only alerts to a configured Telegram chat.

    The bot token is held as `SecretStr`, never a plain `str`, so it can
    never leak through a `repr()`, `str()`, or accidental log call.
    """

    def __init__(
        self,
        bot_token: str | SecretStr | None,
        chat_id: str | None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_backoff_seconds: float = DEFAULT_BASE_BACKOFF_SECONDS,
        sleep_fn: Callable[[float], None] = time.sleep,
        send_fn: Callable[[str, str, str], None] = _http_send,
    ) -> None:
        self._bot_token = (
            bot_token
            if bot_token is None or isinstance(bot_token, SecretStr)
            else SecretStr(bot_token)
        )
        self._chat_id = chat_id
        self._max_retries = max_retries
        self._base_backoff_seconds = base_backoff_seconds
        self._sleep_fn = sleep_fn
        self._send_fn = send_fn
        self._last_error: TelegramSendError | None = None

    def __repr__(self) -> str:
        return f"TelegramNotifier(chat_id={self._chat_id!r})"

    @property
    def is_configured(self) -> bool:
        has_token = self._bot_token is not None and bool(self._bot_token.get_secret_value())
        return has_token and bool(self._chat_id)

    @property
    def last_error(self) -> TelegramSendError | None:
        """Why the most recent `send_text` call failed, or `None` if it
        succeeded, was never attempted (missing credentials), or hasn't
        been called yet. Reset at the start of every `send_text` call.
        """
        return self._last_error

    def send_text(self, text: str) -> bool:
        """Send a raw message. Returns whether it was actually sent.

        Missing credentials: logs a warning and returns False, no send
        attempted. Transient network failure: retries up to
        `max_retries` times with exponential backoff; on final failure,
        logs and returns False. Never raises.
        """
        self._last_error = None
        if not self.is_configured:
            logger.warning("Telegram not configured (missing bot token or chat id) - skipping send")
            return False

        token = self._bot_token.get_secret_value()
        attempt = 0
        while True:
            try:
                self._send_fn(token, self._chat_id, text)
                self._last_error = None
                return True
            except Exception as exc:
                self._last_error = _classify_error(exc)
                attempt += 1
                if attempt > self._max_retries:
                    logger.error(
                        "Telegram send failed after %d retries: %s", self._max_retries, exc
                    )
                    return False
                backoff = self._base_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "transient Telegram send error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt,
                    self._max_retries,
                    backoff,
                    exc,
                )
                self._sleep_fn(backoff)

    def send_alert(self, record: ContextRecord, alert_reason: str, symbol: str) -> bool:
        """Send a read-only alert describing an evaluation change."""
        return self.send_text(build_message(record, alert_reason, symbol))
