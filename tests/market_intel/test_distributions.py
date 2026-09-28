"""measure_distributions: percentile/summary math against a known
fixture, unavailable-value handling (excluded from n, counted
separately, never a fabricated 0), deterministic rank ordering, and
graceful partial results on a rate-limit budget exhaustion mid-run.
"""

from __future__ import annotations

import datetime as dt

from tidemark.market_intel.distributions import measure_distributions
from tidemark.market_intel.errors import RateLimitedError
from tidemark.market_intel.future_markets import FutureMarketsCache
from tidemark.market_intel.models import MARKET_NOT_FOUND, NO_DATA

VENUE = "binanceusdm"
NOW = dt.datetime(2026, 9, 27, 19, 30, tzinfo=dt.UTC)
PERIOD_START = dt.datetime(2026, 9, 27, 18, 0, tzinfo=dt.UTC)
PERIOD_START_EPOCH = int(PERIOD_START.timestamp())


def _coinalyze_symbol(ccxt_symbol: str) -> str:
    base = ccxt_symbol.split("/")[0]
    return f"{base}USDT_PERP.A"


def _market_row(ccxt_symbol: str, **overrides) -> dict:
    base = {
        "symbol": _coinalyze_symbol(ccxt_symbol),
        "exchange": "A",
        "base_asset": ccxt_symbol.split("/")[0],
        "quote_asset": "USDT",
        "is_perpetual": True,
        "has_long_short_ratio_data": True,
        "has_ohlcv_data": True,
        "has_buy_sell_data": True,
        "oi_lq_vol_denominated_in": "BASE_ASSET",
    }
    base.update(overrides)
    return base


def _bucket(symbol: str, **fields) -> list[dict]:
    return [{"symbol": symbol, "history": [{"t": PERIOD_START_EPOCH, **fields}]}]


class _FakeClient:
    """Drives per-symbol long/short ratio, funding, OI %, and buy/sell
    ratio from a table keyed by ccxt symbol - `None` for a symbol means
    "Coinalyze returned nothing for this period" (NO_DATA).
    """

    def __init__(self, listed_symbols: list[str], ls_ratio=None, funding=None, oi=None, bsr=None):
        self._future_markets = [_market_row(s) for s in listed_symbols]
        self._ls_ratio = ls_ratio or {}
        self._funding = funding or {}
        self._oi = oi or {}
        self._bsr = bsr or {}
        self.calls: list[str] = []
        self.calls_in_last_minute = 0
        self._raise_on: dict[str, Exception] = {}

    def raise_on_call(self, coinalyze_symbol: str, exc: Exception) -> None:
        self._raise_on[coinalyze_symbol] = exc

    def _maybe_raise(self, symbol: str) -> None:
        if symbol in self._raise_on:
            raise self._raise_on[symbol]

    def future_markets(self):
        return self._future_markets

    def long_short_ratio_history(self, symbol, interval, from_ts, to_ts):
        self.calls.append(f"ls:{symbol}")
        self._maybe_raise(symbol)
        ratio = self._ls_ratio.get(symbol)
        if ratio is None:
            return []
        return _bucket(symbol, r=ratio, l=50.0, s=50.0)

    def funding_rate_history(self, symbol, interval, from_ts, to_ts):
        self.calls.append(f"funding:{symbol}")
        self._maybe_raise(symbol)
        value = self._funding.get(symbol)
        if value is None:
            return []
        return _bucket(symbol, o=value, h=value, l=value, c=value)

    def open_interest_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        self.calls.append(f"oi:{symbol}")
        self._maybe_raise(symbol)
        pct = self._oi.get(symbol)
        if pct is None:
            return []
        opening = 1000.0
        closing = opening * (1 + pct / 100)
        return _bucket(symbol, o=opening, h=closing, l=opening, c=closing)

    def ohlcv_history(self, symbol, interval, from_ts, to_ts):
        self.calls.append(f"ohlcv:{symbol}")
        self._maybe_raise(symbol)
        ratio = self._bsr.get(symbol)
        if ratio is None:
            return []
        # buy = ratio * sell, sell = 100 (fixed), so bv/sell == ratio.
        sell = 100.0
        buy = ratio * sell
        total = buy + sell
        return _bucket(symbol, v=total, bv=buy)


def _cache(client: _FakeClient) -> FutureMarketsCache:
    return FutureMarketsCache(client)


def _noop_sleep(_seconds: float) -> None:
    raise AssertionError("no pacing wait was expected in this test")


# -- percentile computation against a known fixture --------------------------


