"""`tidemark intel distributions`: measures, across the universe, what
long/short ratio, funding rate, 1H open-interest change, and buy/sell
volume ratio actually look like - before any coin-context rulebook
threshold is chosen. Read-only, no database writes, no interpretation:
every value is either a number or `ClosedPeriodMetric.unavailable(...)`,
never a fabricated zero and never a label like "elevated" or "crowded".

Reuses the same closed-period clamping (`clamping.closed_period`) as
`/coin`/`intel market`, and the same Merge 1 client and future-markets
cache - this module adds no new Coinalyze surface, only a fourth
derived metric (buy/sell volume ratio, from the same OHLCV bucket
`service.py`'s buy/sell volume split already reads) and the
percentile/summary math.

Funding rate here is the CLOSED 1H reading (`funding_rate_history`),
not the live point-in-time value `/coin`'s funding metric shows -
`briefing_data.py` made the same choice for the same reason: CLAUDE.md's
"closed candles only" rule applies to a measurement tool exactly as much
as to a rule evaluation.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass

from tidemark.market_intel.clamping import ClosedPeriod, closed_period
from tidemark.market_intel.client import RATE_LIMIT_PER_MINUTE, CoinalyzeClient
from tidemark.market_intel.errors import RateLimitedError
from tidemark.market_intel.future_markets import FutureMarketsCache
from tidemark.market_intel.models import MARKET_NOT_FOUND, NO_DATA, OK, ClosedPeriodMetric
from tidemark.market_intel.symbols import to_coinalyze_symbol

INTERVAL = "1hour"

# A conservative reservation for the pacing check below - the true cost
# per symbol is 2-4 calls depending on which of long/short-ratio and
# buy/sell data this market has (see FutureMarketInfo's has_* flags),
# but reserving the worst case keeps the pre-call check simple and never
# under-reserves.
CALL_BUDGET_PER_SYMBOL = 4
PACING_CHECK_INTERVAL_SECONDS = 2.0

_NO_LONG_SHORT_DATA = "not available for this market (has_long_short_ratio_data=False)"
_NO_OHLCV_DATA = "not available for this market (has_ohlcv_data=False)"
_NO_BUY_SELL_DATA = "not available for this market (has_buy_sell_data=False)"
_EMPTY_RESPONSE = "Coinalyze returned no data for this period"
_ZERO_DIVISOR = "cannot compute a ratio: the divisor for this period was 0"


@dataclass(frozen=True)
class SymbolMetrics:
    """One symbol's four measured metrics for one closed period."""

    rank: int
    ccxt_symbol: str
    coinalyze_symbol: str
    long_short_ratio: ClosedPeriodMetric
    funding_rate: ClosedPeriodMetric
    oi_change_pct: ClosedPeriodMetric
    buy_sell_ratio: ClosedPeriodMetric


@dataclass(frozen=True)
class MetricSummary:
    name: str
    n: int
    min: float | None
    p25: float | None
    median: float | None
    p75: float | None
    max: float | None
    unavailable_count: int


@dataclass(frozen=True)
class DistributionsMeasurement:
    generated_at: dt.datetime
    period: ClosedPeriod
    source: str
    snapshot_id: str | None
    rows: list[SymbolMetrics]
    summaries: list[MetricSummary]
    skipped_symbols: list[str]
    elapsed_seconds: float


def _find_bucket(history_response: list[dict], period_start_epoch: int) -> dict | None:
    if not history_response:
        return None
    for bucket in history_response[0].get("history", []):
        if bucket["t"] == period_start_epoch:
            return bucket
    return None


def _long_short_ratio(
    client: CoinalyzeClient,
    symbol: str,
    has_data: bool,
    interval: str,
    from_ts: int,
    to_ts: int,
    period,
) -> ClosedPeriodMetric:
    if not has_data:
        return ClosedPeriodMetric.unavailable(NO_DATA, "ratio", _NO_LONG_SHORT_DATA)
    history = client.long_short_ratio_history(symbol, interval, from_ts, to_ts)
    bucket = _find_bucket(history, from_ts)
    if bucket is None:
        return ClosedPeriodMetric.unavailable(NO_DATA, "ratio", _EMPTY_RESPONSE)
    return ClosedPeriodMetric(
        status=OK,
        value=bucket["r"],
        unit="ratio",
        period_start=period.start,
        period_close=period.close,
    )


