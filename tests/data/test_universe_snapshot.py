"""Universe snapshot generation (Phase 6, Merge 2B, PARTS B/D/E). No
network for any test except the explicit FORWARD-backfill ones, which use
a fake ccxt-like stub (see tests/data/test_ingest.py's own pattern).
"""

from __future__ import annotations

import datetime as dt

import pytest

from tidemark.data.asset_class import (
    CLASSIFICATION_METHODOLOGY_VERSION,
    CLASSIFICATION_SOURCE,
    CRYPTO,
)
from tidemark.data.exchange import ExchangeClient, RawCandle
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.data.universe_eligibility import (
    INSUFFICIENT_VOLUME_HISTORY,
    NEVER_EXITED_INSUFFICIENT_STRUCTURE,
    NOT_ASSESSED,
)
from tidemark.data.universe_snapshot import (
    K,
    N,
    generate_universe_snapshot,
    rank_symbols,
)

VENUE = "binanceusdm"
BASE = dt.datetime(2026, 9, 25, tzinfo=dt.UTC)

# The same zigzag used elsewhere in this phase's tests: 19 4H candles,
# exiting INSUFFICIENT_STRUCTURE partway through (candle index 5, into
# BULLISH) and never breaking down again within this window.
_EXIT_VALUES = [
    120,
    115,
    110,
    105,
    100,
    110,
    120,
    130,
    120,
    115,
    112,
    110,
    120,
    130,
    140,
    150,
    140,
    130,
    120,
]


@pytest.fixture
def store() -> TidemarkStore:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    return TidemarkStore(engine)


def _daily_raw(open_time: dt.datetime, volume: float = 1000.0, close: float = 10.0) -> RawCandle:
    return RawCandle(
        open_time=open_time,
        close_time=open_time + dt.timedelta(days=1),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
    )


def _four_h_raw(open_time: dt.datetime, value: float) -> RawCandle:
    return RawCandle(
        open_time=open_time,
        close_time=open_time + dt.timedelta(hours=4),
        open=value,
        high=value,
        low=value,
        close=value,
        volume=1.0,
    )


def _seed_registry(store: TidemarkStore, symbol: str, seen_at: dt.datetime) -> None:
    """Seed a registry listing classified CRYPTO - the default for every
    test in this file that isn't itself about asset-class classification
    (see tests/data/test_asset_class.py and the UNIV-08 tests below for
    those)."""
    store.record_market_listing(VENUE, symbol, "perpetual", "USDT", seen_at)
    store.record_classification(
        VENUE,
        symbol,
        "COIN",
        CRYPTO,
        CLASSIFICATION_SOURCE,
        seen_at,
        CLASSIFICATION_METHODOLOGY_VERSION,
    )


def _seed_daily(
    store: TidemarkStore, symbol: str, n: int, until: dt.datetime, volume: float
) -> None:
    candles = [_daily_raw(until - dt.timedelta(days=n - i), volume=volume) for i in range(n)]
    store.upsert_candles(VENUE, symbol, "1d", candles, until)


def _seed_4h(store: TidemarkStore, symbol: str, values: list[float], start: dt.datetime) -> None:
    candles = [_four_h_raw(start + dt.timedelta(hours=4 * i), v) for i, v in enumerate(values)]
    store.upsert_candles(VENUE, symbol, "4h", candles, start + dt.timedelta(hours=4 * len(values)))


# -- rank_symbols: determinism and tie-breaking --------------------------------


def test_rank_symbols_orders_by_metric_descending() -> None:
    ranked = rank_symbols({"A": 10.0, "B": 30.0, "C": 20.0})
    assert [r.symbol for r in ranked] == ["B", "C", "A"]


def test_rank_symbols_breaks_ties_alphabetically() -> None:
    ranked = rank_symbols({"ZETA": 10.0, "ALPHA": 10.0, "BETA": 10.0})
    assert [r.symbol for r in ranked] == ["ALPHA", "BETA", "ZETA"]


def test_rank_symbols_puts_no_metric_symbols_after_every_valued_one() -> None:
    ranked = rank_symbols({"HAS_VALUE": 1.0, "NO_VALUE": None})
    assert [r.symbol for r in ranked] == ["HAS_VALUE", "NO_VALUE"]


