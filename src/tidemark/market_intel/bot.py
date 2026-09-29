"""The /coin Telegram bot: long polling, chat-ID authorization, command
dispatch. See docs/adr/0011-market-intelligence-layer.md for the
polling/authorization/scope decisions.

Security, non-negotiable (see the ADR):
- Authorization is by numeric chat ID only (`TIDEMARK_TELEGRAM_ALLOWED_
  CHAT_ID`), never username or display name - both are attacker-
  controlled.
- An unauthorized chat is SILENTLY IGNORED: no reply of any kind, only a
  log line with the chat id and timestamp - never the message text.
- This module never touches an exchange, a private key, or anything
  that could place an order. It reads Coinalyze and Telegram, and
  writes only Telegram replies plus its own offset file.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine

from tidemark.market_intel.bot_state import BotStateStore
from tidemark.market_intel.client import RATE_LIMIT_PER_MINUTE, CoinalyzeClient
from tidemark.market_intel.coin_universe_context import gather_coin_universe_context
from tidemark.market_intel.errors import (
    RateLimitedError,
    TelegramConnectionError,
    TelegramHttpError,
    UnsupportedVenueError,
)
from tidemark.market_intel.future_markets import FutureMarketsCache
from tidemark.market_intel.position_flow import classify_snapshot
from tidemark.market_intel.service import fetch_market_intel
from tidemark.market_intel.telegram_client import TelegramBotClient
from tidemark.market_intel.telegram_render import render_snapshot

logger = logging.getLogger(__name__)

# A /coin lookup can hit up to 7 Coinalyze endpoints for one symbol
# (open interest, funding rate, predicted funding rate, open-interest
# history, long/short ratio history, liquidation history, ohlcv
# history - see service.py). The universe-context comparison and the
# position-flow classifier (coin_universe_context.py,
# position_flow_classifier.py) both read fields already computed on
# that same snapshot - no additional calls. Checked before dispatching
# so a single burst of /coin requests can't blow the documented
# 40/minute budget; RateLimitedError from the client itself (see
# client.py) is still caught as a fallback in case this estimate
# undercounts.
ESTIMATED_CALL_COST_PER_COIN_LOOKUP = 7

# Default when a caller doesn't pass its own (e.g. `settings.
# universe_context_stale_after_hours` from the CLI) - a row older than
# this is still shown, with its age stated plainly, rather than hidden.
DEFAULT_CONTEXT_STALE_AFTER = dt.timedelta(hours=12)

STARTUP_BACKLOG_MAX_AGE = dt.timedelta(minutes=5)
LONG_POLL_TIMEOUT_SECONDS = 30
INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0
# Telegram statuses that mean the token itself is wrong - retrying
# forever cannot fix these, so run_forever raises rather than looping.
_UNRECOVERABLE_STATUS_CODES = frozenset({401, 403})

HELP_TEXT = (
    "Tidemark market intelligence bot — read-only.\n\n"
    "/coin <SYMBOL> — Coinalyze derivatives snapshot for a Binance USDT-M "
    "perpetual (e.g. /coin SOL, /coin $SOL, /coin BTC/USDT:USDT)\n"
    "/help — this message\n"
    "/start — this message\n\n"
    "This is market information only, not trading advice. Nothing here is a "
    "trade signal, and this bot never places, suggests, or confirms any order."
)
UNKNOWN_COMMAND_TEXT = "Unknown command. Try /help."
COIN_USAGE_TEXT = "Usage: /coin <SYMBOL> (e.g. /coin SOL)"
RATE_LIMITED_TEXT = "Coinalyze rate limit reached for this minute — try again shortly."


def normalize_coin_input(raw: str) -> str:
    """Accepts "SOL", "$SOL", "sol", "SOL/USDT:USDT" and normalizes each
    to the ccxt unified perpetual form, e.g. "SOL/USDT:USDT". Any Binance
    USDT-M perpetual is allowed here - not only the research universe;
    `to_coinalyze_symbol` + the /future-markets cache decide whether it
    actually exists.
    """
    text = raw.strip().upper()
    if text.startswith("$"):
        text = text[1:]
    base = text.split("/")[0].strip()
    return f"{base}/USDT:USDT"


def _parse_command(text: str) -> tuple[str, str] | None:
    """Returns `(command, argument)` for a `/command arg` message, or
    `None` if `text` isn't a slash command at all. `/coin@BotName arg`
    (Telegram's group-chat suffix) is normalized to `/coin`.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    return command, argument


def _is_stale(message: dict, now: dt.datetime, max_age: dt.timedelta) -> bool:
    message_at = dt.datetime.fromtimestamp(message["date"], tz=dt.UTC)
    return now - message_at > max_age


def _handle_coin(
    telegram: TelegramBotClient,
    coinalyze: CoinalyzeClient,
    cache: FutureMarketsCache,
    chat_id: int,
    argument: str,
    venue: str,
    now: dt.datetime,
    context_engine: Engine | None = None,
    context_stale_after: dt.timedelta = DEFAULT_CONTEXT_STALE_AFTER,
) -> None:
    if not argument:
        telegram.send_message(chat_id, COIN_USAGE_TEXT)
        return

    if coinalyze.calls_in_last_minute + ESTIMATED_CALL_COST_PER_COIN_LOOKUP > RATE_LIMIT_PER_MINUTE:
        telegram.send_message(chat_id, RATE_LIMITED_TEXT)
        return

    ccxt_symbol = normalize_coin_input(argument)
    try:
        snapshot = fetch_market_intel(coinalyze, cache, ccxt_symbol, venue, now)
    except RateLimitedError:
        telegram.send_message(chat_id, RATE_LIMITED_TEXT)
        return
    except UnsupportedVenueError as exc:
        telegram.send_message(chat_id, f"No Coinalyze mapping for venue {exc.venue!r}.")
        return

    universe_context = None
    if context_engine is not None:
        universe_context = gather_coin_universe_context(
            context_engine, snapshot, now, context_stale_after
        )

    position_flow = classify_snapshot(snapshot)

    telegram.send_message(chat_id, render_snapshot(snapshot, universe_context, position_flow))


def _handle_message(
    telegram: TelegramBotClient,
    coinalyze: CoinalyzeClient,
    cache: FutureMarketsCache,
    chat_id: int,
    text: str,
    venue: str,
    now: dt.datetime,
    context_engine: Engine | None = None,
    context_stale_after: dt.timedelta = DEFAULT_CONTEXT_STALE_AFTER,
) -> None:
    parsed = _parse_command(text)
    if parsed is None:
        telegram.send_message(chat_id, UNKNOWN_COMMAND_TEXT)
        return

    command, argument = parsed
    if command == "/coin":
        _handle_coin(
            telegram,
            coinalyze,
            cache,
            chat_id,
            argument,
            venue,
            now,
            context_engine,
            context_stale_after,
        )
    elif command in ("/help", "/start"):
        telegram.send_message(chat_id, HELP_TEXT)
    else:
        telegram.send_message(chat_id, UNKNOWN_COMMAND_TEXT)


@dataclass(frozen=True)
class RunOnceOutcome:
    updates_seen: int
    processed: int
    discarded_stale: int
    unauthorized: int


def run_once(
    telegram: TelegramBotClient,
    coinalyze: CoinalyzeClient,
    cache: FutureMarketsCache,
    state: BotStateStore,
    allowed_chat_id: int,
    venue: str,
    now: dt.datetime,
    discard_backlog: bool = True,
    poll_timeout: int = 0,
    backlog_max_age: dt.timedelta = STARTUP_BACKLOG_MAX_AGE,
    context_engine: Engine | None = None,
    context_stale_after: dt.timedelta = DEFAULT_CONTEXT_STALE_AFTER,
) -> RunOnceOutcome:
    """Fetch whatever updates are pending, dispatch each, and persist the
    offset after every single one - so a crash mid-batch reprocesses at
    most the one update it was handling when it died, never the whole
    batch, and a completed update is never replayed on the next call.
    """
    offset = state.load_offset()
    updates = telegram.get_updates(offset=offset, timeout=poll_timeout)

    processed = discarded_stale = unauthorized = 0
    for update in updates:
        update_id = update["update_id"]
        message = update.get("message")

        if message is None:
            state.save_offset(update_id + 1)
            continue

        if discard_backlog and _is_stale(message, now, backlog_max_age):
            discarded_stale += 1
            state.save_offset(update_id + 1)
            continue

        chat_id = message["chat"]["id"]
        if chat_id != allowed_chat_id:
            logger.warning("unauthorized Telegram chat_id=%s at %s", chat_id, now.isoformat())
            unauthorized += 1
            state.save_offset(update_id + 1)
            continue

        _handle_message(
            telegram,
            coinalyze,
            cache,
            chat_id,
            message.get("text", ""),
            venue,
            now,
            context_engine,
            context_stale_after,
        )
        processed += 1
        state.save_offset(update_id + 1)

    return RunOnceOutcome(
        updates_seen=len(updates),
        processed=processed,
        discarded_stale=discarded_stale,
        unauthorized=unauthorized,
    )


def run_forever(
    telegram: TelegramBotClient,
    coinalyze: CoinalyzeClient,
    cache: FutureMarketsCache,
    state: BotStateStore,
    allowed_chat_id: int,
    venue: str,
    poll_timeout: int = LONG_POLL_TIMEOUT_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    max_iterations: int | None = None,
    context_engine: Engine | None = None,
    context_stale_after: dt.timedelta = DEFAULT_CONTEXT_STALE_AFTER,
) -> None:
    """Poll forever. The first pass discards a stale startup backlog;
    every pass after that does not. A network failure (or a Telegram
    5xx) backs off exponentially, capped at `MAX_BACKOFF_SECONDS`, and
    is retried - the process survives an outage rather than exiting. A
    401/403 (bad bot token) is unrecoverable and is raised instead of
    retried forever. `max_iterations` exists only for tests; production
    callers omit it and this never returns on its own.
    """
    first_pass = True
    backoff = INITIAL_BACKOFF_SECONDS
    iterations = 0

    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        try:
            run_once(
                telegram,
                coinalyze,
                cache,
                state,
                allowed_chat_id,
                venue,
                clock(),
                discard_backlog=first_pass,
                poll_timeout=poll_timeout,
                context_engine=context_engine,
                context_stale_after=context_stale_after,
            )
            first_pass = False
            backoff = INITIAL_BACKOFF_SECONDS
        except TelegramHttpError as exc:
            if exc.status_code in _UNRECOVERABLE_STATUS_CODES:
                raise
            logger.warning(
                "Telegram polling failed (HTTP %d), retrying in %.1fs", exc.status_code, backoff
            )
            sleep_fn(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
        except TelegramConnectionError as exc:
            logger.warning("Telegram polling failed (%s), retrying in %.1fs", exc.detail, backoff)
            sleep_fn(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
