"""gather_coin_universe_context: pairs each metric's current value with
the cached universe median/p75 - `None` entirely when the cache has
never been refreshed, and `None` per-metric when this symbol has no
current value for that one metric, even though the cache itself has
data. OI is fetched independently as a percentage (not reused from
`MarketIntelSnapshot.open_interest_change`, which is an absolute USD
value) - see the module's own docstring for why.
"""

from __future__ import annotations

import datetime as dt

from tidemark.market_intel.coin_universe_context import gather_coin_universe_context
from tidemark.market_intel.future_markets import FutureMarketsCache
from tidemark.market_intel.models import NO_DATA
from tidemark.market_intel.service import fetch_market_intel
from tidemark.market_intel.universe_context_store import (
    METRIC_NAMES,
    MetricSummaryFields,
    UniverseContextRecord,
    init_universe_context_store,
    make_engine,
    record_universe_context,
)

VENUE = "binanceusdm"
SYMBOL = "SOL/USDT:USDT"
COINALYZE_SYMBOL = "SOLUSDT_PERP.A"
NOW = dt.datetime(2026, 9, 28, 8, 30, tzinfo=dt.UTC)
PERIOD_START_EPOCH = int(dt.datetime(2026, 9, 28, 7, 0, tzinfo=dt.UTC).timestamp())
CACHED_AT = dt.datetime(2026, 9, 28, 7, 0, tzinfo=dt.UTC)


