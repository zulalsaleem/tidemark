"""Orchestrates one symbol's `MarketIntelSnapshot`: symbol mapping,
future-markets validation, closed-period clamping, and the per-metric
calls, all composed here rather than in the CLI.

Every closed-period metric uses the same fixed 1-hour window
(`DEFAULT_INTERVAL`) - a deliberate simplification for this merge (see
docs/adr/0011): one interval keeps every metric's period directly
comparable, and 1 hour matches the hourly-briefing use case this layer
exists for. `convert_to_usd=true` is always passed where Coinalyze
supports it (open interest, its history, and liquidations); Coinalyze's
own USD-conversion methodology is undocumented - see the ADR.
"""

from __future__ import annotations

import datetime as dt

from tidemark.market_intel.clamping import closed_period
from tidemark.market_intel.client import CoinalyzeClient
from tidemark.market_intel.future_markets import FutureMarketInfo, FutureMarketsCache
from tidemark.market_intel.models import (
    MARKET_NOT_FOUND,
    NO_DATA,
    OK,
    ClosedPeriodMetric,
    LiquidationsMetric,
    LongShortRatioMetric,
    MarketIntelSnapshot,
    PointInTimeMetric,
)
from tidemark.market_intel.symbols import to_coinalyze_symbol

DEFAULT_INTERVAL = "1hour"

_VOLUME_UNIT_LABELS = {
    "BASE_ASSET": "base asset units",
    "QUOTE_ASSET": "quote asset units",
    "CONTRACTS": "contracts",
}

_NO_LONG_SHORT_DATA = "not available for this market (has_long_short_ratio_data=False)"
_NO_OHLCV_DATA = "not available for this market (has_ohlcv_data=False)"
_NO_BUY_SELL_DATA = "not available for this market (has_buy_sell_data=False)"
_EMPTY_RESPONSE = "Coinalyze returned no data for this symbol"
_ZERO_OPEN = "cannot compute a percentage change: the period's opening value was 0"


def fetch_market_intel(
    client: CoinalyzeClient,
    cache: FutureMarketsCache,
    ccxt_symbol: str,
    venue: str,
    now: dt.datetime,
    interval: str = DEFAULT_INTERVAL,
) -> MarketIntelSnapshot:
    coinalyze_symbol = to_coinalyze_symbol(ccxt_symbol, venue)
    info = cache.lookup(coinalyze_symbol)
    if info is None:
        return _not_found_snapshot(ccxt_symbol, coinalyze_symbol, now)

    period = closed_period(now, interval)
    from_ts = int(period.start.timestamp())
    to_ts = int(period.close.timestamp()) - 1  # exclude the in-progress next bucket

    open_interest = _point_in_time_metric(
        client.open_interest([coinalyze_symbol], convert_to_usd=True), coinalyze_symbol, "USD"
    )
    funding_rate = _point_in_time_metric(
        client.funding_rate([coinalyze_symbol]), coinalyze_symbol, "%"
    )
    predicted_funding_rate = _point_in_time_metric(
        client.predicted_funding_rate([coinalyze_symbol]), coinalyze_symbol, "%"
    )

    oi_history = client.open_interest_history(
        coinalyze_symbol, interval, from_ts, to_ts, convert_to_usd=True
    )
    open_interest_change = _oi_change_metric(oi_history, period)
    open_interest_change_pct = _oi_change_pct_metric(oi_history, period)

    if info.has_long_short_ratio_data:
        ls_history = client.long_short_ratio_history(coinalyze_symbol, interval, from_ts, to_ts)
        long_short_ratio = _long_short_ratio_metric(ls_history, period)
    else:
        long_short_ratio = LongShortRatioMetric.unavailable(NO_DATA, _NO_LONG_SHORT_DATA)

    liq_history = client.liquidation_history(
        coinalyze_symbol, interval, from_ts, to_ts, convert_to_usd=True
    )
    liquidations = _liquidations_metric(liq_history, period)

    futures_volume, buy_volume, sell_volume, price_change = _volume_metrics(
        client, info, interval, from_ts, to_ts, period
    )

    return MarketIntelSnapshot(
        symbol=ccxt_symbol,
        coinalyze_symbol=coinalyze_symbol,
        market_status=OK,
        generated_at=now,
        open_interest=open_interest,
        open_interest_change=open_interest_change,
        funding_rate=funding_rate,
        predicted_funding_rate=predicted_funding_rate,
        long_short_ratio=long_short_ratio,
        liquidations=liquidations,
        futures_volume=futures_volume,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        price_change=price_change,
        open_interest_change_pct=open_interest_change_pct,
    )