def test_rank_symbols_breaks_no_metric_ties_alphabetically_too() -> None:
    ranked = rank_symbols({"ZETA": None, "ALPHA": None})
    assert [r.symbol for r in ranked] == ["ALPHA", "ZETA"]


def test_rank_symbols_is_deterministic_across_repeated_calls() -> None:
    metrics = {"A": 5.0, "B": 5.0, "C": None, "D": 12.0}
    first = rank_symbols(metrics)
    second = rank_symbols(metrics)
    assert [r.symbol for r in first] == [r.symbol for r in second]


# -- end-to-end snapshot generation (BACKFILLED - no network needed) ----------


def test_insufficient_volume_history_symbol_is_excluded(store: TidemarkStore) -> None:
    _seed_registry(store, "SHORT/USDT:USDT", BASE - dt.timedelta(days=100))
    _seed_daily(store, "SHORT/USDT:USDT", n=10, until=BASE, volume=1000.0)

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    [row] = result.rows
    assert row.symbol == "SHORT/USDT:USDT"
    assert row.metric_value is None
    assert row.eligible is False
    assert row.exclusion_reason == INSUFFICIENT_VOLUME_HISTORY
    assert row.selected is False


def test_every_ranked_symbol_appears_in_the_rows_not_only_selected(store: TidemarkStore) -> None:
    for i in range(3):
        symbol = f"SYM{i}/USDT:USDT"
        _seed_registry(store, symbol, BASE - dt.timedelta(days=100))
        _seed_daily(store, symbol, n=40, until=BASE, volume=float(i + 1))
    # None of these have any 4H data -> all excluded, none selected - but
    # every one of the 3 ranked ACTIVE symbols must still get a row.

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    assert len(result.rows) == 3
    assert {r.symbol for r in result.rows} == {"SYM0/USDT:USDT", "SYM1/USDT:USDT", "SYM2/USDT:USDT"}
    assert all(not r.selected for r in result.rows)


def test_symbols_ranked_below_k_are_not_assessed_with_eligible_null(store: TidemarkStore) -> None:
    # K+5 symbols, each with a valid metric but no 4H data - only the top
    # K by metric should ever be assessed; the rest are NOT_ASSESSED.
    total = K + 5
    for i in range(total):
        symbol = f"SYM{i:03d}/USDT:USDT"
        _seed_registry(store, symbol, BASE - dt.timedelta(days=100))
        # Higher i -> higher volume -> better rank (descending).
        _seed_daily(store, symbol, n=40, until=BASE, volume=float(i + 1))

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    by_rank = sorted(result.rows, key=lambda r: r.rank)
    assessed = by_rank[:K]
    not_assessed = by_rank[K:]

    assert len(not_assessed) == 5
    assert all(r.eligible is None for r in not_assessed)
    assert all(r.exclusion_reason == NOT_ASSESSED for r in not_assessed)
    assert all(
        r.eligible is not None or r.exclusion_reason == INSUFFICIENT_VOLUME_HISTORY
        for r in assessed
    )


def test_not_assessed_is_never_reported_as_eligible_false() -> None:
    """PART B: NOT_ASSESSED must be eligible=NULL, distinct from an
    assessed-and-failed eligible=False - a NULL is never accidentally
    coerced to a falsy `False` by the write path."""
    total = K + 1
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    store = TidemarkStore(engine)
    for i in range(total):
        symbol = f"SYM{i:03d}/USDT:USDT"
        _seed_registry(store, symbol, BASE - dt.timedelta(days=100))
        _seed_daily(store, symbol, n=40, until=BASE, volume=float(i + 1))

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    last_ranked = max(result.rows, key=lambda r: r.rank)
    assert last_ranked.eligible is None
    assert (last_ranked.eligible is False) is False  # explicitly not False


def test_never_exited_insufficient_structure_symbol_excluded_with_right_reason(
    store: TidemarkStore,
) -> None:
    symbol = "FLAT/USDT:USDT"
    _seed_registry(store, symbol, BASE - dt.timedelta(days=100))
    _seed_daily(store, symbol, n=40, until=BASE, volume=1000.0)
    _seed_4h(store, symbol, [100.0] * 60, BASE - dt.timedelta(hours=4 * 60))

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    [row] = result.rows
    assert row.eligible is False
    assert row.exclusion_reason == NEVER_EXITED_INSUFFICIENT_STRUCTURE


