"""Phase A /coin reply through the REAL bot dispatch path: BTC/ETH context,
budget pre-check, failure handling, and the output-language guard applied
to every rendered path. No live network. Section 1 records are written with
the production path (`build_journal_entry` + `TidemarkStore.save_journal_entry`).
"""

from __future__ import annotations

import re

from test_market_context import (
    ALLOWED_CHAT_ID,
    FRESH_EVALUATED_AT,
    NOW,
    SOL,
    STALE_EVALUATED_AT,
    VENUE,
    _cache,
    _FakeMarketClient,
    _reading,
)

from tidemark.data.models import ContextRecord
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.journal.records import build_journal_entry
from tidemark.market_intel.bot import RATE_LIMITED_TEXT, run_once
from tidemark.market_intel.bot_state import BotStateStore
from tidemark.market_intel.market_context import BTC_SYMBOL, ETH_SYMBOL
from tidemark.market_intel.service import fetch_market_intel
from tidemark.market_intel.telegram_render import render_snapshot

# -- real bot dispatch path -----------------------------------------------------


class _FakeTelegram:
    def __init__(self, updates: list[dict]) -> None:
        self._updates = updates
        self.sent: list[tuple[int, str]] = []

    def get_updates(self, offset, timeout):
        return self._updates

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def _message_update(text: str) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": int(NOW.timestamp()),
            "chat": {"id": ALLOWED_CHAT_ID},
            "text": text,
        },
    }


def _database(tmp_path) -> str:
    url = f"sqlite:///{(tmp_path / 'tidemark.db').as_posix()}"
    engine = create_store_engine(url)
    init_db(engine)
    engine.dispose()
    return url


def seed_section1(database_url: str, asset: str, state: str, watch: str, evaluated_at) -> None:
    """Writes a Section 1 result exactly as `tidemark run` does."""
    record = ContextRecord(
        asset=asset,
        evaluated_at=evaluated_at,
        rule_version="section-01-v1.1",
        state=state,
        watch=watch,
        grade="A" if watch != "WAIT" else None,
        reason_code="X",
        active_levels=[],
        fib={},
        swings_used=[],
    )
    engine = create_store_engine(database_url)
    TidemarkStore(engine).save_journal_entry(build_journal_entry(record, recorded_at=evaluated_at))
    engine.dispose()


def _run_coin(tmp_path, text: str, client: _FakeMarketClient, database_url: str | None) -> list:
    telegram = _FakeTelegram([_message_update(text)])
    state = BotStateStore(tmp_path / "offset.json")
    run_once(
        telegram,
        client,
        _cache(client),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        NOW,
        discard_backlog=False,
        database_url=database_url,
    )
    return telegram.sent


def _full_readings(**coin_overrides) -> dict:
    return {
        SOL: _reading(price_pct=0.6, **coin_overrides),
        BTC_SYMBOL: _reading(price_pct=0.1),
        ETH_SYMBOL: _reading(price_pct=0.05),
    }


def test_coin_reply_through_the_real_bot_path_renders_market_context(tmp_path) -> None:
    database_url = _database(tmp_path)
    seed_section1(database_url, SOL, "BULLISH", "LONG_WATCH", FRESH_EVALUATED_AT)
    seed_section1(database_url, BTC_SYMBOL, "BULLISH", "LONG_WATCH", FRESH_EVALUATED_AT)
    client = _FakeMarketClient(_full_readings())

    (_, text) = _run_coin(tmp_path, "/coin SOL", client, database_url)[0]

    assert "MARKET CONTEXT" in text
    assert "COIN (SOL/USDT:USDT)" in text
    assert "BTC (primary reference)" in text
    assert "ETH (secondary reference)" in text
    assert "Relative strength vs BTC: COIN STRONGER THAN BTC" in text
    assert "BTC alignment: ALIGNED" in text
    assert "Liquidation imbalance: LONG LIQUIDATIONS > SHORT" in text
    assert "WHAT TO WATCH" in text
    assert "Section 1 LONG_WATCH (grade A)" in text
    # Layer 1 and the existing position-flow layers are still there, untouched.
    assert "Open interest: " in text
    assert "POSITION FLOW" in text


def test_coin_reply_without_a_database_url_has_no_market_context(tmp_path) -> None:
    client = _FakeMarketClient(_full_readings())
    (_, text) = _run_coin(tmp_path, "/coin SOL", client, None)[0]
    assert "MARKET CONTEXT" not in text
    assert BTC_SYMBOL not in client.requested


def test_stale_section1_renders_unavailable_and_is_not_recomputed(tmp_path) -> None:
    database_url = _database(tmp_path)
    seed_section1(database_url, SOL, "BULLISH", "LONG_WATCH", STALE_EVALUATED_AT)
    seed_section1(database_url, BTC_SYMBOL, "BULLISH", "LONG_WATCH", STALE_EVALUATED_AT)
    client = _FakeMarketClient(_full_readings())

    (_, text) = _run_coin(tmp_path, "/coin SOL", client, database_url)[0]

    assert "Section 1 (4H): UNAVAILABLE (stored Section 1 record is stale" in text
    assert "BTC alignment: NO_DIRECTIONAL_ALIGNMENT" in text
    assert "No Section 1 WATCH" not in text  # UNAVAILABLE says so instead
    assert "Section 1 (4H) UNAVAILABLE" in text