def test_percentiles_and_summary_match_a_known_fixture() -> None:
    symbols = [f"S{i}/USDT:USDT" for i in range(1, 6)]  # 5 symbols
    ls_ratio = {
        _coinalyze_symbol(s): v for s, v in zip(symbols, [1.0, 2.0, 3.0, 4.0, 5.0], strict=True)
    }
    client = _FakeClient(symbols, ls_ratio=ls_ratio)

    measurement = measure_distributions(
        client, _cache(client), symbols, VENUE, NOW, "EXPLICIT", None, sleep_fn=_noop_sleep
    )

    summary = next(s for s in measurement.summaries if s.name == "long_short_ratio")
    assert summary.n == 5
    assert summary.min == 1.0
    assert summary.p25 == 2.0
    assert summary.median == 3.0
    assert summary.p75 == 4.0
    assert summary.max == 5.0
    assert summary.unavailable_count == 0


# -- unavailable values: excluded from n, counted separately, never 0 --------


def test_unavailable_values_are_excluded_from_n_and_counted_separately() -> None:
    symbols = ["A/USDT:USDT", "B/USDT:USDT", "C/USDT:USDT"]
    # A and B have real (non-zero) funding readings; C has no data at all.
    funding = {_coinalyze_symbol("A/USDT:USDT"): 0.01, _coinalyze_symbol("B/USDT:USDT"): 0.02}
    client = _FakeClient(symbols, funding=funding)

    measurement = measure_distributions(
        client, _cache(client), symbols, VENUE, NOW, "EXPLICIT", None, sleep_fn=_noop_sleep
    )

    row_c = next(r for r in measurement.rows if r.ccxt_symbol == "C/USDT:USDT")
    assert row_c.funding_rate.status == NO_DATA
    assert row_c.funding_rate.value is None  # never a fabricated 0

    summary = next(s for s in measurement.summaries if s.name == "funding_rate")
    assert summary.n == 2  # only A and B
    assert summary.unavailable_count == 1  # C, counted separately
    assert 0.0 not in (summary.min, summary.max)  # C's absence never leaks in as a 0


def test_unlisted_symbol_is_market_not_found_not_zero() -> None:
    client = _FakeClient(listed_symbols=[])  # nothing listed at all
    measurement = measure_distributions(
        client,
        _cache(client),
        ["X/USDT:USDT"],
        VENUE,
        NOW,
        "EXPLICIT",
        None,
        sleep_fn=_noop_sleep,
    )

    row = measurement.rows[0]
    assert row.long_short_ratio.status == MARKET_NOT_FOUND
    assert row.funding_rate.status == MARKET_NOT_FOUND
    assert row.oi_change_pct.status == MARKET_NOT_FOUND
    assert row.buy_sell_ratio.status == MARKET_NOT_FOUND
    assert client.calls == []  # short-circuited, no history calls at all


# -- deterministic ordering ---------------------------------------------------


def test_rows_preserve_input_order_and_carry_the_given_rank() -> None:
    symbols = ["ZETA/USDT:USDT", "ALPHA/USDT:USDT", "MID/USDT:USDT"]
    client = _FakeClient(symbols)

    measurement = measure_distributions(
        client, _cache(client), symbols, VENUE, NOW, "EXPLICIT", None, sleep_fn=_noop_sleep
    )

    assert [r.ccxt_symbol for r in measurement.rows] == symbols  # never re-sorted by value
    assert [r.rank for r in measurement.rows] == [1, 2, 3]


# -- a 429 mid-run reports partial results rather than crashing --------------


def test_rate_limit_exhaustion_mid_run_reports_partial_results() -> None:
    symbols = ["A/USDT:USDT", "B/USDT:USDT", "C/USDT:USDT"]
    client = _FakeClient(symbols)
    client.raise_on_call(_coinalyze_symbol("B/USDT:USDT"), RateLimitedError(3, 5.0))

    measurement = measure_distributions(
        client, _cache(client), symbols, VENUE, NOW, "EXPLICIT", None, sleep_fn=_noop_sleep
    )

    assert [r.ccxt_symbol for r in measurement.rows] == ["A/USDT:USDT"]
    assert measurement.skipped_symbols == ["B/USDT:USDT", "C/USDT:USDT"]


# -- pacing --------------------------------------------------------------------


def test_pacing_waits_when_the_client_reports_the_budget_is_nearly_spent() -> None:
    symbols = ["A/USDT:USDT"]
    client = _FakeClient(symbols)
    client.calls_in_last_minute = 39  # one unit of headroom, but 4 are needed

    waits: list[float] = []

    def _sleep(seconds: float) -> None:
        waits.append(seconds)
        client.calls_in_last_minute = 0  # budget clears after waiting

    measure_distributions(
        client, _cache(client), symbols, VENUE, NOW, "EXPLICIT", None, sleep_fn=_sleep
    )

    assert len(waits) == 1


# -- source/snapshot_id are carried through unchanged -------------------------


def test_source_and_snapshot_id_are_recorded_on_the_result() -> None:
    symbols = ["A/USDT:USDT"]
    client = _FakeClient(symbols)

    measurement = measure_distributions(
        client, _cache(client), symbols, VENUE, NOW, "SNAPSHOT", "snap-42", sleep_fn=_noop_sleep
    )

    assert measurement.source == "SNAPSHOT"
    assert measurement.snapshot_id == "snap-42"