def test_eligible_symbol_within_n_gets_selected(store: TidemarkStore) -> None:
    symbol = "GOOD/USDT:USDT"
    _seed_registry(store, symbol, BASE - dt.timedelta(days=100))
    _seed_daily(store, symbol, n=40, until=BASE, volume=1000.0)
    start = BASE - dt.timedelta(hours=4 * len(_EXIT_VALUES))
    _seed_4h(store, symbol, _EXIT_VALUES, start)

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    [row] = result.rows
    assert row.eligible is True
    assert row.selected is True
    assert row.exclusion_reason is None
    assert result.snapshot.n_selected == 1


def test_only_top_n_eligible_get_selected_even_if_more_are_eligible(store: TidemarkStore) -> None:
    start = BASE - dt.timedelta(hours=4 * len(_EXIT_VALUES))
    for i in range(N + 3):
        symbol = f"ELIG{i:02d}/USDT:USDT"
        _seed_registry(store, symbol, BASE - dt.timedelta(days=100))
        # Higher i -> higher volume -> better rank.
        _seed_daily(store, symbol, n=40, until=BASE, volume=float(1000 + i))
        _seed_4h(store, symbol, _EXIT_VALUES, start)

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    assert result.snapshot.n_selected == N
    selected = [r for r in result.rows if r.selected]
    assert len(selected) == N
    assert all(r.rank <= N for r in selected)  # the N best-ranked eligible ones


def test_snapshot_header_records_k_and_metric_fields(store: TidemarkStore) -> None:
    _seed_registry(store, "A/USDT:USDT", BASE - dt.timedelta(days=100))
    _seed_daily(store, "A/USDT:USDT", n=40, until=BASE, volume=1000.0)

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    assert result.snapshot.k == K
    assert result.snapshot.metric_name == "MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D"
    assert result.snapshot.metric_window_days == 30
    assert result.snapshot.venue == VENUE


def test_backfilled_snapshot_never_writes_registry_eligibility_cache(store: TidemarkStore) -> None:
    symbol = "GOOD/USDT:USDT"
    _seed_registry(store, symbol, BASE - dt.timedelta(days=100))
    _seed_daily(store, symbol, n=40, until=BASE, volume=1000.0)
    start = BASE - dt.timedelta(hours=4 * len(_EXIT_VALUES))
    _seed_4h(store, symbol, _EXIT_VALUES, start)

    generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    row = store.market_registry_row(VENUE, symbol)
    assert row is not None
    assert row.section1_first_usable_at is None
    assert row.section1_eligibility_checked_at is None


def test_backfilled_snapshot_requires_no_exchange() -> None:
    """A BACKFILLED (as-of-the-past) snapshot never touches the network -
    passing exchange=None must not raise."""
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    store = TidemarkStore(engine)
    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)
    assert result.rows == []


def test_forward_snapshot_without_an_exchange_raises() -> None:
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    store = TidemarkStore(engine)
    with pytest.raises(ValueError, match="exchange is required"):
        generate_universe_snapshot(store, None, VENUE, as_of=None, now=BASE)


def test_duplicate_snapshot_at_same_as_of_is_rejected(store: TidemarkStore) -> None:
    _seed_registry(store, "A/USDT:USDT", BASE - dt.timedelta(days=100))
    _seed_daily(store, "A/USDT:USDT", n=40, until=BASE, volume=1000.0)

    generate_universe_snapshot(store, None, VENUE, as_of=BASE)
    with pytest.raises(ValueError, match="already exists"):
        generate_universe_snapshot(store, None, VENUE, as_of=BASE)


# -- FORWARD snapshot: backfill-if-missing and registry caching (fake exchange) -


class _FakeExchange:
    apiKey = ""
    secret = ""

    def __init__(self, rows_by_symbol: dict[str, list] | None = None) -> None:
        self._rows_by_symbol = rows_by_symbol or {}

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        since = since or 0
        rows = [r for r in self._rows_by_symbol.get(symbol, []) if r[0] >= since]
        return rows[:limit] if limit is not None else rows


