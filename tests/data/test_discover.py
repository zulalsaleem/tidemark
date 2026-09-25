"""Venue symbol discovery: filtering and market_registry sync (Phase 6,
Merge 2A, PART A). No network - a fake ccxt-like markets dict stands in
for `load_markets`.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tidemark.data.discover import run_discovery
from tidemark.data.exchange import ExchangeClient
from tidemark.data.store import TidemarkStore, create_store_engine, init_db

VENUE = "binanceusdm"


class FakeMarketsExchange:
    """Minimal ccxt-like stub: only `load_markets`, no network."""

    apiKey = ""
    secret = ""

    def __init__(self, markets: dict[str, dict]) -> None:
        self._markets = markets

    def load_markets(self):
        return self._markets


def _market(symbol: str, type_: str = "swap", quote: str = "USDT", active: bool = True) -> dict:
    return {"symbol": symbol, "type": type_, "quote": quote, "active": active}


@pytest.fixture
def store() -> TidemarkStore:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    return TidemarkStore(engine)


def test_discovery_includes_only_active_usdt_perpetuals(store: TidemarkStore) -> None:
    markets = {
        "BTC/USDT:USDT": _market("BTC/USDT:USDT"),
        "ETH/USDT:USDT": _market("ETH/USDT:USDT"),
        "BTC/USDT": _market("BTC/USDT", type_="spot"),
        "BTC/USDT:USDT-261225": _market("BTC/USDT:USDT-261225", type_="future"),
        "BTC/USDC:USDC": _market("BTC/USDC:USDC", quote="USDC"),
        "OMG/USDT:USDT": _market("OMG/USDT:USDT", active=False),
    }
    exchange = ExchangeClient(exchange=FakeMarketsExchange(markets))
    now = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)

    outcome = run_discovery(store, exchange, VENUE, now=now)

    assert outcome.status == "COMPLETED"
    assert outcome.discovered == 2
    symbols = {row.symbol for row in store.market_registry(VENUE)}
    assert symbols == {"BTC/USDT:USDT", "ETH/USDT:USDT"}


def test_discovered_rows_are_active_with_no_candle_coverage_yet(store: TidemarkStore) -> None:
    markets = {"BTC/USDT:USDT": _market("BTC/USDT:USDT")}
    exchange = ExchangeClient(exchange=FakeMarketsExchange(markets))
    now = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)

    run_discovery(store, exchange, VENUE, now=now)

    [row] = store.market_registry(VENUE)
    assert row.status == "ACTIVE"
    assert row.contract_type == "perpetual"
    assert row.quote_currency == "USDT"
    assert row.first_seen_in_venue_list_at == now
    assert row.last_seen_in_venue_list_at == now
    assert row.first_candle_seen_at is None
    assert row.last_candle_seen_at is None


def test_rerunning_discovery_is_idempotent_and_bumps_last_seen(store: TidemarkStore) -> None:
    markets = {"BTC/USDT:USDT": _market("BTC/USDT:USDT")}
    exchange = ExchangeClient(exchange=FakeMarketsExchange(markets))
    first_run = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    second_run = first_run + dt.timedelta(days=1)

    run_discovery(store, exchange, VENUE, now=first_run)
    outcome = run_discovery(store, exchange, VENUE, now=second_run)

    assert outcome.discovered == 1
    rows = store.market_registry(VENUE)
    assert len(rows) == 1
    assert rows[0].first_seen_in_venue_list_at == first_run
    assert rows[0].last_seen_in_venue_list_at == second_run


def test_symbol_disappearing_from_listing_becomes_absent_not_deleted(store: TidemarkStore) -> None:
    first_markets = {
        "BTC/USDT:USDT": _market("BTC/USDT:USDT"),
        "ETH/USDT:USDT": _market("ETH/USDT:USDT"),
    }
    second_markets = {"BTC/USDT:USDT": _market("BTC/USDT:USDT")}  # ETH delisted
    first_run = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    second_run = first_run + dt.timedelta(days=1)

    run_discovery(
        store, ExchangeClient(exchange=FakeMarketsExchange(first_markets)), VENUE, now=first_run
    )
    outcome = run_discovery(
        store, ExchangeClient(exchange=FakeMarketsExchange(second_markets)), VENUE, now=second_run
    )

    assert outcome.marked_absent == 1
    rows = {row.symbol: row for row in store.market_registry(VENUE)}
    assert set(rows) == {"BTC/USDT:USDT", "ETH/USDT:USDT"}  # never deleted
    assert rows["BTC/USDT:USDT"].status == "ACTIVE"
    assert rows["ETH/USDT:USDT"].status == "ABSENT_FROM_VENUE"
    # last_seen_in_venue_list_at stays at when it was last actually seen,
    # never bumped to a run that no longer saw it.
    assert rows["ETH/USDT:USDT"].last_seen_in_venue_list_at == first_run


def test_symbol_relisted_after_absence_returns_to_active(store: TidemarkStore) -> None:
    with_it = {
        "BTC/USDT:USDT": _market("BTC/USDT:USDT"),
        "ETH/USDT:USDT": _market("ETH/USDT:USDT"),
    }
    without_it = {"BTC/USDT:USDT": _market("BTC/USDT:USDT")}
    t1 = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)
    t2 = t1 + dt.timedelta(days=1)
    t3 = t1 + dt.timedelta(days=2)

    run_discovery(store, ExchangeClient(exchange=FakeMarketsExchange(with_it)), VENUE, now=t1)
    run_discovery(store, ExchangeClient(exchange=FakeMarketsExchange(without_it)), VENUE, now=t2)
    run_discovery(store, ExchangeClient(exchange=FakeMarketsExchange(with_it)), VENUE, now=t3)

    row = store.market_registry_row(VENUE, "ETH/USDT:USDT")
    assert row is not None
    assert row.status == "ACTIVE"
    assert row.last_seen_in_venue_list_at == t3


def test_discovery_records_a_completed_run(store: TidemarkStore) -> None:
    markets = {"BTC/USDT:USDT": _market("BTC/USDT:USDT")}
    exchange = ExchangeClient(exchange=FakeMarketsExchange(markets))
    now = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)

    run_discovery(store, exchange, VENUE, now=now)

    run = store.latest_run()
    assert run is not None
    assert run.command == "discover"
    assert run.status == "COMPLETED"


def test_discovery_with_no_matching_markets_completes_with_zero(store: TidemarkStore) -> None:
    markets = {"BTC/USDT": _market("BTC/USDT", type_="spot")}
    exchange = ExchangeClient(exchange=FakeMarketsExchange(markets))

    outcome = run_discovery(store, exchange, VENUE)

    assert outcome.status == "COMPLETED"
    assert outcome.discovered == 0
    assert store.market_registry(VENUE) == []