def test_btc_fetch_failure_still_answers_the_coin(tmp_path) -> None:
    database_url = _database(tmp_path)
    client = _FakeMarketClient(_full_readings(), fail_for=(BTC_SYMBOL,))

    (_, text) = _run_coin(tmp_path, "/coin SOL", client, database_url)[0]

    assert "Coinalyze: UNAVAILABLE (Coinalyze rate limit reached this minute)" in text
    assert "Open interest: " in text  # the coin's own Layer 1 still answers


def test_budget_precheck_counts_both_references_when_context_is_on(tmp_path) -> None:
    # A non-BTC/ETH coin costs 3 snapshots = 21 call-units. 25 + 21 > 40.
    database_url = _database(tmp_path)
    client = _FakeMarketClient(_full_readings(), calls_in_last_minute=25)
    assert _run_coin(tmp_path, "/coin SOL", client, database_url)[0][1] == RATE_LIMITED_TEXT


def test_budget_precheck_without_context_is_the_original_single_lookup(tmp_path) -> None:
    client = _FakeMarketClient(_full_readings(), calls_in_last_minute=25)
    (_, text) = _run_coin(tmp_path, "/coin SOL", client, None)[0]
    assert text != RATE_LIMITED_TEXT  # 25 + 7 = 32 <= 40


def test_btc_lookup_costs_two_snapshots_not_three(tmp_path) -> None:
    # BTC as coin needs only ETH on top: 2 snapshots = 14. 27 + 14 = 41 > 40.
    database_url = _database(tmp_path)
    client = _FakeMarketClient(_full_readings(), calls_in_last_minute=27)
    assert _run_coin(tmp_path, "/coin BTC", client, database_url)[0][1] == RATE_LIMITED_TEXT


# -- output language (every rendered path) -----------------------------------

# Out-of-scope terms, trading-execution words, and derivatives labels the
# brief bars from every rendered output. Matched on word boundaries, so
# "stronger"/"weaker" (mandated labels) and "flow" never trip them. The
# buy/sell check excludes the existing Layer 1/3 labels that the brief keeps.
_BANNED = [
    r"\bentry\b",
    r"\bentries\b",
    r"\benter\b",
    r"\bstop\b",
    r"\bsl\b",
    r"\btp\b",
    r"\btarget\b",
    r"\bsetup\b",
    r"r:r",
    r"\bsweeps?\b",
    r"\breclaims?\b",
    r"\b15m\b",
    r"\b5m\b",
    r"confirmation",
    r"\bliquidity\b",
    r"\bclean touch\b",
    r"\belevated\b",
    r"\bcrowded\b",
    r"\bhigh\b",
    r"\blow\b",
    r"\babove\b",
    r"\bbelow\b",
    r"\bavoid\b",
    r"\bstrong\b",
    r"\bweak\b",
    r"\bscore\b",
    r"\bconfidence\b",
]
_EXISTING_BUY_SELL_LABELS = r"buy volume|sell volume|buy/sell ratio|buy/sell flow|buying|selling"


def assert_no_banned_language(text: str) -> None:
    lowered = text.lower()
    for pattern in _BANNED:
        assert not re.search(pattern, lowered), f"banned term {pattern!r} in:\n{text}"
    without_labels = re.sub(_EXISTING_BUY_SELL_LABELS, "", lowered)
    assert not re.search(r"\bbuy\b", without_labels), text
    assert not re.search(r"\bsell\b", without_labels), text


def test_full_coin_reply_with_market_context_has_no_banned_language(tmp_path) -> None:
    database_url = _database(tmp_path)
    seed_section1(database_url, SOL, "BULLISH", "LONG_WATCH", FRESH_EVALUATED_AT)
    seed_section1(database_url, BTC_SYMBOL, "BEARISH", "SHORT_WATCH", FRESH_EVALUATED_AT)
    client = _FakeMarketClient(_full_readings())

    (_, text) = _run_coin(tmp_path, "/coin SOL", client, database_url)[0]

    assert_no_banned_language(text)


def test_context_block_for_each_render_path_has_no_banned_language(tmp_path) -> None:
    # Unavailable and NO_MATCH paths too - not only the happy path.
    database_url = _database(tmp_path)
    client = _FakeMarketClient(_full_readings(), fail_for=(ETH_SYMBOL,))
    (_, text) = _run_coin(tmp_path, "/coin SOL", client, database_url)[0]
    assert_no_banned_language(text)


def test_render_snapshot_without_market_context_is_unchanged(tmp_path) -> None:
    """Existing callers pass no market context and must get exactly the
    output they always had - the new section is appended only when given.
    """
    client = _FakeMarketClient(_full_readings())
    snapshot = fetch_market_intel(client, _cache(client), SOL, VENUE, NOW)
    assert "MARKET CONTEXT" not in render_snapshot(snapshot)