def _four_h_row(open_time: dt.datetime, value: float) -> list:
    return [int(open_time.timestamp() * 1000), value, value, value, value, 1.0]


def test_forward_snapshot_backfills_4h_for_the_assessment_set_only_if_missing(
    store: TidemarkStore,
) -> None:
    now = BASE
    symbol = "NEW/USDT:USDT"
    _seed_registry(store, symbol, now - dt.timedelta(days=100))
    _seed_daily(store, symbol, n=40, until=now, volume=1000.0)
    assert store.count_candles(VENUE, symbol, "4h") == 0

    start = now - dt.timedelta(hours=4 * len(_EXIT_VALUES))
    rows = [_four_h_row(start + dt.timedelta(hours=4 * i), v) for i, v in enumerate(_EXIT_VALUES)]
    fake = _FakeExchange(rows_by_symbol={symbol: rows})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: now)

    result = generate_universe_snapshot(store, exchange, VENUE, as_of=None, now=now)

    assert store.count_candles(VENUE, symbol, "4h") > 0
    [row] = result.rows
    assert row.eligible is True


def test_forward_snapshot_does_not_refetch_a_symbol_with_existing_4h_data(
    store: TidemarkStore,
) -> None:
    now = BASE
    symbol = "EXISTING/USDT:USDT"
    _seed_registry(store, symbol, now - dt.timedelta(days=100))
    _seed_daily(store, symbol, n=40, until=now, volume=1000.0)
    start = now - dt.timedelta(hours=4 * len(_EXIT_VALUES))
    _seed_4h(store, symbol, _EXIT_VALUES, start)
    before = store.count_candles(VENUE, symbol, "4h")

    # A fake exchange with no rows at all - if the code tried to backfill
    # despite already having data, it would simply add nothing (harmless),
    # so instead assert the count is unchanged AND that eligibility still
    # resolved correctly from the pre-existing data alone.
    fake = _FakeExchange(rows_by_symbol={})
    exchange = ExchangeClient(exchange=fake, now_fn=lambda: now)

    result = generate_universe_snapshot(store, exchange, VENUE, as_of=None, now=now)

    assert store.count_candles(VENUE, symbol, "4h") == before
    [row] = result.rows
    assert row.eligible is True


def test_forward_snapshot_caches_section1_first_usable_at_on_the_registry(
    store: TidemarkStore,
) -> None:
    now = BASE
    symbol = "GOOD/USDT:USDT"
    _seed_registry(store, symbol, now - dt.timedelta(days=100))
    _seed_daily(store, symbol, n=40, until=now, volume=1000.0)
    start = now - dt.timedelta(hours=4 * len(_EXIT_VALUES))
    _seed_4h(store, symbol, _EXIT_VALUES, start)
    exchange = ExchangeClient(exchange=_FakeExchange(), now_fn=lambda: now)

    generate_universe_snapshot(store, exchange, VENUE, as_of=None, now=now)

    row = store.market_registry_row(VENUE, symbol)
    assert row is not None
    assert row.section1_first_usable_at is not None
    assert row.section1_eligibility_checked_at == now


def test_forward_snapshot_reuses_cached_eligibility_without_recomputing(
    store: TidemarkStore,
) -> None:
    """PART C: 'persist it so a later snapshot does not recompute the
    whole history' - once cached, a later FORWARD run must not need the
    4H candles at all to know the symbol is eligible.
    """
    now = BASE
    symbol = "CACHED/USDT:USDT"
    _seed_registry(store, symbol, now - dt.timedelta(days=100))
    _seed_daily(store, symbol, n=40, until=now, volume=1000.0)
    cached_at = now - dt.timedelta(days=10)
    store.record_section1_eligibility(VENUE, symbol, cached_at, cached_at)
    # No 4H candles stored at all, and the fake exchange returns none
    # either - if the code tried to recompute from scratch, it would find
    # INSUFFICIENT_4H_HISTORY instead of reusing the cache.
    exchange = ExchangeClient(exchange=_FakeExchange(), now_fn=lambda: now)

    result = generate_universe_snapshot(store, exchange, VENUE, as_of=None, now=now)

    [row] = result.rows
    assert row.eligible is True
    assert row.exclusion_reason is None