def _funding_rate(
    client: CoinalyzeClient, symbol: str, interval: str, from_ts: int, to_ts: int, period
) -> ClosedPeriodMetric:
    history = client.funding_rate_history(symbol, interval, from_ts, to_ts)
    bucket = _find_bucket(history, from_ts)
    if bucket is None:
        return ClosedPeriodMetric.unavailable(NO_DATA, "%", _EMPTY_RESPONSE)
    return ClosedPeriodMetric(
        status=OK, value=bucket["c"], unit="%", period_start=period.start, period_close=period.close
    )


def _oi_change_pct(
    client: CoinalyzeClient, symbol: str, interval: str, from_ts: int, to_ts: int, period
) -> ClosedPeriodMetric:
    history = client.open_interest_history(symbol, interval, from_ts, to_ts, convert_to_usd=True)
    bucket = _find_bucket(history, from_ts)
    if bucket is None:
        return ClosedPeriodMetric.unavailable(NO_DATA, "%", _EMPTY_RESPONSE)
    if bucket["o"] == 0:
        return ClosedPeriodMetric.unavailable(NO_DATA, "%", _ZERO_DIVISOR)
    change_pct = (bucket["c"] - bucket["o"]) / bucket["o"] * 100
    return ClosedPeriodMetric(
        status=OK, value=change_pct, unit="%", period_start=period.start, period_close=period.close
    )


def _buy_sell_ratio(
    client: CoinalyzeClient,
    symbol: str,
    has_ohlcv: bool,
    has_buy_sell: bool,
    interval: str,
    from_ts: int,
    to_ts: int,
    period,
) -> ClosedPeriodMetric:
    if not has_ohlcv:
        return ClosedPeriodMetric.unavailable(NO_DATA, "ratio", _NO_OHLCV_DATA)
    if not has_buy_sell:
        return ClosedPeriodMetric.unavailable(NO_DATA, "ratio", _NO_BUY_SELL_DATA)
    history = client.ohlcv_history(symbol, interval, from_ts, to_ts)
    bucket = _find_bucket(history, from_ts)
    if bucket is None:
        return ClosedPeriodMetric.unavailable(NO_DATA, "ratio", _EMPTY_RESPONSE)
    buy = bucket["bv"]
    sell = bucket["v"] - buy
    if sell == 0:
        return ClosedPeriodMetric.unavailable(NO_DATA, "ratio", _ZERO_DIVISOR)
    return ClosedPeriodMetric(
        status=OK,
        value=buy / sell,
        unit="ratio",
        period_start=period.start,
        period_close=period.close,
    )


def _fetch_symbol_metrics(
    client: CoinalyzeClient,
    cache: FutureMarketsCache,
    rank: int,
    ccxt_symbol: str,
    venue: str,
    interval: str,
    from_ts: int,
    to_ts: int,
    period,
) -> SymbolMetrics:
    coinalyze_symbol = to_coinalyze_symbol(ccxt_symbol, venue)
    info = cache.lookup(coinalyze_symbol)
    if info is None:
        reason = f"{coinalyze_symbol!r} is not listed in Coinalyze's /future-markets"
        unavailable = ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "-", reason)
        return SymbolMetrics(
            rank=rank,
            ccxt_symbol=ccxt_symbol,
            coinalyze_symbol=coinalyze_symbol,
            long_short_ratio=unavailable,
            funding_rate=unavailable,
            oi_change_pct=unavailable,
            buy_sell_ratio=unavailable,
        )

    return SymbolMetrics(
        rank=rank,
        ccxt_symbol=ccxt_symbol,
        coinalyze_symbol=coinalyze_symbol,
        long_short_ratio=_long_short_ratio(
            client,
            coinalyze_symbol,
            info.has_long_short_ratio_data,
            interval,
            from_ts,
            to_ts,
            period,
        ),
        funding_rate=_funding_rate(client, coinalyze_symbol, interval, from_ts, to_ts, period),
        oi_change_pct=_oi_change_pct(client, coinalyze_symbol, interval, from_ts, to_ts, period),
        buy_sell_ratio=_buy_sell_ratio(
            client,
            coinalyze_symbol,
            info.has_ohlcv_data,
            info.has_buy_sell_data,
            interval,
            from_ts,
            to_ts,
            period,
        ),
    )