def _market_row(**overrides) -> dict:
    base = {
        "symbol": COINALYZE_SYMBOL,
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


def _history(**fields) -> list[dict]:
    return [{"symbol": COINALYZE_SYMBOL, "history": [{"t": PERIOD_START_EPOCH, **fields}]}]


class _FakeClient:
    def __init__(self, future_markets_rows) -> None:
        self._rows = future_markets_rows
        self.open_interest_history_calls = 0

    def future_markets(self):
        return self._rows

    def open_interest(self, symbols, convert_to_usd=True):
        return [
            {
                "symbol": COINALYZE_SYMBOL,
                "value": 1_000_000.0,
                "update": int(NOW.timestamp() * 1000),
            }
        ]

    def funding_rate(self, symbols):
        return [
            {"symbol": COINALYZE_SYMBOL, "value": -0.0032, "update": int(NOW.timestamp() * 1000)}
        ]

    def predicted_funding_rate(self, symbols):
        return [
            {"symbol": COINALYZE_SYMBOL, "value": 0.0022, "update": int(NOW.timestamp() * 1000)}
        ]

    def open_interest_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        self.open_interest_history_calls += 1
        return _history(o=1000.0, h=1010.0, l=995.0, c=1010.0)  # +1.0%

    def long_short_ratio_history(self, symbol, interval, from_ts, to_ts):
        return _history(r=1.207, l=54.69, s=45.31)

    def liquidation_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        return _history(l=495_606.6, s=158_077.0)

    def ohlcv_history(self, symbol, interval, from_ts, to_ts):
        return _history(v=597_404.22, bv=250_123.4)  # sell = 347,280.82


def _snapshot(client):
    cache = FutureMarketsCache(client)
    return fetch_market_intel(client, cache, SYMBOL, VENUE, NOW)


def _summary(**overrides) -> MetricSummaryFields:
    defaults = dict(n=30, min=0.5, p25=1.2, median=1.742, p75=2.125, max=2.8, unavailable_count=0)
    defaults.update(overrides)
    return MetricSummaryFields(**defaults)


def _seed_cache(tmp_path, **metric_overrides):
    engine = make_engine(f"sqlite:///{(tmp_path / 'tidemark.db').as_posix()}")
    init_universe_context_store(engine)
    metrics = {name: _summary() for name in METRIC_NAMES}
    metrics.update(metric_overrides)
    record_universe_context(
        engine,
        UniverseContextRecord(
            computed_at=CACHED_AT,
            universe_snapshot_id="snap-1",
            period_start=dt.datetime(2026, 9, 28, 6, 0, tzinfo=dt.UTC),
            period_end=CACHED_AT,
            metrics=metrics,
        ),
    )
    return engine


STALE_AFTER = dt.timedelta(hours=12)


def test_no_cached_row_returns_none_entirely(tmp_path) -> None:
    engine = make_engine(f"sqlite:///{(tmp_path / 'tidemark.db').as_posix()}")
    init_universe_context_store(engine)
    client = _FakeClient([_market_row()])
    snapshot = _snapshot(client)

    result = gather_coin_universe_context(client, engine, snapshot, NOW, STALE_AFTER)

    assert result is None


def test_cached_row_pairs_every_metrics_current_value_with_median_and_p75(tmp_path) -> None:
    engine = _seed_cache(tmp_path)
    client = _FakeClient([_market_row()])
    snapshot = _snapshot(client)

    result = gather_coin_universe_context(client, engine, snapshot, NOW, STALE_AFTER)

    assert result is not None
    assert result.long_short_ratio.current == 1.207
    assert result.long_short_ratio.median == 1.742
    assert result.funding_rate.current == -0.0032
    assert result.oi_change_pct.current == 1.0  # (1010-1000)/1000*100
    assert result.buy_sell_ratio.current == 250_123.4 / (597_404.22 - 250_123.4)
    for metric in (
        result.long_short_ratio,
        result.funding_rate,
        result.oi_change_pct,
        result.buy_sell_ratio,
    ):
        assert metric.is_stale is False
        assert metric.computed_at == CACHED_AT


def test_oi_percentage_is_computed_independently_not_reused_from_the_snapshot(tmp_path) -> None:
    """The snapshot's own open_interest_change is an absolute USD value;
    the universe-context OI figure must be the percentage, fetched fresh.
    """
    engine = _seed_cache(tmp_path)
    client = _FakeClient([_market_row()])
    snapshot = _snapshot(client)
    assert snapshot.open_interest_change.unit == "USD"  # the pre-existing, unchanged metric

    result = gather_coin_universe_context(client, engine, snapshot, NOW, STALE_AFTER)

    assert result.oi_change_pct.current == 1.0
    assert client.open_interest_history_calls == 2  # once for USD, once for the percentage


def test_stale_row_is_flagged(tmp_path) -> None:
    old = NOW - dt.timedelta(hours=20)
    engine = make_engine(f"sqlite:///{(tmp_path / 'tidemark.db').as_posix()}")
    init_universe_context_store(engine)
    record_universe_context(
        engine,
        UniverseContextRecord(
            computed_at=old,
            universe_snapshot_id="snap-old",
            period_start=old - dt.timedelta(hours=1),
            period_end=old,
            metrics={name: _summary() for name in METRIC_NAMES},
        ),
    )
    client = _FakeClient([_market_row()])
    snapshot = _snapshot(client)

    result = gather_coin_universe_context(client, engine, snapshot, NOW, STALE_AFTER)

    assert result.long_short_ratio.is_stale is True
    assert result.long_short_ratio.age == NOW - old


def test_a_metric_missing_its_current_value_is_none_even_with_cache_data(tmp_path) -> None:
    engine = _seed_cache(tmp_path)
    client = _FakeClient([_market_row(has_long_short_ratio_data=False)])
    snapshot = _snapshot(client)

    result = gather_coin_universe_context(client, engine, snapshot, NOW, STALE_AFTER)

    assert snapshot.long_short_ratio.status == NO_DATA
    assert result.long_short_ratio is None  # no current value to pair with a real median/p75
    assert result.funding_rate is not None  # unaffected


def test_a_metric_with_no_summary_in_the_cache_is_none(tmp_path) -> None:
    engine = _seed_cache(
        tmp_path,
        funding_rate=MetricSummaryFields(
            n=0, min=None, p25=None, median=None, p75=None, max=None, unavailable_count=30
        ),
    )
    client = _FakeClient([_market_row()])
    snapshot = _snapshot(client)

    result = gather_coin_universe_context(client, engine, snapshot, NOW, STALE_AFTER)

    assert result.funding_rate is None  # cache had no data for this metric that hour
    assert result.long_short_ratio is not None  # unaffected


def test_market_not_found_symbol_skips_the_extra_oi_fetch(tmp_path) -> None:
    engine = _seed_cache(tmp_path)
    client = _FakeClient([])  # nothing listed
    snapshot = _snapshot(client)

    result = gather_coin_universe_context(client, engine, snapshot, NOW, STALE_AFTER)

    assert client.open_interest_history_calls == 0
    assert result.long_short_ratio is None
    assert result.oi_change_pct is None


def test_zero_sell_volume_makes_buy_sell_ratio_unavailable_not_a_division_error(tmp_path) -> None:
    engine = _seed_cache(tmp_path)

    class _ZeroSellClient(_FakeClient):
        def ohlcv_history(self, symbol, interval, from_ts, to_ts):
            return _history(v=100.0, bv=100.0)  # sell = 0

    client = _ZeroSellClient([_market_row()])
    snapshot = _snapshot(client)
    assert snapshot.sell_volume.value == 0.0

    result = gather_coin_universe_context(client, engine, snapshot, NOW, STALE_AFTER)

    assert result.buy_sell_ratio is None