# -- UNIVERSE_AS_OF_INVARIANT (mandatory gate, PART E) -------------------------

_INVARIANT_4H_START = BASE - dt.timedelta(hours=4 * len(_EXIT_VALUES))


def _canonical_daily_candles() -> list[RawCandle]:
    """One fixed, canonical 40-day daily candle set, anchored to BASE -
    the single source of truth both the 'full' and 'truncated' databases
    below must derive from, so a difference in which candles happen to
    exist is never what the comparison is testing."""
    return [_daily_raw(BASE - dt.timedelta(days=40 - i), volume=1000.0) for i in range(40)]


def _canonical_4h_candles() -> list[RawCandle]:
    return [
        _four_h_raw(_INVARIANT_4H_START + dt.timedelta(hours=4 * i), v)
        for i, v in enumerate(_EXIT_VALUES)
    ]


def _seed_full_history(store: TidemarkStore, symbol: str, seen_at: dt.datetime) -> None:
    """The entire canonical candle set, unfiltered - the 'full database'
    side of the invariant comparison."""
    _seed_registry(store, symbol, seen_at)
    store.upsert_candles(VENUE, symbol, "1d", _canonical_daily_candles(), BASE)
    store.upsert_candles(VENUE, symbol, "4h", _canonical_4h_candles(), BASE)


def _seed_truncated_history(
    store: TidemarkStore, symbol: str, seen_at: dt.datetime, as_of: dt.datetime
) -> None:
    """The same canonical candle set, but only the rows that had already
    closed by `as_of` were ever stored - the 'truncated database' side."""
    _seed_registry(store, symbol, seen_at)
    daily = [c for c in _canonical_daily_candles() if c.close_time <= as_of]
    store.upsert_candles(VENUE, symbol, "1d", daily, as_of)
    four_h = [c for c in _canonical_4h_candles() if c.close_time <= as_of]
    store.upsert_candles(VENUE, symbol, "4h", four_h, as_of)


@pytest.mark.parametrize("candle_index", [4, 5, 6, 10, 18])
def test_as_of_invariant_across_several_timestamps(candle_index: int) -> None:
    """generate(T, database truncated at T) must equal generate_as_of(T,
    full database), for every field knowable at T - the metric, the
    ranking, eligibility, and section1_first_usable_at. Same truncation
    approach as tests/context/test_look_ahead_guard.py: both databases
    derive from the exact same canonical candle set (see
    `_canonical_daily_candles`/`_canonical_4h_candles`), so any
    difference in the two snapshots can only come from truncation itself,
    never from the fixtures happening to diverge.
    """
    seen_at = BASE - dt.timedelta(days=200)
    as_of = (_INVARIANT_4H_START + dt.timedelta(hours=4 * candle_index)) + dt.timedelta(hours=4)

    full_engine = create_store_engine("sqlite:///:memory:")
    init_db(full_engine)
    full_store = TidemarkStore(full_engine)
    _seed_full_history(full_store, "X/USDT:USDT", seen_at)
    _seed_full_history(full_store, "Y/USDT:USDT", seen_at)

    trunc_engine = create_store_engine("sqlite:///:memory:")
    init_db(trunc_engine)
    trunc_store = TidemarkStore(trunc_engine)
    _seed_truncated_history(trunc_store, "X/USDT:USDT", seen_at, as_of)
    _seed_truncated_history(trunc_store, "Y/USDT:USDT", seen_at, as_of)

    full_result = generate_universe_snapshot(full_store, None, VENUE, as_of=as_of)
    trunc_result = generate_universe_snapshot(trunc_store, None, VENUE, as_of=as_of)

    def _fields(rows) -> list[dict]:
        return sorted(
            (
                {
                    "symbol": r.symbol,
                    "rank": r.rank,
                    "metric_value": r.metric_value,
                    "eligible": r.eligible,
                    "selected": r.selected,
                    "exclusion_reason": r.exclusion_reason,
                    # UNIV-08: classification is part of the as-of invariant too.
                    "underlying_type": r.underlying_type,
                    "asset_class": r.asset_class,
                }
                for r in rows
            ),
            key=lambda d: d["symbol"],
        )

    assert _fields(full_result.rows) == _fields(trunc_result.rows)
    assert full_result.snapshot.candle_hash == trunc_result.snapshot.candle_hash
    assert full_result.snapshot.n_selected == trunc_result.snapshot.n_selected
    assert (
        full_result.snapshot.counts_by_exclusion_reason
        == trunc_result.snapshot.counts_by_exclusion_reason
    )


