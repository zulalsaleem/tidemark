"""BTC/ETH reference context cached per closed 1H period. Lookups go through
the real bot dispatch path (`run_once`) with one shared in-memory cache, the
way `intel bot` holds it. No live network.
"""

from __future__ import annotations

import datetime as dt
from itertools import count

from test_coin_market_context import (
    _database,
    _FakeTelegram,
    _full_readings,
    _message_update,
)
from test_market_context import (
    ALLOWED_CHAT_ID,
    BTC_SYMBOL,
    ETH_SYMBOL,
    NOW,
    PERIOD_START,
    SOL,
    VENUE,
    _cache,
    _FakeMarketClient,
)

from tidemark.market_intel.bot import RATE_LIMITED_TEXT, estimated_call_cost, run_once
from tidemark.market_intel.bot_state import BotStateStore
from tidemark.market_intel.market_context import ReferenceSnapshotCache, fetch_market_context
from tidemark.market_intel.service import fetch_market_intel

# One `fetch_market_intel` makes seven metric calls, each logged in `requested`.
CALLS_PER_SNAPSHOT = 7
_offsets = count()


def _lookup(tmp_path, client, reference_cache, text: str, now, database_url: str) -> str:
    telegram = _FakeTelegram([_message_update(text)])
    state = BotStateStore(tmp_path / f"offset-{next(_offsets)}.json")
    run_once(
        telegram,
        client,
        _cache(client),
        state,
        ALLOWED_CHAT_ID,
        VENUE,
        now,
        discard_backlog=False,
        database_url=database_url,
        reference_cache=reference_cache,
    )
    return telegram.sent[0][1]


def _fetches(client, symbol: str) -> int:
    return client.requested.count(symbol) // CALLS_PER_SNAPSHOT


def test_two_lookups_in_the_same_period_fetch_btc_and_eth_once(tmp_path) -> None:
    database_url = _database(tmp_path)
    client = _FakeMarketClient(_full_readings())
    reference_cache = ReferenceSnapshotCache()

    _lookup(tmp_path, client, reference_cache, "/coin SOL", NOW, database_url)
    _lookup(tmp_path, client, reference_cache, "/coin SOL", NOW, database_url)

    assert _fetches(client, BTC_SYMBOL) == 1
    assert _fetches(client, ETH_SYMBOL) == 1
    assert _fetches(client, SOL) == 2  # the coin itself is still fetched each time


def test_a_lookup_in_a_new_period_refetches_both_references(tmp_path) -> None:
    database_url = _database(tmp_path)
    client = _FakeMarketClient(_full_readings())
    reference_cache = ReferenceSnapshotCache()

    _lookup(tmp_path, client, reference_cache, "/coin SOL", NOW, database_url)
    _lookup(
        tmp_path, client, reference_cache, "/coin SOL", NOW + dt.timedelta(hours=1), database_url
    )

    assert _fetches(client, BTC_SYMBOL) == 2
    assert _fetches(client, ETH_SYMBOL) == 2


def test_requesting_btc_itself_does_not_fetch_btc_twice(tmp_path) -> None:
    database_url = _database(tmp_path)
    client = _FakeMarketClient(_full_readings())
    reference_cache = ReferenceSnapshotCache()

    _lookup(tmp_path, client, reference_cache, "/coin BTC", NOW, database_url)
    assert _fetches(client, BTC_SYMBOL) == 1  # the coin's own snapshot, once

    # The coin's snapshot seeded the cache, so a later coin in the same period
    # reuses BTC rather than fetching it again.
    _lookup(tmp_path, client, reference_cache, "/coin SOL", NOW, database_url)
    assert _fetches(client, BTC_SYMBOL) == 1
    assert _fetches(client, ETH_SYMBOL) == 1