def _not_found_snapshot(
    ccxt_symbol: str, coinalyze_symbol: str, now: dt.datetime
) -> MarketIntelSnapshot:
    reason = f"{coinalyze_symbol!r} is not listed in Coinalyze's /future-markets"
    return MarketIntelSnapshot(
        symbol=ccxt_symbol,
        coinalyze_symbol=coinalyze_symbol,
        market_status=MARKET_NOT_FOUND,
        generated_at=now,
        open_interest=PointInTimeMetric.unavailable(MARKET_NOT_FOUND, "USD", reason),
        open_interest_change=ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "USD", reason),
        funding_rate=PointInTimeMetric.unavailable(MARKET_NOT_FOUND, "%", reason),
        predicted_funding_rate=PointInTimeMetric.unavailable(MARKET_NOT_FOUND, "%", reason),
        long_short_ratio=LongShortRatioMetric.unavailable(MARKET_NOT_FOUND, reason),
        liquidations=LiquidationsMetric.unavailable(MARKET_NOT_FOUND, reason),
        futures_volume=ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "-", reason),
        buy_volume=ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "-", reason),
        sell_volume=ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "-", reason),
        price_change=ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "%", reason),
        open_interest_change_pct=ClosedPeriodMetric.unavailable(MARKET_NOT_FOUND, "%", reason),
    )


def _point_in_time_metric(rows: list[dict], symbol: str, unit: str) -> PointInTimeMetric:
    for row in rows:
        if row.get("symbol") == symbol:
            return PointInTimeMetric(
                status=OK,
                value=row["value"],
                unit=unit,
                updated_at=dt.datetime.fromtimestamp(row["update"] / 1000, tz=dt.UTC),
            )
    return PointInTimeMetric.unavailable(NO_DATA, unit, _EMPTY_RESPONSE)


def _find_bucket(history_response: list[dict], period_start_epoch: int) -> dict | None:
    if not history_response:
        return None
    buckets = history_response[0].get("history", [])
    for bucket in buckets:
        if bucket["t"] == period_start_epoch:
            return bucket
    return None


def _oi_change_metric(history_response: list[dict], period) -> ClosedPeriodMetric:
    bucket = _find_bucket(history_response, int(period.start.timestamp()))
    if bucket is None:
        return ClosedPeriodMetric.unavailable(NO_DATA, "USD", _EMPTY_RESPONSE)
    return ClosedPeriodMetric(
        status=OK,
        value=bucket["c"] - bucket["o"],
        unit="USD",
        period_start=period.start,
        period_close=period.close,
    )


def _oi_change_pct_metric(history_response: list[dict], period) -> ClosedPeriodMetric:
    """Same bucket `_oi_change_metric` already reads - just the
    percentage instead of the absolute USD difference. No extra
    Coinalyze call; see `position_flow_classifier.py`.
    """
    bucket = _find_bucket(history_response, int(period.start.timestamp()))
    if bucket is None:
        return ClosedPeriodMetric.unavailable(NO_DATA, "%", _EMPTY_RESPONSE)
    if bucket["o"] == 0:
        return ClosedPeriodMetric.unavailable(NO_DATA, "%", _ZERO_OPEN)
    change_pct = (bucket["c"] - bucket["o"]) / bucket["o"] * 100
    return ClosedPeriodMetric(
        status=OK, value=change_pct, unit="%", period_start=period.start, period_close=period.close
    )