def test_as_of_invariant_registry_membership_excludes_symbols_listed_after_t() -> None:
    """'No future ... listing status ... may influence a snapshot at T':
    a symbol first seen in the venue's listing AFTER T must not appear in
    a BACKFILLED snapshot at T, even though it's ACTIVE in the registry
    right now.
    """
    engine = create_store_engine("sqlite:///:memory:")
    init_db(engine)
    store = TidemarkStore(engine)
    t = BASE - dt.timedelta(days=50)

    _seed_registry(store, "OLD/USDT:USDT", BASE - dt.timedelta(days=200))  # listed before T
    _seed_daily(store, "OLD/USDT:USDT", n=40, until=t, volume=1000.0)

    _seed_registry(store, "NEW/USDT:USDT", BASE - dt.timedelta(days=10))  # listed AFTER T
    _seed_daily(store, "NEW/USDT:USDT", n=40, until=BASE, volume=1000.0)

    result = generate_universe_snapshot(store, None, VENUE, as_of=t)

    symbols = {r.symbol for r in result.rows}
    assert symbols == {"OLD/USDT:USDT"}


# -- UNIV-08: asset-class domain constraint ------------------------------------


def _seed_classified(
    store: TidemarkStore, symbol: str, seen_at: dt.datetime, underlying_type: str, asset_class: str
) -> None:
    store.record_market_listing(VENUE, symbol, "perpetual", "USDT", seen_at)
    store.record_classification(
        VENUE,
        symbol,
        underlying_type,
        asset_class,
        CLASSIFICATION_SOURCE,
        seen_at,
        CLASSIFICATION_METHODOLOGY_VERSION,
    )


def test_non_crypto_symbol_excluded_regardless_of_volume_or_rank(store: TidemarkStore) -> None:
    """A non-crypto symbol excluded on domain grounds even though it has
    plenty of volume history and would otherwise have ranked at #1."""
    from tidemark.data.asset_class import NON_CRYPTO, NON_CRYPTO_UNDERLYING

    _seed_classified(store, "MSTR/USDT:USDT", BASE - dt.timedelta(days=100), "EQUITY", NON_CRYPTO)
    _seed_daily(store, "MSTR/USDT:USDT", n=40, until=BASE, volume=999_999.0)  # highest volume

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    [row] = result.rows
    assert row.rank == 1  # still ranked by volume - ranking methodology unchanged
    assert row.metric_value is not None  # metric still computed and shown
    assert row.eligible is False
    assert row.selected is False
    assert row.exclusion_reason == NON_CRYPTO_UNDERLYING
    assert row.asset_class == NON_CRYPTO


def test_index_symbol_excluded_as_non_eligible_index(store: TidemarkStore) -> None:
    from tidemark.data.asset_class import NON_ELIGIBLE_INDEX

    _seed_classified(
        store, "BTCDOM/USDT:USDT", BASE - dt.timedelta(days=100), "INDEX", NON_ELIGIBLE_INDEX
    )
    _seed_daily(store, "BTCDOM/USDT:USDT", n=40, until=BASE, volume=1000.0)

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    [row] = result.rows
    assert row.exclusion_reason == NON_ELIGIBLE_INDEX
    assert row.asset_class == NON_ELIGIBLE_INDEX


def test_unclassified_symbol_excluded_as_unknown(store: TidemarkStore) -> None:
    """A registry row with no classification at all (pre-UNIV-08, never
    re-discovered) must be excluded as UNKNOWN, never guessed CRYPTO."""
    from tidemark.data.asset_class import UNKNOWN, UNKNOWN_UNDERLYING_TYPE

    store.record_market_listing(
        VENUE, "OLD/USDT:USDT", "perpetual", "USDT", BASE - dt.timedelta(days=100)
    )
    _seed_daily(store, "OLD/USDT:USDT", n=40, until=BASE, volume=1000.0)

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    [row] = result.rows
    assert row.exclusion_reason == UNKNOWN_UNDERLYING_TYPE
    assert row.asset_class == UNKNOWN