def test_cached_context_carries_the_same_period_as_the_coin(tmp_path) -> None:
    database_url = _database(tmp_path)
    client = _FakeMarketClient(_full_readings())
    reference_cache = ReferenceSnapshotCache()
    fetch_market_context(
        client,
        _cache(client),
        fetch_market_intel(client, _cache(client), SOL, VENUE, NOW),
        VENUE,
        NOW,
        database_url,
        reference_cache,
    )

    later = NOW + dt.timedelta(minutes=15)  # 08:45 - still inside the same closed 07:00-08:00 hour
    coin_snapshot = fetch_market_intel(client, _cache(client), SOL, VENUE, later)
    bundle = fetch_market_context(
        client, _cache(client), coin_snapshot, VENUE, later, database_url, reference_cache
    )

    assert _fetches(client, BTC_SYMBOL) == 1  # the BTC context came from the cache
    coin_period = coin_snapshot.price_change.period_start
    assert coin_period == PERIOD_START
    assert bundle.market.btc.price_change.period_start == coin_period
    assert bundle.market.btc.price_change.period_close == coin_snapshot.price_change.period_close
    assert bundle.market.eth.price_change.period_start == coin_period


def test_precheck_is_full_price_cold_and_one_snapshot_warm(tmp_path) -> None:
    database_url = _database(tmp_path)
    cold = ReferenceSnapshotCache()
    assert estimated_call_cost(SOL, True, NOW, cold) == 21
    assert estimated_call_cost(BTC_SYMBOL, True, NOW, cold) == 14
    assert estimated_call_cost(SOL, False, NOW) == 7  # no market context, no references

    client = _FakeMarketClient(_full_readings())
    _lookup(tmp_path, client, cold, "/coin SOL", NOW, database_url)

    assert estimated_call_cost(SOL, True, NOW, cold) == 7
    assert estimated_call_cost(BTC_SYMBOL, True, NOW, cold) == 7
    # A new period is cold again.
    assert estimated_call_cost(SOL, True, NOW + dt.timedelta(hours=1), cold) == 21


def test_warm_cache_lets_a_busy_minute_through_and_cold_cache_does_not(tmp_path) -> None:
    database_url = _database(tmp_path)
    reference_cache = ReferenceSnapshotCache()
    client = _FakeMarketClient(_full_readings())
    _lookup(tmp_path, client, reference_cache, "/coin SOL", NOW, database_url)

    client.calls_in_last_minute = 25  # 25 + 7 = 32 <= 40, but 25 + 21 = 46 > 40
    warm = _lookup(tmp_path, client, reference_cache, "/coin SOL", NOW, database_url)
    assert warm != RATE_LIMITED_TEXT

    cold = _lookup(tmp_path, client, ReferenceSnapshotCache(), "/coin SOL", NOW, database_url)
    assert cold == RATE_LIMITED_TEXT


def test_a_failed_reference_fetch_is_not_cached(tmp_path) -> None:
    database_url = _database(tmp_path)
    client = _FakeMarketClient(_full_readings(), fail_for=(BTC_SYMBOL,))
    reference_cache = ReferenceSnapshotCache()

    first = _lookup(tmp_path, client, reference_cache, "/coin SOL", NOW, database_url)
    assert "Coinalyze: UNAVAILABLE" in first

    client._fail_for.clear()  # the next lookup, same period, should try BTC again
    second = _lookup(tmp_path, client, reference_cache, "/coin SOL", NOW, database_url)
    assert "Coinalyze: UNAVAILABLE" not in second
    # The failed attempt logged one call before raising; the retry was a full fetch.
    assert client.requested.count(BTC_SYMBOL) == 1 + CALLS_PER_SNAPSHOT


def test_cached_reference_funding_is_as_of_and_the_coins_stays_live(tmp_path) -> None:
    database_url = _database(tmp_path)
    client = _FakeMarketClient(_full_readings())
    reference_cache = ReferenceSnapshotCache()
    _lookup(tmp_path, client, reference_cache, "/coin SOL", NOW, database_url)

    text = _lookup(
        tmp_path, client, reference_cache, "/coin SOL", NOW + dt.timedelta(minutes=15), database_url
    )

    coin_block = text.split("COIN (SOL/USDT:USDT)")[1].split("BTC (primary reference)")[0]
    btc_block = text.split("BTC (primary reference)")[1].split("ETH (secondary reference)")[0]
    assert "Funding rate: 0.001% (as of " in btc_block
    assert "LIVE" not in btc_block
    assert "Funding rate: 0.001% (LIVE, updated " in coin_block