def _wait_for_budget(
    client: CoinalyzeClient, needed: int, sleep_fn: Callable[[float], None]
) -> None:
    while client.calls_in_last_minute + needed > RATE_LIMIT_PER_MINUTE:
        sleep_fn(PACING_CHECK_INTERVAL_SECONDS)


def _percentile(sorted_values: list[float], q: float) -> float:
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _summarize(name: str, metrics: list[ClosedPeriodMetric]) -> MetricSummary:
    values = sorted(m.value for m in metrics if m.status == OK)
    unavailable_count = sum(1 for m in metrics if m.status != OK)
    if not values:
        return MetricSummary(
            name=name,
            n=0,
            min=None,
            p25=None,
            median=None,
            p75=None,
            max=None,
            unavailable_count=unavailable_count,
        )
    return MetricSummary(
        name=name,
        n=len(values),
        min=values[0],
        p25=_percentile(values, 0.25),
        median=_percentile(values, 0.5),
        p75=_percentile(values, 0.75),
        max=values[-1],
        unavailable_count=unavailable_count,
    )


def measure_distributions(
    client: CoinalyzeClient,
    cache: FutureMarketsCache,
    ccxt_symbols: list[str],
    venue: str,
    now: dt.datetime,
    source: str,
    snapshot_id: str | None,
    *,
    interval: str = INTERVAL,
    sleep_fn: Callable[[float], None] = time.sleep,
    wall_clock: Callable[[], float] = time.monotonic,
) -> DistributionsMeasurement:
    """Fetch and summarize the four measured metrics for every symbol in
    `ccxt_symbols`, in the given (deterministic, rank) order.

    Paces itself against `client.calls_in_last_minute` before each
    symbol's calls, on top of the client's own reactive 429 retry. If
    the retry budget is exhausted mid-run (`RateLimitedError`), the
    symbols not yet fetched are reported in `skipped_symbols` rather
    than raising - a partial measurement is still useful, and "no
    setups found" is not the failure mode here, incomplete data is, and
    it's reported plainly rather than crashing the whole run.
    """
    period = closed_period(now, interval)
    from_ts = int(period.start.timestamp())
    to_ts = int(period.close.timestamp()) - 1

    started = wall_clock()
    rows: list[SymbolMetrics] = []
    skipped: list[str] = []

    for index, ccxt_symbol in enumerate(ccxt_symbols):
        _wait_for_budget(client, CALL_BUDGET_PER_SYMBOL, sleep_fn)
        try:
            row = _fetch_symbol_metrics(
                client, cache, index + 1, ccxt_symbol, venue, interval, from_ts, to_ts, period
            )
        except RateLimitedError:
            skipped.extend(ccxt_symbols[index:])
            break
        rows.append(row)

    elapsed = wall_clock() - started

    summaries = [
        _summarize("long_short_ratio", [r.long_short_ratio for r in rows]),
        _summarize("funding_rate", [r.funding_rate for r in rows]),
        _summarize("oi_change_pct", [r.oi_change_pct for r in rows]),
        _summarize("buy_sell_ratio", [r.buy_sell_ratio for r in rows]),
    ]

    return DistributionsMeasurement(
        generated_at=now,
        period=period,
        source=source,
        snapshot_id=snapshot_id,
        rows=rows,
        summaries=summaries,
        skipped_symbols=skipped,
        elapsed_seconds=elapsed,
    )