def test_crypto_symbol_is_unaffected_by_the_domain_check(store: TidemarkStore) -> None:
    symbol = "GOOD/USDT:USDT"
    _seed_registry(store, symbol, BASE - dt.timedelta(days=100))
    _seed_daily(store, symbol, n=40, until=BASE, volume=1000.0)
    start = BASE - dt.timedelta(hours=4 * len(_EXIT_VALUES))
    _seed_4h(store, symbol, _EXIT_VALUES, start)

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    [row] = result.rows
    assert row.asset_class == CRYPTO
    assert row.eligible is True
    assert row.selected is True


def test_fresh_snapshot_contains_only_coin_instruments(store: TidemarkStore) -> None:
    from tidemark.data.asset_class import NON_CRYPTO

    start = BASE - dt.timedelta(hours=4 * len(_EXIT_VALUES))
    for i in range(3):
        symbol = f"CRYPTO{i}/USDT:USDT"
        _seed_registry(store, symbol, BASE - dt.timedelta(days=100))
        _seed_daily(store, symbol, n=40, until=BASE, volume=float(1000 + i))
        _seed_4h(store, symbol, _EXIT_VALUES, start)
    for i in range(3):
        symbol = f"EQUITY{i}/USDT:USDT"
        _seed_classified(store, symbol, BASE - dt.timedelta(days=100), "EQUITY", NON_CRYPTO)
        _seed_daily(store, symbol, n=40, until=BASE, volume=float(2000 + i))

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    selected_symbols = {r.symbol for r in result.rows if r.selected}
    assert selected_symbols == {"CRYPTO0/USDT:USDT", "CRYPTO1/USDT:USDT", "CRYPTO2/USDT:USDT"}
    assert all(r.asset_class == CRYPTO for r in result.rows if r.selected)


def test_snapshot_reads_persisted_classification_not_live_metadata(store: TidemarkStore) -> None:
    """Snapshot generation never calls load_markets() itself - it can
    only see what discovery already persisted. Proven here structurally:
    generate_universe_snapshot is given no exchange for a BACKFILLED run
    (exchange=None) and still classifies correctly, which is only
    possible if it read the registry, not a live call."""
    _seed_classified(store, "MSTR/USDT:USDT", BASE - dt.timedelta(days=100), "EQUITY", "NON_CRYPTO")
    _seed_daily(store, "MSTR/USDT:USDT", n=40, until=BASE, volume=1000.0)

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)  # exchange=None

    [row] = result.rows
    assert row.asset_class == "NON_CRYPTO"


def test_a_reclassification_after_t_does_not_alter_a_backfilled_snapshot_at_t(
    store: TidemarkStore,
) -> None:
    """UNIVERSE_AS_OF_INVARIANT applied to asset class: a classification
    change discovery makes AFTER T must not retroactively alter what a
    BACKFILLED snapshot at T reports - proven here because
    record_classification's set-once semantics mean the registry can
    only ever hold the FIRST classification captured, so this is really
    asserting the snapshot uses that persisted value, not a live one.
    """
    symbol = "BTC/USDT:USDT"
    seen_at = BASE - dt.timedelta(days=100)
    _seed_classified(store, symbol, seen_at, "COIN", CRYPTO)
    _seed_daily(store, symbol, n=40, until=BASE, volume=1000.0)
    start = BASE - dt.timedelta(hours=4 * len(_EXIT_VALUES))
    _seed_4h(store, symbol, _EXIT_VALUES, start)

    t = BASE - dt.timedelta(days=1)
    result = generate_universe_snapshot(store, None, VENUE, as_of=t)

    [row] = result.rows
    assert row.asset_class == CRYPTO