def _long_short_ratio_metric(history_response: list[dict], period) -> LongShortRatioMetric:
    bucket = _find_bucket(history_response, int(period.start.timestamp()))
    if bucket is None:
        return LongShortRatioMetric.unavailable(NO_DATA, _EMPTY_RESPONSE)
    return LongShortRatioMetric(
        status=OK,
        ratio=bucket["r"],
        long_pct=bucket["l"],
        short_pct=bucket["s"],
        percent_unit="%",
        period_start=period.start,
        period_close=period.close,
    )


def _liquidations_metric(history_response: list[dict], period) -> LiquidationsMetric:
    bucket = _find_bucket(history_response, int(period.start.timestamp()))
    if bucket is None:
        return LiquidationsMetric.unavailable(NO_DATA, _EMPTY_RESPONSE)
    return LiquidationsMetric(
        status=OK,
        long_usd=bucket["l"],
        short_usd=bucket["s"],
        unit="USD",
        period_start=period.start,
        period_close=period.close,
    )


def _price_change_metric(bucket: dict, period) -> ClosedPeriodMetric:
    """Same OHLCV bucket the volume metrics already read - just the
    open/close percentage change instead of volume. No extra Coinalyze
    call; see `position_flow_classifier.py`.
    """
    if bucket["o"] == 0:
        return ClosedPeriodMetric.unavailable(NO_DATA, "%", _ZERO_OPEN)
    change_pct = (bucket["c"] - bucket["o"]) / bucket["o"] * 100
    return ClosedPeriodMetric(
        status=OK, value=change_pct, unit="%", period_start=period.start, period_close=period.close
    )


def _volume_metrics(
    client: CoinalyzeClient,
    info: FutureMarketInfo,
    interval: str,
    from_ts: int,
    to_ts: int,
    period,
) -> tuple[ClosedPeriodMetric, ClosedPeriodMetric, ClosedPeriodMetric, ClosedPeriodMetric]:
    unit = _VOLUME_UNIT_LABELS.get(info.oi_lq_vol_denominated_in, info.oi_lq_vol_denominated_in)

    if not info.has_ohlcv_data:
        unavailable = ClosedPeriodMetric.unavailable(NO_DATA, unit, _NO_OHLCV_DATA)
        price_change = ClosedPeriodMetric.unavailable(NO_DATA, "%", _NO_OHLCV_DATA)
        return unavailable, unavailable, unavailable, price_change

    history_response = client.ohlcv_history(info.symbol, interval, from_ts, to_ts)
    bucket = _find_bucket(history_response, int(period.start.timestamp()))
    if bucket is None:
        unavailable = ClosedPeriodMetric.unavailable(NO_DATA, unit, _EMPTY_RESPONSE)
        price_change = ClosedPeriodMetric.unavailable(NO_DATA, "%", _EMPTY_RESPONSE)
        return unavailable, unavailable, unavailable, price_change

    price_change = _price_change_metric(bucket, period)

    futures_volume = ClosedPeriodMetric(
        status=OK,
        value=bucket["v"],
        unit=unit,
        period_start=period.start,
        period_close=period.close,
    )

    if not info.has_buy_sell_data:
        no_split = ClosedPeriodMetric.unavailable(NO_DATA, unit, _NO_BUY_SELL_DATA)
        return futures_volume, no_split, no_split, price_change

    buy_volume = ClosedPeriodMetric(
        status=OK,
        value=bucket["bv"],
        unit=unit,
        period_start=period.start,
        period_close=period.close,
    )
    sell_volume = ClosedPeriodMetric(
        status=OK,
        value=bucket["v"] - bucket["bv"],
        unit=unit,
        period_start=period.start,
        period_close=period.close,
    )
    return futures_volume, buy_volume, sell_volume, price_change