def test_pre_univ08_snapshot_is_unchanged_by_generating_a_fresh_one(store: TidemarkStore) -> None:
    """Snapshots are append-only. A snapshot generated before UNIV-08
    existed (methodology_version='universe-v1', no classification fields
    on its rows - exactly what the real, already-shipped snapshot looks
    like) must never be rewritten, mutated, or deleted by a later,
    UNIV-08-aware snapshot generation.
    """
    from tidemark.data.models import UniverseSnapshot, UniverseSnapshotRow

    old_snapshot_at = BASE - dt.timedelta(days=1)
    old_header = UniverseSnapshot(
        snapshot_id="pre-univ08",
        snapshot_at=old_snapshot_at,
        methodology_version="universe-v1",
        venue=VENUE,
        metric_name="MEDIAN_DAILY_DERIVED_QUOTE_VOLUME_30D",
        metric_window_days=30,
        k=K,
        n_selected=1,
        provenance="FORWARD",
        candle_hash="deadbeef",
        counts_by_exclusion_reason={},
    )
    old_row = UniverseSnapshotRow(
        snapshot_id="pre-univ08",
        symbol="MSTR/USDT:USDT",
        rank=1,
        metric_value=1_000_000.0,
        eligible=True,
        selected=True,
        exclusion_reason=None,
        # no classification fields - exactly the pre-UNIV-08 shape
    )
    store.save_universe_snapshot(old_header, [old_row])

    # Generate a fresh, UNIV-08-aware snapshot at a different timestamp.
    symbol = "GOOD/USDT:USDT"
    _seed_registry(store, symbol, BASE - dt.timedelta(days=100))
    _seed_daily(store, symbol, n=40, until=BASE, volume=1000.0)
    start = BASE - dt.timedelta(hours=4 * len(_EXIT_VALUES))
    _seed_4h(store, symbol, _EXIT_VALUES, start)
    generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    # The old snapshot is byte-for-byte exactly as it was written.
    old_after = store.universe_snapshot_by_id("pre-univ08")
    assert old_after is not None
    assert old_after.methodology_version == "universe-v1"
    assert old_after.snapshot_at == old_snapshot_at
    assert old_after.n_selected == 1

    [old_row_after] = store.universe_snapshot_rows("pre-univ08")
    assert old_row_after.symbol == "MSTR/USDT:USDT"
    assert old_row_after.selected is True
    assert old_row_after.asset_class is None  # never backfilled onto an old row
    assert old_row_after.underlying_type is None

    # Two distinct snapshots now coexist, both untouched by each other.
    assert len(store.universe_snapshots(VENUE)) == 2


def test_non_crypto_symbols_never_consume_an_assessment_slot_from_crypto_candidates(
    store: TidemarkStore,
) -> None:
    """UNIV-08: the assessment set is the top K *crypto* candidates by
    rank, not top K of the raw mixed ranking - ADR 0009's own reason for
    K's existence ("headroom over N so ineligible symbols can be skipped
    without running out of candidates") is defeated if a symbol that can
    never be eligible on domain grounds occupies one of the K slots. Here,
    K non-crypto symbols outrank every crypto one by volume; without the
    fix, every crypto candidate would be pushed past the assessment
    cutoff and get NOT_ASSESSED instead of a real eligibility check.
    """
    from tidemark.data.asset_class import NON_CRYPTO

    start = BASE - dt.timedelta(hours=4 * len(_EXIT_VALUES))

    # K non-crypto symbols, all out-ranking every crypto symbol by volume.
    for i in range(K):
        symbol = f"EQUITY{i:03d}/USDT:USDT"
        _seed_classified(store, symbol, BASE - dt.timedelta(days=100), "EQUITY", NON_CRYPTO)
        _seed_daily(store, symbol, n=40, until=BASE, volume=float(100_000 + i))

    # One crypto candidate, ranked below all K non-crypto symbols by volume,
    # but still perfectly eligible.
    symbol = "GOOD/USDT:USDT"
    _seed_registry(store, symbol, BASE - dt.timedelta(days=100))
    _seed_daily(store, symbol, n=40, until=BASE, volume=1.0)  # lowest volume
    _seed_4h(store, symbol, _EXIT_VALUES, start)

    result = generate_universe_snapshot(store, None, VENUE, as_of=BASE)

    row = next(r for r in result.rows if r.symbol == symbol)
    assert row.rank == K + 1  # true overall rank: last, behind all K non-crypto
    assert row.eligible is True  # still actually assessed and found eligible
    assert row.selected is True
    assert row.exclusion_reason is None
